from decimal import Decimal as D
import math
import os
import random
import unittest
from unittest.mock import patch

from technical_market import PublicMarket, atr, ema, evaluate, rsi


NOW = 1_800_000_000_000


def bars():
    rows = []
    for i in range(200):
        close = 100 + i / 10
        end = NOW - (200 - i) * 900_000
        rows.append([end - 900_000, str(close), str(close + 1), str(close - 1), str(close), '100', end])
    return rows


class TechnicalTests(unittest.TestCase):
    def test_wilder_rsi_matches_published_calculation_example(self):
        values = [44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28]
        self.assertAlmostEqual(rsi(values)[-1], 70.464135, places=5)

    def test_flat_rsi_and_constant_range_atr_are_defined(self):
        self.assertEqual(rsi([100] * 60)[-1], 50)
        self.assertEqual(atr([102] * 60, [98] * 60, [100] * 60), 4)
        self.assertEqual(ema([1,2,3,4,5], 3)[-1], 4)

    def test_open_candle_cannot_change_signal_or_atr(self):
        rows = bars()
        original = evaluate(rows, D('120'), NOW, 'BTCUSDT')
        rows.append([NOW - 900_000, '100', '1000', '1', '999', '100', NOW])
        self.assertEqual(evaluate(rows, D('120'), NOW, 'BTCUSDT'), original)
        self.assertFalse(original.entry)  # Rising RSI is 100, outside the entry range.
        self.assertEqual(original.atr, D('2.0'))

    def test_stale_missing_and_nonfinite_bars_are_rejected(self):
        missing = bars()
        del missing[100]
        invalid = bars()
        invalid[-1][4] = 'nan'
        for rows, now in ((bars(), NOW + 3_600_000), (missing, NOW), (invalid, NOW), (bars()[:30], NOW)):
            with self.subTest(now=now), self.assertRaises(ValueError):
                evaluate(rows, D('120'), now, 'BTCUSDT')

    def test_market_client_has_no_account_or_order_methods_and_ignores_credentials(self):
        with patch.dict(os.environ, {'MEXC_API_KEY': 'unused-key', 'MEXC_LIVE_TRADING': 'true',
                                    'MEXC_BASE_URL': 'https://invalid.example'}):
            market = PublicMarket()
        self.addCleanup(market.close)
        self.assertNotIn('X-MEXC-APIKEY', market.session.headers)
        for name in ('account', 'place_market_order', 'test_order'):
            self.assertFalse(hasattr(market, name))

    def test_technical_entry_can_trigger_after_a_closed_bar_recovery(self):
        # Fixed synthetic history; this tests signal generation, not profitability.
        rng, close, rows = random.Random(117), 100, []
        for index in range(200):
            close += 0.05 + rng.uniform(-1,1)
            end = NOW - (200-index) * 900_000
            rows.append([end-900_000,close,close+1,close-1,close,100,end])
        result = evaluate(rows, D(str(close)), NOW, 'BTCUSDT')
        self.assertTrue(result.entry)
        self.assertFalse(result.exit)


if __name__ == '__main__':
    unittest.main()
