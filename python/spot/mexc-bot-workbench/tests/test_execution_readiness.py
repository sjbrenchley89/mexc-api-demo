import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests

import execution_readiness as preflight
import workbench


NOW = 1_800_000_000_000


def info(**changes):
    row = {'symbol': 'BTCUSDT', 'status': '1', 'baseAsset': 'BTC',
           'baseAssetPrecision': 8, 'quoteAsset': 'USDT',
           'isSpotTradingAllowed': True, 'permissions': ['SPOT'],
           'orderTypes': ['LIMIT', 'MARKET'], 'tradeSideType': 1,
           'quoteAmountPrecisionMarket': '1', 'maxQuoteAmountMarket': '4000000',
           'baseSizePrecision': '0.000001', 'takerCommission': '0.0005',
           'filters': [{'filterType': 'PERCENT_PRICE_BY_SIDE', 'bidMultiplierUp': '0.005'}]}
    row.update(changes)
    return {'symbols': [row]}


def book(**changes):
    return {'symbol': 'BTCUSDT', 'bidPrice': '99999', 'askPrice': '100000',
            'bidQty': '1', 'askQty': '1', **changes}


def report(rules=None, ticker=None, server=None, started=NOW, finished=NOW+500):
    return preflight.build_report('BTCUSDT', info() if rules is None else rules,
                                  book() if ticker is None else ticker,
                                  {'serverTime': NOW} if server is None else server,
                                  started, finished)


class ReportTests(unittest.TestCase):
    def test_successful_public_check_always_reports_live_blockers(self):
        value = report()
        self.assertEqual(value['public_check'], 'passed')
        self.assertFalse(value['live'])
        self.assertFalse(value['live_ready'])
        self.assertFalse(value['account_access'])
        self.assertFalse(value['order_submission_available'])
        self.assertIn('durable_order_reconciliation_and_protective_execution_not_implemented', value['live_blockers'])
        self.assertEqual(value['hard_caps'], {'capital':'50', 'position':'10', 'floor':'25', 'daily_loss_percent':'10'})

    def test_minimum_quantity_is_not_treated_as_quantity_step(self):
        value = report(info(baseAssetPrecision=4, baseSizePrecision='0.003',
                            quoteAmountPrecisionMarket='1'), book(bidPrice='299', askPrice='300'))
        # Minimum notional requires 0.0034 units, NOT a multiple of 0.003.
        self.assertEqual(value['estimated_minimum_entry_cost_usdt'], '1.02153051000')
        self.assertEqual(value['rules']['minimum_quantity'], '0.003')

    def test_market_minimum_and_estimated_cost_must_fit_ten_including_costs(self):
        for minimum in ('10', '11'):
            with self.subTest(minimum=minimum):
                value = report(info(quoteAmountPrecisionMarket=minimum))
                self.assertEqual(value['public_check'], 'blocked')
                self.assertFalse(value['minimum_budget_compatible'])
                self.assertIn('estimated_exchange_minimum_does_not_fit_position_cap', value['public_blockers'])

    def test_high_minimum_quantity_and_market_maximum_are_enforced(self):
        value = report(info(baseSizePrecision='0.01'))
        self.assertFalse(value['minimum_budget_compatible'])
        value = report(info(baseSizePrecision='0.001', maxQuoteAmountMarket='2'))
        self.assertEqual(value['public_check'], 'blocked')

    def test_advertised_fee_cannot_reduce_conservative_estimate(self):
        low, high = report(), report(info(takerCommission='0.003'))
        self.assertEqual(low['estimated_fee_fraction'], '0.001')
        self.assertEqual(high['estimated_fee_fraction'], '0.003')

    def test_missing_symbol_and_duplicates_fail_closed(self):
        for payload in ({}, {'symbols': []}, {'symbols': [info()['symbols'][0]]*2}, {'symbols': None}):
            with self.subTest(payload=payload), self.assertRaises(preflight.ReadinessError):
                report(payload)

    def test_trading_disabled_or_one_sided_symbols_are_rejected(self):
        for change in ({'status':'2'}, {'status':'ENABLED'}, {'isSpotTradingAllowed':1},
                       {'permissions':[]}, {'tradeSideType':2}, {'tradeSideType':3},
                       {'orderTypes':['LIMIT']}, {'quoteAsset':'BTC'}, {'baseAsset':'ETH'}):
            with self.subTest(change=change), self.assertRaises(preflight.ReadinessError):
                report(info(**change))

    def test_numeric_metadata_missing_nonfinite_or_negative_is_rejected(self):
        for field in ('baseSizePrecision','quoteAmountPrecisionMarket','maxQuoteAmountMarket','takerCommission'):
            for value in (None, True, '-1', 'NaN', 'Infinity', 'invalid'):
                with self.subTest(field=field,value=value), self.assertRaises(preflight.ReadinessError):
                    report(info(**{field:value}))
        for field in ('baseSizePrecision','quoteAmountPrecisionMarket','maxQuoteAmountMarket'):
            with self.subTest(field=field), self.assertRaises(preflight.ReadinessError):
                report(info(**{field:'0'}))

    def test_precision_and_filter_metadata_are_required(self):
        for change in ({'baseAssetPrecision':True},{'baseAssetPrecision':-1},
                       {'baseAssetPrecision':19},{'baseAssetPrecision':'8'},
                       {'filters':None},{'filters':[{}]},{'filters':['LOT_SIZE']}):
            with self.subTest(change=change), self.assertRaises(preflight.ReadinessError):
                report(info(**change))

    def test_minimum_exceeding_exchange_maximum_is_rejected(self):
        with self.assertRaises(preflight.ReadinessError):
            report(info(maxQuoteAmountMarket='0.5'))

    def test_stale_observations_and_clock_drift_fail_closed(self):
        for start,end,server in ((NOW,NOW+15001,NOW),(NOW,NOW-1,NOW),
                                 (NOW,NOW+500,NOW-5001),(NOW,NOW+500,NOW+5501)):
            with self.subTest(start=start,end=end,server=server), self.assertRaises(preflight.ReadinessError):
                report(started=start,finished=end,server={'serverTime':server})
        for value in (None,'1800000000000',True):
            with self.assertRaises(preflight.ReadinessError):
                report(server={'serverTime':value})

    def test_wrong_crossed_empty_and_nonfinite_book_is_rejected(self):
        for change in ({'symbol':'ETHUSDT'},{'bidPrice':'100001'}, {'askQty':'0'},
                       {'bidQty':'NaN'}, {'askPrice':None}):
            with self.subTest(change=change), self.assertRaises(preflight.ReadinessError):
                report(ticker=book(**change))

    def test_wide_spread_and_small_book_are_explicit_blockers(self):
        self.assertIn('spread_exceeds_50_basis_points', report(ticker=book(bidPrice='90000'))['public_blockers'])
        self.assertIn('displayed_top_of_book_is_too_small_for_minimum', report(ticker=book(askQty='0.000001'))['public_blockers'])

    def test_report_does_not_mutate_provider_data(self):
        rules,ticker=info(),book()
        original=deepcopy((rules,ticker))
        report(rules,ticker)
        self.assertEqual((rules,ticker),original)


