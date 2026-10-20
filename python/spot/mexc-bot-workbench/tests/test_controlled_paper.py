from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import sqlite3
import unittest

from controlled_paper import MarketEvent, PaperAccount, RiskSettings


NOW = 1_800_000_000_000


def event(now=NOW, symbol='BTCUSDT', price='100', atr='1', entry=True, exit=False, candle=None):
    return MarketEvent(symbol=symbol, price=D(price), atr=D(atr), entry=entry, exit=exit,
                       observed_ms=now, candle_ms=candle or (now // 900_000 * 900_000 - 900_000))


class ControlledPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def account(self, settings=None):
        return PaperAccount(self.folder, settings)

    def active(self, settings=None):
        account = self.account(settings)
        account.resume(NOW)
        return account

    def test_starts_paused_with_hard_caps_and_no_account_access(self):
        status = self.account().status(NOW)
        self.assertEqual(status['mode'], 'paper')
        self.assertFalse(status['live'])
        self.assertFalse(status['account_access'])
        self.assertEqual(D(status['equity']), D('50'))
        self.assertTrue(status['paused'])
        self.assertEqual(status['hard_caps'], {'capital': '50', 'position': '10', 'floor': '25'})

    def test_limits_cannot_be_raised_or_disabled_in_settings(self):
        for kwargs in ({'initial_cash': D('51')}, {'position_cap': D('11')},
                       {'capital_cap': D('51')}, {'floor': D('24')},
                       {'daily_loss_pct': D('11')}, {'risk_pct': D('0')},
                       {'risk_pct': D('NaN')}, {'risk_pct': D('1.1')},
                       {'max_positions': 0}, {'fee_bps': D('-1')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.account(replace(RiskSettings(), **kwargs))
        self.assertFalse((self.folder / 'controlled-paper.sqlite3').exists())

    def test_atr_sizing_accounts_for_costs_and_limits_each_position(self):
        account = self.active()
        result = account.process(event(), NOW)
        self.assertEqual(result['action'], 'BUY')
        position = account.status(NOW)['positions'][0]
        self.assertLessEqual(D(position['allocated']), D('10'))
        self.assertLessEqual(D(position['planned_loss']), D('0.25'))
        self.assertLess(D(account.status(NOW)['equity']), D('50'))

    def test_higher_atr_reduces_quantity(self):
        account = self.active()
        account.process(event(atr='1'), NOW)
        account.process(event(symbol='ETHUSDT', atr='10'), NOW)
        positions = {p['symbol']: p for p in account.status(NOW)['positions']}
        self.assertGreater(D(positions['BTCUSDT']['quantity']), D(positions['ETHUSDT']['quantity']))

    def test_same_asset_cannot_stack_positions_from_repeated_events(self):
        account = self.active()
        account.process(event(), NOW)
        result = account.process(event(NOW + 900_000), NOW + 900_000)
        self.assertEqual(result['action'], 'HOLD')
        self.assertEqual(len(account.status(NOW)['positions']), 1)
        self.assertEqual(account.status(NOW)['fill_count'], 1)

    def test_position_count_and_capital_limits_are_shared(self):
        account = self.active(replace(RiskSettings(), max_positions=1, capital_cap=D('7')))
        account.process(event(), NOW)
        first = account.status(NOW)['positions'][0]
        self.assertLessEqual(D(first['allocated']), D('7'))
        self.assertEqual(account.process(event(symbol='ETHUSDT'), NOW)['action'], 'POSITION_LIMIT')

    def test_daily_breach_blocks_exits_and_survives_next_day_until_review(self):
        account = self.active()
        account.process(event(), NOW)
        breached = account.process(event(NOW + 60_000, price='1', entry=False, exit=True), NOW + 60_000)
        self.assertEqual(breached['action'], 'HALTED')
        self.assertIn('DAILY_LOSS', breached['locks'])
        tomorrow = NOW + 86_400_000
        account = self.account()
        account.process(event(tomorrow, price='1'), tomorrow)
        with self.assertRaises(ValueError):
            account.resume(tomorrow)
        with self.assertRaises(ValueError):
            account.rearm('REARM', tomorrow)
        account.review('Checked losses, data and risk settings.', tomorrow)
        account.rearm('REARM', tomorrow)
        self.assertFalse(account.status(tomorrow)['paused'])
        self.assertEqual(account.status(tomorrow)['locks'], [])

    def test_daily_review_does_not_allow_same_day_rearm(self):
        account = self.active()
        account.process(event(), NOW)
        account.process(event(NOW + 60_000, price='1'), NOW + 60_000)
        account.review('Reviewed the loss and current exposure.', NOW + 60_000)
        with self.assertRaises(ValueError):
            account.rearm('REARM', NOW + 60_000)

    def test_floor_lock_cannot_be_cleared_while_equity_is_below_25(self):
        account = self.active(replace(RiskSettings(), initial_cash=D('30')))
        account.process(event(), NOW)
        account.process(event(NOW + 60_000, price='1'), NOW + 60_000)
        self.assertIn('ACCOUNT_FLOOR', account.status(NOW + 60_000)['locks'])
        tomorrow = NOW + 86_400_000
        account.process(event(tomorrow, price='1'), tomorrow)
        account.review('Reviewed floor breach and current equity.', tomorrow)
        with self.assertRaises(ValueError):
            account.rearm('REARM', tomorrow)

    def test_kill_requires_new_review_and_explicit_confirmation_after_restart(self):
        account = self.active()
        account.review('Reviewed settings before the later kill.', NOW)
        account.kill(NOW + 1)
        account = self.account()
        with self.assertRaises(ValueError):
            account.resume(NOW + 2)
        with self.assertRaises(ValueError):
            account.rearm('REARM', NOW + 2)
        account.review('Reviewed kill cause and current settings.', NOW + 3)
        with self.assertRaises(ValueError):
            account.rearm('yes', NOW + 3)
        account.rearm('REARM', NOW + 3)
        self.assertEqual(account.status(NOW + 3)['locks'], [])

    def test_stop_is_checked_on_new_prices_even_during_same_candle(self):
        account = self.active()
        account.process(event(), NOW)
        result = account.process(event(NOW + 60_000, price='97', entry=False), NOW + 60_000)
        self.assertEqual(result['action'], 'SELL')
        self.assertEqual(result['reason'], 'STOP')
        self.assertEqual(account.status(NOW + 60_000)['positions'], [])
        self.assertLess(D(account.status(NOW + 60_000)['equity']), D('50'))

    def test_stale_and_unapproved_source_events_do_not_change_state(self):
        account = self.active()
        before = account.status(NOW)
        for bad in (event(NOW - 600_000), replace(event(), source='tradingview'),
                    replace(event(), symbol='BTCUSD'), replace(event(), price=D('NaN'))):
            with self.subTest(event=bad), self.assertRaises(ValueError):
                account.process(bad, NOW)
        self.assertEqual(account.status(NOW), before)

    def test_restart_does_not_replay_entry_or_reset_seed(self):
        account = self.active()
        account.process(event(), NOW)
        self.assertEqual(self.account().process(event(), NOW)['action'], 'ALREADY_PROCESSED')
        self.assertEqual(self.account().status(NOW)['fill_count'], 1)
        with self.assertRaises(ValueError):
            self.account(replace(RiskSettings(), initial_cash=D('40')))

    def test_paused_events_are_not_replayed_after_resume(self):
        account = self.account()
        self.assertEqual(account.process(event(), NOW)['action'], 'PAUSED')
        account.resume(NOW)
        self.assertEqual(account.process(event(), NOW)['action'], 'ALREADY_PROCESSED')

    def test_minimum_notional_never_causes_rounding_above_position_cap(self):
        account = self.active(replace(RiskSettings(), min_notional=D('10')))
        self.assertEqual(account.process(event(), NOW)['action'], 'BELOW_MINIMUM')
        self.assertEqual(account.status(NOW)['fill_count'], 0)

    def test_concurrent_workers_cannot_duplicate_a_fill(self):
        left = self.active()
        right = self.account()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda item: item.process(event(), NOW), [left,right]))
        self.assertEqual(sorted(row['action'] for row in results), ['ALREADY_PROCESSED','BUY'])
        self.assertEqual(left.status(NOW)['fill_count'], 1)

    def test_failed_commit_rolls_back_account_fill_and_candle_claim(self):
        account = self.active()
        before = account.status(NOW)
        with account.transaction() as db:
            db.execute("CREATE TRIGGER interrupt BEFORE UPDATE ON account BEGIN SELECT RAISE(ABORT,'test interruption'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            account.process(event(), NOW)
        self.assertEqual(account.status(NOW), before)
        with account.transaction() as db:
            db.execute('DROP TRIGGER interrupt')
        self.assertEqual(account.process(event(), NOW)['action'], 'BUY')

    def test_stale_other_position_blocks_new_allocations(self):
        account = self.active()
        account.process(event(), NOW)
        later = NOW + 240_000
        result = account.process(event(later, symbol='ETHUSDT'), later)
        self.assertEqual(result['action'], 'STALE_VALUATION')
        self.assertEqual(account.status(later)['fill_count'], 1)

    def test_gap_at_midnight_is_counted_instead_of_erased_by_day_reset(self):
        late = int(datetime.fromtimestamp(NOW/1000,timezone.utc).replace(hour=23,minute=59,second=0).timestamp()*1000)
        account = self.active()
        account.process(event(late), late)
        morning = late + 120_000
        result = account.process(event(morning, price='1'), morning)
        self.assertIn('DAILY_LOSS', result['locks'])

    def test_backwards_clock_cannot_reset_day_or_replay_signals(self):
        account = self.active()
        account.process(event(), NOW)
        with self.assertRaises(ValueError):
            account.process(event(NOW-60_000), NOW-60_000)


if __name__ == '__main__':
    unittest.main()
