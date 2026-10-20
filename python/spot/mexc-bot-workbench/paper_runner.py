"""Continuous, credential-free BTCUSDT simulation. No exchange orders are sent."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time

from mexc_bot import Config, MexcClient
from monitor import build_snapshot


D = Decimal


@dataclass(frozen=True)
class PaperConfig:
    initial_cash: Decimal = D('1000')
    position_pct: Decimal = D('5')
    daily_loss_pct: Decimal = D('2')
    min_notional: Decimal = D('10')
    fee_bps: Decimal = D('10')
    slippage_bps: Decimal = D('5')
    poll_seconds: int = 60

    def validate(self):
        for name in ('initial_cash', 'position_pct', 'daily_loss_pct', 'min_notional'):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f'Invalid paper setting: {name}')
        if self.position_pct > 100 or self.daily_loss_pct > 100:
            raise ValueError('Paper percentage must be at most 100')
        for name in ('fee_bps', 'slippage_bps'):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value < 1000:
                raise ValueError(f'Invalid paper setting: {name}')
        if type(self.poll_seconds) is not int or not 1 <= self.poll_seconds <= 300:
            raise ValueError('Paper poll interval must be 1 to 300 seconds')

    @classmethod
    def from_env(cls):
        return cls(**{
            name: D(os.environ.get('PAPER_' + name.upper(), str(default)))
            for name, default in vars(cls()).items() if name != 'poll_seconds'
        }, poll_seconds=int(os.environ.get('PAPER_POLL_SECONDS', '60')))


def public_client():
    # Never inherit exchange credentials, host overrides or live flags.
    return MexcClient(Config(api_key='', api_secret='', live_trading=False,
                             base_url='https://api.mexc.com', symbol='BTCUSDT',
                             quote_asset='USDT', entry_window=20, exit_window=10))


class PaperRunner:
    def __init__(self, folder: Path, config: PaperConfig, client):
        config.validate()
        self.cfg, self.client = config, client
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / 'paper.sqlite3'
        with closing(self.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY CHECK(id=1), initial_cash TEXT NOT NULL,
                    cash TEXT NOT NULL, base_qty TEXT NOT NULL, price TEXT NOT NULL,
                    risk_day TEXT NOT NULL, day_equity TEXT NOT NULL,
                    daily_halted INTEGER NOT NULL, last_candle INTEGER NOT NULL,
                    last_success_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY, candle INTEGER UNIQUE NOT NULL,
                    side TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL,
                    fee TEXT NOT NULL, observed_at TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO portfolio VALUES (1,?,?,?,?,?,?,0,0,0)',
                       (str(config.initial_cash), str(config.initial_cash), '0', '0', '',
                        str(config.initial_cash)))
            row = db.execute('SELECT initial_cash FROM portfolio WHERE id=1').fetchone()
            if D(row['initial_cash']) != config.initial_cash:
                raise ValueError('Existing paper seed cannot be changed; use a separate data directory')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def state(self):
        with closing(self.connect()) as db:
            row = dict(db.execute('SELECT * FROM portfolio WHERE id=1').fetchone())
            row['fill_count'] = db.execute('SELECT COUNT(*) FROM fills').fetchone()[0]
        row['equity'] = str(D(row['cash']) + D(row['base_qty']) * D(row['price']))
        row['daily_halted'] = bool(row['daily_halted'])
        return row

    def cycle(self, now_ms: int):
        # Validate the complete market snapshot before touching persistent state.
        snapshot = build_snapshot(self.client, now_ms)
        price, candle = D(str(snapshot['price'])), snapshot['last_closed_candle_ms']
        day = datetime.fromtimestamp(now_ms / 1000, timezone.utc).date().isoformat()
        db = self.connect()
        try:
            # One transaction covers candle claim, fill and cash/position update.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM portfolio WHERE id=1').fetchone()
            cash, quantity = D(row['cash']), D(row['base_qty'])
            if candle <= row['last_candle']:
                db.rollback()
                return self.result('ALREADY_PROCESSED', row['daily_halted'])
            equity = cash + quantity * price
            baseline = D(row['day_equity'])
            halted = bool(row['daily_halted'])
            if day != row['risk_day']:
                baseline, halted = equity, False
            if baseline <= 0 or (baseline - equity) * 100 >= baseline * self.cfg.daily_loss_pct:
                halted = True
            action = snapshot['signals']['with_position' if quantity > 0 else 'without_position']
            fill = None
            if action == 'BUY':
                if halted:
                    action = 'DAILY_LIMIT'
                else:
                    execution_price = price * (1 + self.cfg.slippage_bps / 10000)
                    # Cap total debit, including estimated fees, at the allocation.
                    allocation = min(cash, D(row['initial_cash']) * self.cfg.position_pct / 100)
                    bought = (allocation / (execution_price * (1 + self.cfg.fee_bps / 10000))).quantize(
                        D('0.00000001'), rounding=ROUND_DOWN)
                    notional = bought * execution_price
                    if bought <= 0 or notional < self.cfg.min_notional:
                        action = 'BELOW_MINIMUM'
                    else:
                        fee = notional * self.cfg.fee_bps / 10000
                        cash -= notional + fee
                        quantity += bought
                        fill = (bought, execution_price, fee)
            elif action == 'SELL':
                execution_price = price * (1 - self.cfg.slippage_bps / 10000)
                notional = quantity * execution_price
                fee = notional * self.cfg.fee_bps / 10000
                cash += notional - fee
                fill = (quantity, execution_price, fee)
                quantity = D('0')
            # Fee/slippage losses on entry count toward the same day's halt.
            equity = cash + quantity * price
            if baseline <= 0 or (baseline - equity) * 100 >= baseline * self.cfg.daily_loss_pct:
                halted = True
            if cash < 0 or quantity < 0:
                raise ValueError('Invalid paper accounting')
            if fill:
                db.execute('INSERT INTO fills(candle,side,quantity,price,fee,observed_at) VALUES (?,?,?,?,?,?)',
                           (candle, action, *(str(x) for x in fill), snapshot['observed_at']))
            db.execute('''UPDATE portfolio SET cash=?,base_qty=?,price=?,risk_day=?,day_equity=?,
                          daily_halted=?,last_candle=?,last_success_ms=? WHERE id=1''',
                       (str(cash), str(quantity), str(price), day, str(baseline), int(halted), candle, now_ms))
            db.commit()
            return self.result(action, halted)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def result(action, halted):
        return {'mode': 'paper', 'symbol': 'BTCUSDT', 'live': False, 'account_access': False,
                'action': action, 'daily_halted': bool(halted)}


def safe_error(exc):
    result = {'mode': 'paper', 'live': False, 'error_type': type(exc).__name__}
    response = getattr(exc, 'response', None)
    if response is not None:
        result['http_status'] = response.status_code
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('PAPER_DATA_DIR', 'paper-data')))
    parser.add_argument('--once', action='store_true', help='Run one simulated cycle and exit')
    args = parser.parse_args(argv)
    client, server = None, None
    stop = threading.Event()
    try:
        config = PaperConfig.from_env()
        client = public_client()
        runner = PaperRunner(args.data_dir, config, client)
        if args.once:
            print(json.dumps(runner.cycle(int(time.time() * 1000))), flush=True)
            return 0

        health = {'last_success_ms': 0, 'failures': 0}
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/health':
                    self.send_error(404)
                    return
                with lock:
                    fields = dict(health)
                age = (int(time.time() * 1000) - fields['last_success_ms']) / 1000
                fresh = fields['last_success_ms'] > 0 and 0 <= age <= 180
                body = json.dumps({'mode': 'paper', 'live': False, 'account_access': False,
                                   'status': 'ok' if fresh else 'stale',
                                   'data_age_seconds': round(age, 1) if fields['last_success_ms'] else None,
                                   **fields}).encode()
                self.send_response(200 if fresh else 503)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
        failures = 0
        while not stop.is_set():
            try:
                result = runner.cycle(int(time.time() * 1000))
                failures = 0
                with lock:
                    health.update(last_success_ms=int(time.time() * 1000), failures=0)
                print(json.dumps(result), flush=True)
            except Exception as exc:
                failures += 1
                with lock:
                    health['failures'] = failures
                print(json.dumps(safe_error(exc)), flush=True)
            delay = config.poll_seconds if not failures else min(300, 15 * 2 ** min(failures - 1, 5))
            stop.wait(delay)
        return 0
    except Exception as exc:
        print(json.dumps(safe_error(exc)), flush=True)
        return 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if client:
            client.session.close()


if __name__ == '__main__':
    raise SystemExit(main())
