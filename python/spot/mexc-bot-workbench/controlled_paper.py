"""Transactional paper portfolio and owner controls; no network or live execution."""
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import json
from pathlib import Path
import sqlite3


D = Decimal
HARD_CAPITAL, HARD_POSITION, HARD_FLOOR = D('50'), D('10'), D('25')
SYMBOLS = ('BTCUSDT', 'ETHUSDT')


class RuleError(ValueError):
    """A fixed, credential-free explanation that may be shown to the owner."""


def utc_day(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).date().isoformat()


@dataclass(frozen=True)
class RiskSettings:
    initial_cash: Decimal = D('50')
    capital_cap: Decimal = D('50')
    position_cap: Decimal = D('10')
    floor: Decimal = D('25')
    daily_loss_pct: Decimal = D('10')
    risk_pct: Decimal = D('0.5')
    max_positions: int = 2
    fee_bps: Decimal = D('10')
    slippage_bps: Decimal = D('5')
    min_notional: Decimal = D('1')
    quantity_step: Decimal = D('0.00000001')

    def validate(self):
        for field in fields(self):
            if field.name == 'max_positions':
                continue
            value = getattr(self, field.name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise RuleError('Risk settings must be finite decimal numbers')
            if value < 0 or (value == 0 and field.name not in ('fee_bps', 'slippage_bps')):
                raise RuleError('Risk settings must be positive')
        if not HARD_FLOOR <= self.floor <= self.initial_cash <= HARD_CAPITAL:
            raise RuleError('Paper equity must start between the account floor and 50')
        if self.capital_cap > HARD_CAPITAL or self.position_cap > HARD_POSITION:
            raise RuleError('Capital and position limits cannot exceed hard caps')
        if self.daily_loss_pct > 10 or self.risk_pct > 1:
            raise RuleError('Daily loss limit is at most 10%; per-trade risk is at most 1%')
        if type(self.max_positions) is not int or not 1 <= self.max_positions <= 2:
            raise RuleError('Maximum positions must be one or two')
        if self.fee_bps >= 1000 or self.slippage_bps >= 1000:
            raise RuleError('Invalid paper cost model')

    def export(self):
        return {key: value if type(value) is int else str(value) for key, value in asdict(self).items()}

    @classmethod
    def restore(cls, value):
        return cls(**{key: number if key == 'max_positions' else D(number) for key, number in value.items()})


@dataclass(frozen=True)
class MarketEvent:
    symbol: str
    price: Decimal
    atr: Decimal
    entry: bool
    exit: bool
    observed_ms: int
    candle_ms: int
    source: str = 'local_technical'
    strategy: str = 'technical-v1'

    def validate(self, now):
        if self.source != 'local_technical' or self.strategy != 'technical-v1':
            raise RuleError('External signals and optional strategies are disabled')
        if self.symbol not in SYMBOLS:
            raise RuleError('Only approved spot symbols are supported')
        if not all(isinstance(x, Decimal) and x.is_finite() for x in (self.price, self.atr)):
            raise RuleError('Invalid market numbers')
        if self.price <= 0 or self.atr < 0:
            raise RuleError('Invalid price or ATR')
        if type(self.entry) is not bool or type(self.exit) is not bool or (self.entry and self.exit):
            raise RuleError('Ambiguous technical signal')
        if type(self.observed_ms) is not int or type(self.candle_ms) is not int:
            raise RuleError('Invalid timestamps')
        if not 0 <= now - self.observed_ms <= 90_000:
            raise RuleError('Market observation is stale or in the future')
        if not 0 < self.candle_ms < self.observed_ms or now - self.candle_ms > 1_800_000:
            raise RuleError('Closed 15-minute candle is stale or invalid')

    @property
    def event_id(self):
        return f'{self.source}:{self.strategy}:{self.symbol}:{self.candle_ms}'


class PaperAccount:
    def __init__(self, folder: Path, settings=None):
        if settings is not None:
            settings.validate()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = folder / 'controlled-paper.sqlite3'
        with closing(self.connect()) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at_ms INTEGER NOT NULL, kind TEXT NOT NULL, note TEXT NOT NULL);
            ''')
        with self.transaction() as db:
            row = db.execute('SELECT payload FROM account WHERE id=1').fetchone()
            if row is None:
                config = settings or RiskSettings()
                config.validate()
                state = {'settings': config.export(), 'cash': str(config.initial_cash), 'positions': {},
                         'day': '', 'day_equity': str(config.initial_cash), 'paused': True, 'killed': False,
                         'daily_breach': '', 'floor_latched': False, 'control_version': 0,
                         'review_version': -1, 'review_note': '', 'last_observed_ms': 0}
                db.execute('INSERT INTO account VALUES (1,?)', (json.dumps(state),))
            else:
                state = json.loads(row['payload'])
                config = RiskSettings.restore(state['settings'])
                config.validate()
                if settings is not None and config != settings:
                    raise RuleError('Existing paper ledger settings are immutable; do not reset its history')
        self.cfg = config

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def transaction(self):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def load(db):
        return json.loads(db.execute('SELECT payload FROM account WHERE id=1').fetchone()['payload'])

    @staticmethod
    def save(db, state):
        db.execute('UPDATE account SET payload=? WHERE id=1', (json.dumps(state, allow_nan=False),))

    @staticmethod
    def equity(state):
        return D(state['cash']) + sum((D(p['quantity']) * D(p['mark']) for p in state['positions'].values()), D('0'))

    @staticmethod
    def locks(state):
        return [name for name, enabled in [('KILL_SWITCH', state['killed']),
                ('ACCOUNT_FLOOR', state['floor_latched']), ('DAILY_LOSS', bool(state['daily_breach']))] if enabled]

    @staticmethod
    def audit(db, now, kind, note=''):
        db.execute('INSERT INTO audit(at_ms,kind,note) VALUES (?,?,?)', (now, kind, note))

    def check_risk(self, db, state, now):
        equity, baseline = self.equity(state), D(state['day_equity'])
        triggers = []
        if equity < max(HARD_FLOOR, self.cfg.floor) and not state['floor_latched']:
            state['floor_latched'] = True
            triggers.append('ACCOUNT_FLOOR')
        if (baseline <= 0 or (baseline - equity) * 100 >= baseline * min(self.cfg.daily_loss_pct, D('10'))) and not state['daily_breach']:
            state['daily_breach'] = utc_day(now)
            triggers.append('DAILY_LOSS')
        if triggers:
            state['paused'] = True
            state['control_version'] += 1
            self.audit(db, now, 'RISK_HALT', ','.join(triggers))

    def status(self, now):
        with closing(self.connect()) as db:
            state = self.load(db)
            fill_count = db.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
        equity = self.equity(state)
        return {'mode': 'paper', 'live': False, 'account_access': False,
                'valuation_assumption': 'simulated USDT valued at USD parity; not a live USD risk model',
                'exchange_rules_verified': False, 'source': 'local_technical',
                'disabled_sources': ['tradingview', 'scanner', 'news', 'copy', 'tradovate', 'portal'],
                'paused': state['paused'], 'locks': self.locks(state),
                'cash': state['cash'], 'equity': str(equity),
                'positions': list(state['positions'].values()), 'fill_count': fill_count,
                'allocated_capital': str(sum((D(p['allocated']) for p in state['positions'].values()), D('0'))),
                'risk_day': state['day'], 'daily_pnl': str(equity - D(state['day_equity'])),
                'daily_limit_amount': str(D(state['day_equity']) * self.cfg.daily_loss_pct / 100),
                'distance_to_floor': str(equity - self.cfg.floor), 'daily_breach_day': state['daily_breach'],
                'review_current': state['review_version'] == state['control_version'],
                'valuation_fresh': all(0 <= now - p['mark_ms'] <= 180_000 for p in state['positions'].values()),
                'last_observed_ms': state['last_observed_ms'], 'settings': self.cfg.export(),
                'hard_caps': {'capital': '50', 'position': '10', 'floor': '25'}}

    def pause(self, now):
        with self.transaction() as db:
            state = self.load(db)
            state['paused'] = True
            self.audit(db, now, 'PAUSE')
            self.save(db, state)

    def resume(self, now):
        with self.transaction() as db:
            state = self.load(db)
            if self.locks(state):
                raise RuleError('Risk halt requires review and explicit re-arm, not Resume')
            state['paused'] = False
            self.audit(db, now, 'RESUME_PAPER')
            self.save(db, state)

    def kill(self, now):
        with self.transaction() as db:
            state = self.load(db)
            state.update(paused=True, killed=True, control_version=state['control_version'] + 1)
            self.audit(db, now, 'KILL')
            self.save(db, state)

    def review(self, note, now):
        if not isinstance(note, str) or not 12 <= len(note.strip()) <= 500:
            raise RuleError('Record a review note of 12 to 500 characters; never include credentials')
        with self.transaction() as db:
            state = self.load(db)
            state.update(review_note=note.strip(), review_version=state['control_version'])
            self.audit(db, now, 'MANUAL_REVIEW', note.strip())
            self.save(db, state)

    def rearm(self, confirmation, now):
        with self.transaction() as db:
            state = self.load(db)
            if confirmation != 'REARM' or state['review_version'] != state['control_version'] or not state['review_note']:
                raise RuleError('A current manual review and explicit REARM confirmation are required')
            if state['daily_breach'] and utc_day(now) <= state['daily_breach']:
                raise RuleError('Daily-loss re-arm must wait until a later UTC day')
            if self.equity(state) < max(HARD_FLOOR, self.cfg.floor):
                raise RuleError('Equity remains below the account floor')
            if any(not 0 <= now - p['mark_ms'] <= 180_000 for p in state['positions'].values()):
                raise RuleError('Refresh all open-position prices before re-arming')
            state.update(paused=False, killed=False, daily_breach='', floor_latched=False,
                         control_version=state['control_version'] + 1)
            self.audit(db, now, 'REARM_PAPER')
            self.save(db, state)

    def process(self, event: MarketEvent, now):
        event.validate(now)
        with self.transaction() as db:
            state = self.load(db)
            if event.observed_ms < state['last_observed_ms']:
                raise RuleError('Market observation moved backwards')
            day = utc_day(now)
            if day != state['day']:
                # Use last persisted marks so a gap over midnight is not erased.
                state.update(day=day, day_equity=str(self.equity(state)))
            position = state['positions'].get(event.symbol)
            if position:
                position.update(mark=str(event.price), mark_ms=event.observed_ms)
            state['last_observed_ms'] = event.observed_ms
            duplicate = db.execute('SELECT 1 FROM events WHERE id=?', (event.event_id,)).fetchone() is not None
            db.execute('INSERT OR IGNORE INTO events VALUES (?)', (event.event_id,))
            self.check_risk(db, state, now)
            action, reason = 'HOLD', ''
            if self.locks(state):
                action = 'HALTED'
            elif state['paused']:
                action = 'PAUSED'
            elif any(not 0 <= now - p['mark_ms'] <= 180_000 for p in state['positions'].values()):
                action = 'STALE_VALUATION'
            elif position and (event.price <= D(position['stop']) or event.price >= D(position['target']) or (event.exit and not duplicate)):
                reason = 'STOP' if event.price <= D(position['stop']) else 'TARGET' if event.price >= D(position['target']) else 'TECHNICAL_EXIT'
                quantity = D(position['quantity'])
                price = event.price * (1 - self.cfg.slippage_bps / 10000)
                notional = quantity * price
                fee = notional * self.cfg.fee_bps / 10000
                state['cash'] = str(D(state['cash']) + notional - fee)
                self.fill(db, f'exit:{event.event_id}:{event.observed_ms}', event, 'SELL', quantity, price, fee, reason)
                del state['positions'][event.symbol]
                action = 'SELL'
            elif duplicate:
                action = 'ALREADY_PROCESSED'
            elif event.entry and not position:
                action = self.buy(db, state, event)
            self.check_risk(db, state, now)
            if D(state['cash']) < 0:
                raise RuleError('Paper accounting would produce negative cash')
            self.save(db, state)
            return {'mode': 'paper', 'live': False, 'action': action, 'reason': reason,
                    'symbol': event.symbol, 'locks': self.locks(state)}

    def buy(self, db, state, event):
        if len(state['positions']) >= self.cfg.max_positions:
            return 'POSITION_LIMIT'
        if event.atr <= 0:
            return 'NO_ATR'
        buy_price = event.price * (1 + self.cfg.slippage_bps / 10000)
        stop, target = buy_price - 2 * event.atr, buy_price + 4 * event.atr
        if stop <= 0:
            return 'INVALID_STOP'
        stop_fill = stop * (1 - self.cfg.slippage_bps / 10000)
        loss_per_unit = buy_price - stop_fill + (buy_price + stop_fill) * self.cfg.fee_bps / 10000
        equity = self.equity(state)
        risk_budget = equity * self.cfg.risk_pct / 100
        committed = sum((D(p['allocated']) for p in state['positions'].values()), D('0'))
        allocation = min(D(state['cash']), HARD_POSITION, self.cfg.position_cap,
                         min(HARD_CAPITAL, self.cfg.capital_cap) - committed)
        if allocation <= 0:
            return 'CAPITAL_LIMIT'
        quantity = min(risk_budget / loss_per_unit, allocation / (buy_price * (1 + self.cfg.fee_bps / 10000)))
        quantity = (quantity / self.cfg.quantity_step).to_integral_value(rounding=ROUND_DOWN) * self.cfg.quantity_step
        notional = quantity * buy_price
        if quantity <= 0 or notional < self.cfg.min_notional:
            return 'BELOW_MINIMUM'
        fee, debit = notional * self.cfg.fee_bps / 10000, notional * (1 + self.cfg.fee_bps / 10000)
        if debit > min(HARD_POSITION, self.cfg.position_cap, allocation) or committed + debit > min(HARD_CAPITAL, self.cfg.capital_cap):
            raise RuleError('Hard allocation cap rejected the simulated fill')
        if equity - debit + quantity * event.price < max(HARD_FLOOR, self.cfg.floor):
            return 'FLOOR_BUFFER'
        state['cash'] = str(D(state['cash']) - debit)
        state['positions'][event.symbol] = {'symbol': event.symbol, 'quantity': str(quantity),
            'entry': str(buy_price), 'stop': str(stop), 'target': str(target),
            'allocated': str(debit), 'planned_loss': str(quantity * loss_per_unit),
            'mark': str(event.price), 'mark_ms': event.observed_ms}
        self.fill(db, event.event_id, event, 'BUY', quantity, buy_price, fee, 'TECHNICAL_ENTRY')
        return 'BUY'

    @staticmethod
    def fill(db, event_id, event, side, quantity, price, fee, reason):
        payload = {'source': event.source, 'strategy': event.strategy, 'symbol': event.symbol,
                   'side': side, 'quantity': str(quantity), 'price': str(price), 'fee': str(fee),
                   'reason': reason, 'source_time_ms': event.candle_ms,
                   'observed_ms': event.observed_ms, 'signal_age_ms': event.observed_ms - event.candle_ms}
        db.execute('INSERT INTO fills(event_id,payload) VALUES (?,?)', (event_id, json.dumps(payload)))
