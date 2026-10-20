# MEXC AI Trading Bot Scaffold

A conservative MEXC Spot bot scaffold using a Donchian breakout signal.

## Safety defaults
- Spot only
- `MEXC_LIVE_TRADING=false` by default
- Maximum position allocation defaults to 5% of quote balance
- Daily loss kill switch defaults to 2%
- Duplicate signal/order suppression per candle
- API credentials come only from environment variables

## MEXC API endpoints used
- `GET /api/v3/account`
- `GET /api/v3/ticker/price`
- `GET /api/v3/klines`
- `POST /api/v3/order` only when live trading is enabled

## Setup
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
```

Create a MEXC API key with only the permissions required for Spot trading. Restrict the key by IP and trading pairs where available. Never enable withdrawal permissions for a trading bot.

Fill `.env` locally. Keep:
```env
MEXC_LIVE_TRADING=false
```
for validation.

Run tests:
```bash
python -m unittest discover -s tests -v
```

Run:
```bash
python mexc_bot.py
```

## Going live
Only after dry-run logs are correct, set `MEXC_LIVE_TRADING=true`. Start with a low balance and verify MEXC symbol filters/minimum quantities before placing real orders.

## Important
MEXC states that its API connects to the live environment and does not offer a sandbox. This project therefore implements its own non-executing dry-run mode.
