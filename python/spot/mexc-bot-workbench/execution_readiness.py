"""Anonymous MEXC spot preflight. This module cannot access accounts or send orders."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import time

import requests

from controlled_paper import RiskSettings, SYMBOLS


D = Decimal
DOCUMENTATION = 'https://mexcdevelop.github.io/apidocs/spot_v3_en/#exchange-information'
BLOCKERS = (
    'country_and_account_eligibility_not_verified',
    'trade_only_permissions_and_withdrawals_disabled_not_verified',
    'account_specific_fees_and_all_holdings_not_verified',
    'live_usd_valuation_and_persistent_risk_integration_not_implemented',
    'durable_order_reconciliation_and_protective_execution_not_implemented',
    'strategy_backtesting_and_paper_acceptance_not_completed',
    'real_order_submission_not_implemented_in_controlled_workbench',
)


class ReadinessError(ValueError):
    """Fixed, non-secret explanation safe to display to the owner."""


def milliseconds():
    return int(time.time() * 1000)


def number(value, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ReadinessError('Expected a finite exchange number')
    try:
        result = D(str(value))
    except (InvalidOperation, ValueError):
        raise ReadinessError('Expected a finite exchange number') from None
    if not result.is_finite() or result < 0 or (not zero and result == 0):
        raise ReadinessError('Expected a positive exchange number')
    return result


def approved_symbol(symbol):
    if symbol not in SYMBOLS:
        raise ReadinessError('Only the approved spot symbols are supported')


def exchange_rules(payload, symbol):
    approved_symbol(symbol)
    if not isinstance(payload, dict) or not isinstance(payload.get('symbols'), list):
        raise ReadinessError('Exchange information is malformed')
    matches = [row for row in payload['symbols'] if isinstance(row, dict) and row.get('symbol') == symbol]
    if len(matches) != 1:
        raise ReadinessError('Exactly one matching exchange symbol is required')
    row = matches[0]
    if str(row.get('status')) != '1' or row.get('isSpotTradingAllowed') is not True:
        raise ReadinessError('This spot symbol is not online for API trading')
    if row.get('quoteAsset') != 'USDT' or row.get('baseAsset') != symbol.removesuffix('USDT'):
        raise ReadinessError('Unexpected spot base or quote asset')
    if not isinstance(row.get('permissions'), list) or 'SPOT' not in row['permissions']:
        raise ReadinessError('Spot permission is not advertised for this symbol')
    if not isinstance(row.get('orderTypes'), list) or 'MARKET' not in row['orderTypes']:
        raise ReadinessError('Market orders are not advertised for this symbol')
    if str(row.get('tradeSideType')) != '1':
        raise ReadinessError('Both buy and sell must be available for the spot symbol')
    precision = row.get('baseAssetPrecision')
    if type(precision) is not int or not 0 <= precision <= 18:
        raise ReadinessError('Quantity decimal precision is missing or invalid')
    filters = row.get('filters')
    if not isinstance(filters, list) or any(not isinstance(f, dict) or not isinstance(f.get('filterType'), str) for f in filters):
        raise ReadinessError('Exchange filters are missing or malformed')
    minimum = number(row.get('quoteAmountPrecisionMarket'))
    maximum = number(row.get('maxQuoteAmountMarket'))
    if maximum < minimum:
        raise ReadinessError('Exchange minimum exceeds its maximum')
    fee = number(row.get('takerCommission'), zero=True)
    if fee >= D('0.1'):
        raise ReadinessError('Advertised fee is outside the supported preflight range')
    return {
        'symbol': symbol,
        'quantity_decimal_places': precision,
        # MEXC documents baseSizePrecision as a MINIMUM quantity, not a step.
        'minimum_quantity': number(row.get('baseSizePrecision')),
        'minimum_market_notional': minimum,
        'maximum_market_notional': maximum,
        'advertised_taker_fee_fraction': fee,
        'filter_types_requiring_execution_validation': sorted({f['filterType'] for f in filters}),
    }


def build_report(symbol, info, book, server, started, finished):
    approved_symbol(symbol)
    if type(started) is not int or type(finished) is not int or not 0 <= finished - started <= 15_000:
        raise ReadinessError('Public preflight data is stale or the local clock moved')
    if not isinstance(server, dict) or type(server.get('serverTime')) is not int:
        raise ReadinessError('Exchange server time is invalid')
    server_ms = server['serverTime']
    if server_ms < started - 5_000 or server_ms > finished + 5_000:
        raise ReadinessError('Exchange and local clocks differ by more than five seconds')
    rules = exchange_rules(info, symbol)
    if not isinstance(book, dict) or book.get('symbol') != symbol:
        raise ReadinessError('Book ticker has the wrong symbol')
    bid, ask = number(book.get('bidPrice')), number(book.get('askPrice'))
    bid_qty, ask_qty = number(book.get('bidQty')), number(book.get('askQty'))
    if bid > ask:
        raise ReadinessError('Book ticker is crossed')
    settings = RiskSettings()
    settings.validate()
    # Public fees are indicative; use at least the conservative paper estimate.
    fee = max(rules['advertised_taker_fee_fraction'], settings.fee_bps / 10_000)
    slip = settings.slippage_bps / 10_000
    quantum = D(1).scaleb(-rules['quantity_decimal_places'])
    minimum_quantity = max(rules['minimum_quantity'], rules['minimum_market_notional'] / ask)
    minimum_quantity = (minimum_quantity / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum
    estimated_minimum_cost = minimum_quantity * ask * (1 + slip) * (1 + fee)
    spread_bps = (ask - bid) / ask * 10_000
    public_blockers = []
    if estimated_minimum_cost > settings.position_cap or minimum_quantity * ask > rules['maximum_market_notional']:
        public_blockers.append('estimated_exchange_minimum_does_not_fit_position_cap')
    if spread_bps > 50:
        public_blockers.append('spread_exceeds_50_basis_points')
    if ask * ask_qty < estimated_minimum_cost or bid * bid_qty < estimated_minimum_cost:
        public_blockers.append('displayed_top_of_book_is_too_small_for_minimum')
    return {
        'mode': 'execution_preflight', 'live': False, 'live_ready': False,
        'account_access': False, 'order_submission_available': False,
        'observed_at': datetime.fromtimestamp(finished / 1000, timezone.utc).isoformat(),
        'symbol': symbol, 'public_check': 'passed' if not public_blockers else 'blocked',
        'rules': {key: str(value) if isinstance(value, Decimal) else value for key, value in rules.items()},
        'estimated_minimum_entry_cost_usdt': str(estimated_minimum_cost),
        'estimated_fee_fraction': str(fee), 'spread_bps': str(spread_bps),
        'minimum_budget_compatible': estimated_minimum_cost <= settings.position_cap,
        'hard_caps': {'capital': '50', 'position': '10', 'floor': '25', 'daily_loss_percent': '10'},
        'public_blockers': public_blockers,
        'live_blockers': list(BLOCKERS) + (
            ['symbol_filters_require_execution_validation']
            if rules['filter_types_requiring_execution_validation'] else []),
        'limitations': [
            'No account, balances, keys, private fees or order permissions were checked.',
            'Public rules, displayed liquidity and estimated costs do not guarantee order acceptance or fills.',
            'USDT is treated at USD parity; future live dollar valuation remains unverified.',
            'This does not apply exchange rules to the existing paper ledger or change its controls.',
        ],
        'documentation': DOCUMENTATION,
    }


class ReadOnlyMexc:
    """Only three fixed public GETs; no private or mutating API surface."""

    def __init__(self):
        self.session = requests.Session()

    def inspect(self, symbol):
        approved_symbol(symbol)
        started = milliseconds()
        values = []
        for path, params in (
            ('/api/v3/exchangeInfo', {'symbol': symbol}),
            ('/api/v3/ticker/bookTicker', {'symbol': symbol}),
            ('/api/v3/time', {}),
        ):
            response = self.session.get('https://api.mexc.com' + path, params=params,
                                        headers={'X-MEXC-APIKEY': None}, timeout=5,
                                        allow_redirects=False)
            if not 200 <= response.status_code < 300:
                response.raise_for_status()
                raise ReadinessError('Unexpected exchange redirect or response')
            values.append(response.json())
        return build_report(symbol, *values, started, milliseconds())

    def close(self):
        self.session.close()


def run_preflight(symbol):
    client = ReadOnlyMexc()
    try:
        report = client.inspect(symbol)
        print(json.dumps(report, indent=2, allow_nan=False))
        # Success means public inspection completed, never permission to go live.
        return 0 if report['public_check'] == 'passed' else 1
    except Exception as exc:
        error = {'mode': 'execution_preflight', 'live': False, 'live_ready': False,
                 'account_access': False, 'order_submission_available': False,
                 'error_type': type(exc).__name__}
        if isinstance(exc, ReadinessError):
            error['reason'] = str(exc)
        response = getattr(exc, 'response', None)
        if response is not None:
            error['http_status'] = response.status_code
        print(json.dumps(error))
        return 1
    finally:
        client.close()
