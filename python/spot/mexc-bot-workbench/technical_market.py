"""Local indicators on anonymous MEXC data; no TradingView data or account APIs."""
from decimal import Decimal
import math

import requests

from controlled_paper import MarketEvent, SYMBOLS


def ema(values, window):
    if len(values) < window:
        raise ValueError('Not enough values for EMA')
    result = [None] * (window - 1) + [sum(values[:window]) / window]
    alpha = 2 / (window + 1)
    for value in values[window:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def rsi(values, window=14):
    if len(values) <= window:
        raise ValueError('Not enough values for RSI')
    changes = [right - left for left, right in zip(values, values[1:])]
    gain = sum(max(x, 0) for x in changes[:window]) / window
    loss = sum(max(-x, 0) for x in changes[:window]) / window

    def score():
        return 50 if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)

    result = [None] * window + [score()]
    for change in changes[window:]:
        gain = (gain * (window - 1) + max(change, 0)) / window
        loss = (loss * (window - 1) + max(-change, 0)) / window
        result.append(score())
    return result


def atr(highs, lows, closes, window=14):
    ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
              for i in range(1, len(closes))]
    if len(ranges) < window:
        raise ValueError('Not enough values for ATR')
    value = sum(ranges[:window]) / window
    for item in ranges[window:]:
        value = (value * (window - 1) + item) / window
    return value


def evaluate(rows, price, now, symbol):
    closed, previous = [], None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 7:
            raise ValueError('Malformed candle')
        opened, ended = int(row[0]), int(row[6])
        if ended - opened != 900_000 or (previous is not None and opened - previous != 900_000):
            raise ValueError('Expected consecutive 15-minute candles')
        previous = opened
        if ended >= now:
            continue
        opening, high, low, close, volume = (float(row[i]) for i in (1,2,3,4,5))
        if not all(math.isfinite(x) and x > 0 for x in (opening,high,low,close)) or not math.isfinite(volume) or volume < 0:
            raise ValueError('Invalid candle data')
        if not low <= opening <= high or not low <= close <= high:
            raise ValueError('Inconsistent candle prices')
        closed.append((high,low,close,ended))
    if len(closed) < 60 or now - closed[-1][3] > 1_800_000:
        raise ValueError('Insufficient or stale closed candles')
    highs, lows, closes = ([row[index] for row in closed] for index in range(3))
    strength = rsi(closes)
    fast, slow = ema(closes,12), ema(closes,26)
    macd = [a-b for a,b in zip(fast,slow) if a is not None and b is not None]
    histogram = macd[-1] - ema(macd,9)[-1]
    average = sum(closes[-50:]) / 50
    entry = closes[-1] > average and strength[-2] <= 50 < strength[-1] < 70 and histogram > 0
    exit = strength[-1] < 45 or closes[-1] < average
    event = MarketEvent(symbol=symbol, price=price, atr=Decimal(str(atr(highs,lows,closes))),
                        entry=entry, exit=exit, observed_ms=now, candle_ms=closed[-1][3])
    event.validate(now)
    return event


class PublicMarket:
    def __init__(self):
        self.session = requests.Session()

    def get(self, path, params):
        response = self.session.get('https://api.mexc.com' + path, params=params,
                                    headers={'X-MEXC-APIKEY': None}, timeout=15, allow_redirects=False)
        response.raise_for_status()
        return response.json()

    def snapshot(self, symbol, now):
        if symbol not in SYMBOLS:
            raise ValueError('Unsupported spot symbol')
        rows = self.get('/api/v3/klines', {'symbol':symbol,'interval':'15m','limit':200})
        price = Decimal(self.get('/api/v3/ticker/price', {'symbol':symbol})['price'])
        return evaluate(rows, price, now, symbol)

    def close(self):
        self.session.close()
