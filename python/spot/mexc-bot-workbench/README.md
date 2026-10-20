# MEXC Bot Workbench

For the current $50 controlled paper workflow, begin with [START_HERE](START_HERE.md).
The account-backed scaffold below is legacy code, not a live-ready trading system.

A Donchian signal monitor and an account-backed Spot bot scaffold. The automated
workflow uses public market data only. It never reads an account, persists a
portfolio, or places an order.

## Setup and tests

Use Python 3.12 or later in this directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

On Windows, activate with `.venv/Scripts/activate` instead. The runtime imports
only `requests` and `python-dotenv`; exchange credentials are not needed for tests
or public monitoring.

## Public-data monitoring

```bash
python monitor.py --output monitor-output/latest.json
```

Each run checks BTCUSDT using one-minute candles, a 20-period entry channel and a
10-period exit channel. It excludes the open candle and rejects missing, invalid,
out-of-order or stale data. The snapshot contains the current price and two
scenarios: the signal without a position, and the signal with a position.
Actual holdings remain unknown. A SELL signal is a conditional strategy output,
not an instruction to sell an account holding.

The monitor always uses anonymous public requests to `https://api.mexc.com`,
even if live-trading flags or credentials exist in the environment. No balance,
position size, execution, daily P/L or portfolio simulation is performed.
Request failures report an exception class and HTTP status without signed URLs.

## GitHub automation

The repository workflow `.github/workflows/mexc-workbench.yml`:

- Runs the test suite on relevant pushes and pull requests.
- Checks public signals on the default branch at minutes 7, 22, 37 and 52 UTC.
- Supports **Actions → MEXC Workbench → Run workflow** for an immediate check.
- Keeps each successful JSON snapshot as a workflow artifact for seven days.

GitHub may delay or skip scheduled runs; this is periodic monitoring, not a
continuous service or an execution scheduler. The workflow needs no exchange
secrets and has read-only repository permissions. Installation of the Workbench
plugin alone does not create or activate the GitHub workflow.

## Public status site

The workflow builds a static status page, a `status.json` feed, an OpenAPI
description, `llms.txt`, robots instructions and a sitemap. Only approved public
snapshot fields are published; account data and credentials are excluded.
The page reports both position scenarios and labels snapshots older than
30 minutes stale, including when JavaScript is disabled at publication time.

Enable **Settings → Pages → Source → GitHub Actions** in this repository.
Successful checks on `main` then publish to
<https://sjbrenchley89.github.io/mexc-api-demo/>. Deployment status is visible in
the workflow's `publish` job. A failed check leaves the previous successful page
visible with its original observation timestamp.

To preview a build locally after a public monitor run:

```bash
python build_site.py monitor-output/latest.json --output-dir site-output
python -m http.server 8000 --directory site-output
```

Visit `http://localhost:8000`. The build needs no exchange credentials.

## Optional account-backed preview

Keep credentials in environment injection or a local `.env` excluded from Git.
`.env.example` contains blank credential fields and the scaffold defaults:
BTCUSDT, 5% quote-balance allocation, a configured 10 USDT minimum notional,
and a configured 2% loss threshold. These are application settings, not verified
exchange filters.

Run only one non-executing cycle:

```python
import os
os.environ["MEXC_LIVE_TRADING"] = "false"
from mexc_bot import Config, TradingBot

try:
    cfg = Config(live_trading=False, base_url="https://api.mexc.com")
    print(TradingBot(cfg).run_once())
except Exception as exc:
    response = getattr(exc, "response", None)
    print({"error_type": type(exc).__name__,
           "http_status": response.status_code if response is not None else None})
```

If a managed runtime exposes a verified key pair under other names, pass their
values to `Config(api_key=..., api_secret=..., live_trading=False,
base_url="https://api.mexc.com")` without printing or saving those values.
This preview reads account balances. A `dry_run` result is hypothetical and
does not update a paper portfolio.

## Execution limits

The inherited bot contains live-order code but is not validated for live use.
Quantity filters, free-versus-locked balances, closed candles, calendar-day loss
tracking, zero-equity handling, durable duplicate suppression and order
reconciliation still need engineering and exchange validation before live
execution. The public monitor does not enable that code path.

Astral's BTCUSD strategy uses a different quote currency and data provider from
MEXC BTCUSDT. Connecting Astral does not route this monitor's signals to MEXC.

## Controlled paper CLI (current Docker default)

Start with [the beginner walkthrough](START_HERE.md). The current container starts
`workbench.py run`, a public-data simulation with a separate persistent ledger.
It starts paused on first use; later restarts preserve the saved control state.
No API keys or live mode are supported. The local technical strategy follows the
Option A rules on MEXC data; it does not consume TradingView alerts.

Defaults: 50 simulated USDT, 50 maximum allocated capital, 10 per position, two
positions, 0.5% planned equity risk per trade, a 10% daily-loss halt, and a 25 account
floor. ATR sizing includes estimated entry and stop-exit costs. USDT/USD parity,
fees, minimum notional and quantity increments are paper assumptions, not verified
live exchange constraints. Market moves can exceed stop-loss budgets.

The CLI provides status, menu, init, run, pause, resume, review, rearm and kill.
Use `python workbench.py --help` for commands. Put `--data-dir` before the command
to select a ledger. Ordinary Resume cannot clear risk locks. A daily breach needs
a later UTC day, a recorded review and explicit REARM confirmation. Floor and kill
locks persist too. Pause and risk halts block all fills, including simulated exits;
positions remain exposed and are not automatically liquidated.

Docker Compose retains the named paper-ledger volume and stores the controlled
ledger in /data/controlled. Existing /data/paper legacy data is not imported or
modified. Do not delete volumes or reset ledgers when restarting.

The old `paper_runner.py` is retained only as a separate legacy Donchian simulation
with its original 1,000-USDT seed, 2% entry-only halt and automatic daily reset.
Those are not the controlled workflow's settings. It is no longer the image's
entrypoint. The public GitHub Pages signal feed remains independent of both paper
ledgers.

Backtesting, a dashboard, scanner, news, copy trading, Tradovate, portal imports and
live execution remain outside this first controlled-CLI milestone. They are not
represented as completed integrations or validated profitable strategies.