class Adapter(requests.adapters.BaseAdapter):
    def __init__(self, status=200):
        self.recorded=[]
        self.status=status

    def send(self, request, **kwargs):
        self.recorded.append((request,kwargs))
        result=requests.Response()
        result.status_code=self.status
        result.request=request
        path=urlsplit(request.url).path
        value={'/api/v3/exchangeInfo':info(), '/api/v3/ticker/bookTicker':book(), '/api/v3/time':{'serverTime':NOW}}[path]
        result._content=json.dumps(value).encode()
        return result

    def close(self):
        pass


class TransportTests(unittest.TestCase):
    def client(self, status=200):
        client=preflight.ReadOnlyMexc()
        adapter=Adapter(status)
        client.session.mount('https://',adapter)
        self.addCleanup(client.close)
        return client,adapter

    def test_only_fixed_anonymous_gets_ignore_live_flags_and_host(self):
        with patch.dict(os.environ, {'MEXC_API_KEY':'unused-key','MEXC_API_SECRET':'unused-secret',
                                    'MEXC_LIVE_TRADING':'true','MEXC_BASE_URL':'https://invalid.example'}):
            client,adapter=self.client()
            client.session.headers['X-MEXC-APIKEY']='unused-session-key'
            with patch('execution_readiness.milliseconds', side_effect=[NOW,NOW+500]):
                value=client.inspect('BTCUSDT')
        self.assertFalse(value['live_ready'])
        self.assertEqual(len(adapter.recorded),3)
        for request,kwargs in adapter.recorded:
            self.assertEqual(request.method,'GET')
            self.assertEqual(urlsplit(request.url).netloc,'api.mexc.com')
            self.assertNotIn('X-MEXC-APIKEY',request.headers)
            self.assertNotIn('signature',parse_qs(urlsplit(request.url).query))
            self.assertEqual(kwargs['timeout'],5)
        self.assertFalse(any(hasattr(client,method) for method in ('account','place_market_order','test_order','cancel_order')))

    def test_unapproved_symbol_is_rejected_before_network(self):
        client,adapter=self.client()
        with self.assertRaises(preflight.ReadinessError):
            client.inspect('BTCUSDT&symbol=ETHUSDT')
        self.assertEqual(adapter.recorded,[])

    def test_redirect_does_not_follow_to_another_host(self):
        client,adapter=self.client(302)
        with self.assertRaises(preflight.ReadinessError):
            client.inspect('BTCUSDT')
        self.assertEqual(len(adapter.recorded),1)

    def test_rate_limit_is_not_blindly_retried(self):
        client,adapter=self.client(429)
        with self.assertRaises(requests.HTTPError):
            client.inspect('BTCUSDT')
        self.assertEqual(len(adapter.recorded),1)


class CLITests(unittest.TestCase):
    def test_preflight_creates_no_ledger_and_never_activates_trading(self):
        fake=type('Client',(),{'inspect':lambda self,symbol:report(), 'close':lambda self:None})()
        with tempfile.TemporaryDirectory() as folder:
            data_dir=Path(folder)/'must-not-be-created'
            with patch('execution_readiness.ReadOnlyMexc',return_value=fake), patch('workbench.PaperAccount') as account:
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(workbench.main(['--data-dir',str(data_dir),'preflight']),0)
            account.assert_not_called()
            self.assertFalse(data_dir.exists())
            self.assertFalse(json.loads(output.getvalue())['live_ready'])

    def test_error_output_excludes_signed_urls_and_provider_messages(self):
        error=requests.HTTPError('https://api.mexc.com/?signature=never-display-this')
        error.response=requests.Response()
        error.response.status_code=403
        fake=type('Client',(),{'inspect':lambda self,symbol:(_ for _ in ()).throw(error), 'close':lambda self:None})()
        with patch('execution_readiness.ReadOnlyMexc',return_value=fake), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(preflight.run_preflight('BTCUSDT'),1)
        value=json.loads(output.getvalue())
        self.assertEqual(value['http_status'],403)
        self.assertFalse(value['live_ready'])
        self.assertNotIn('never-display-this',output.getvalue())


if __name__ == '__main__':
    unittest.main()
