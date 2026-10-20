"""Restart, accounting and market-data regression tests for the paper worker."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import os
import sqlite3
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from unittest.mock import patch

from paper_runner import PaperConfig, PaperRunner, public_client, safe_error
import paper_runner


NOW = 1_800_000_000_000


class Market:
    def __init__(self, now=NOW, close=110, price=110):
        self.now, self.close, self.current_price = now, close, price

    def klines(self, limit):
        rows = []
        for index in range(21):
            end = self.now - (21 - index) * 60_000
            close = self.close if index == 20 else 95
            rows.append([end - 60_000, 95, max(100, close), min(90, close), close, 1, end])
        return rows

    def price(self):
        return self.current_price

    def account(self):
        raise AssertionError('Paper worker must never read an account')

    def place_market_order(self, *args):
        raise AssertionError('Paper worker must never submit an order')


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.cfg = PaperConfig()

    def runner(self, market=None, config=None):
        return PaperRunner(self.folder, config or self.cfg, market or Market())

    def test_buy_is_simulated_fee_adjusted_and_persisted(self):
        result = self.runner().cycle(NOW)
        self.assertEqual(result['mode'], 'paper')
        self.assertFalse(result['live'])
        self.assertFalse(result['account_access'])
        self.assertEqual(result['action'], 'BUY')
        state = self.runner().state()
        self.assertGreater(Decimal(state['base_qty']), 0)
        self.assertGreater(Decimal(state['cash']), Decimal('949'))
        self.assertLess(Decimal(state['cash']), Decimal('950.01'))
        self.assertEqual(state['fill_count'], 1)
        self.assertLess(Decimal(state['equity']), Decimal('1000'))

    def test_restart_and_repeated_candle_cannot_duplicate_a_fill(self):
        self.runner().cycle(NOW)
        original = self.runner().state()
        for _ in range(3):
            self.assertEqual(self.runner().cycle(NOW)['action'], 'ALREADY_PROCESSED')
        after = self.runner().state()
        self.assertEqual(after['fill_count'], 1)
        self.assertEqual(after['cash'], original['cash'])
        self.assertEqual(after['base_qty'], original['base_qty'])

    def test_two_workers_cannot_fill_the_same_candle_twice(self):
        left, right = self.runner(), self.runner()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda worker: worker.cycle(NOW), [left, right]))
        self.assertEqual(sorted(row['action'] for row in results), ['ALREADY_PROCESSED', 'BUY'])
        self.assertEqual(left.state()['fill_count'], 1)

    def test_failed_accounting_commit_rolls_back_fill_and_allows_retry(self):
        runner = self.runner()
        before = runner.state()
        with runner.connect() as db:
            db.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON portfolio BEGIN SELECT RAISE(ABORT,'test interruption'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            runner.cycle(NOW)
        self.assertEqual(runner.state(), before)
        with runner.connect() as db:
            db.execute('DROP TRIGGER fail_update')
        self.assertEqual(runner.cycle(NOW)['action'], 'BUY')
        self.assertEqual(runner.state()['fill_count'], 1)

    def test_sell_uses_only_simulated_position_and_books_proceeds(self):
        self.runner().cycle(NOW)
        second = self.runner(Market(NOW + 60_000, close=80, price=80))
        self.assertEqual(second.cycle(NOW + 60_000)['action'], 'SELL')
        state = second.state()
        self.assertEqual(Decimal(state['base_qty']), 0)
        self.assertEqual(state['fill_count'], 2)
        self.assertLess(Decimal(state['cash']), Decimal('1000'))

    def test_daily_loss_halts_new_entries_across_restart_until_next_utc_day(self):
        self.runner().cycle(NOW)
        crashed = self.runner(Market(NOW + 60_000, close=1, price=1))
        result = crashed.cycle(NOW + 60_000)
        self.assertTrue(result['daily_halted'])
        self.assertEqual(result['action'], 'SELL')
        entry = self.runner(Market(NOW + 120_000))
        self.assertEqual(entry.cycle(NOW + 120_000)['action'], 'DAILY_LIMIT')
        self.assertTrue(entry.state()['daily_halted'])
        tomorrow = NOW + 86_400_000
        restarted = self.runner(Market(tomorrow))
        self.assertEqual(restarted.cycle(tomorrow)['action'], 'BUY')
        self.assertFalse(restarted.state()['daily_halted'])
        self.assertEqual(restarted.state()['risk_day'], datetime.fromtimestamp(tomorrow / 1000, timezone.utc).date().isoformat())

    def test_stale_market_data_does_not_change_the_ledger(self):
        runner = self.runner()
        before = runner.state()
        with self.assertRaises(ValueError):
            runner.cycle(NOW + 600_000)
        self.assertEqual(runner.state(), before)

    def test_backward_candle_cannot_replay_a_trade(self):
        self.runner().cycle(NOW)
        runner = self.runner(Market(NOW - 60_000))
        self.assertEqual(runner.cycle(NOW - 60_000)['action'], 'ALREADY_PROCESSED')
        self.assertEqual(runner.state()['fill_count'], 1)

    def test_reconfigured_initial_cash_cannot_reset_an_existing_portfolio(self):
        self.runner().cycle(NOW)
        with self.assertRaises(ValueError):
            self.runner(config=replace(self.cfg, initial_cash=Decimal('2000')))
        self.assertEqual(self.runner().state()['fill_count'], 1)

    def test_invalid_risk_settings_fail_before_state_creation(self):
        for kwargs in ({'initial_cash': Decimal('0')}, {'initial_cash': Decimal('NaN')},
                       {'position_pct': Decimal('101')}, {'daily_loss_pct': Decimal('-1')},
                       {'fee_bps': Decimal('-1')}, {'slippage_bps': Decimal('Infinity')},
                       {'poll_seconds': 0}, {'min_notional': Decimal('0')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.runner(config=replace(self.cfg, **kwargs))
        self.assertFalse((self.folder / 'paper.sqlite3').exists())

    def test_low_budget_cannot_create_negative_cash_or_a_tiny_fill(self):
        runner = self.runner(config=replace(self.cfg, initial_cash=Decimal('10')))
        self.assertEqual(runner.cycle(NOW)['action'], 'BELOW_MINIMUM')
        self.assertEqual(Decimal(runner.state()['cash']), Decimal('10'))
        self.assertEqual(runner.state()['fill_count'], 0)

    def test_injected_credentials_and_live_flag_cannot_arm_public_client(self):
        with patch.dict(os.environ, {'MEXC_API_KEY': 'unused-key', 'MEXC_API_SECRET': 'unused-secret',
                                    'MEXC_LIVE_TRADING': 'true', 'MEXC_BASE_URL': 'https://invalid.example'}):
            client = public_client()
        self.addCleanup(client.session.close)
        self.assertEqual(client.cfg.api_key, '')
        self.assertEqual(client.cfg.api_secret, '')
        self.assertEqual(client.cfg.base_url, 'https://api.mexc.com')
        self.assertFalse(client.cfg.live_trading)

    def test_error_payload_never_includes_exception_text_or_signed_url(self):
        import requests
        error = requests.HTTPError('https://api.mexc.com/?signature=unused-sensitive-value')
        error.response = requests.Response()
        error.response.status_code = 429
        self.assertEqual(safe_error(error), {'mode': 'paper', 'live': False,
                                            'error_type': 'HTTPError', 'http_status': 429})


class WorkerProcessTests(unittest.TestCase):
    def test_process_reports_real_cycle_health_and_stops_cleanly(self):
        # Replace only public market I/O, then exercise the real CLI, HTTP server,
        # SQLite ledger and SIGTERM handler in a separate operating-system process.
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        bootstrap = '''
import sys,time,types
sys.path.insert(0, sys.argv.pop())
from test_paper_runner import Market
import paper_runner
class FreshMarket(Market):
    def klines(self, limit):
        self.now = int(time.time() * 1000) // 60000 * 60000
        return super().klines(limit)
market = FreshMarket()
market.session = types.SimpleNamespace(close=lambda: None)
paper_runner.public_client = lambda: market
raise SystemExit(paper_runner.main())
'''
        with tempfile.TemporaryDirectory() as folder:
            proc = subprocess.Popen([sys.executable, '-u', '-c', bootstrap,
                                     str(Path(__file__).resolve().parent)],
                                    cwd=Path(paper_runner.__file__).resolve().parent,
                                    env={'PORT': str(port), 'PAPER_DATA_DIR': folder, 'PAPER_POLL_SECONDS': '1'},
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                endpoint = f'http://127.0.0.1:{port}/health'
                health = None
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and proc.poll() is None:
                    try:
                        with urlopen(endpoint, timeout=1) as response:
                            health = json.load(response)
                            break
                    except (URLError, OSError):
                        time.sleep(0.05)
                self.assertIsNotNone(health, 'Worker did not reach a healthy cycle')
                self.assertEqual(health['mode'], 'paper')
                self.assertFalse(health['live'])
                self.assertFalse(health['account_access'])
                self.assertEqual(health['status'], 'ok')
                self.assertLess(health['data_age_seconds'], 10)
                self.assertNotIn('cash', health)
                with self.assertRaises(HTTPError) as error:
                    urlopen(f'http://127.0.0.1:{port}/account', timeout=1)
                self.assertEqual(error.exception.code, 404)
                proc.terminate()
                proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0)
                state = PaperRunner(Path(folder), PaperConfig(), Market()).state()
                self.assertEqual(state['fill_count'], 1)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
