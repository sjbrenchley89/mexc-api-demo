# MEXC Bot Workbench

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

## Continuous paper-trading worker

`paper_runner.py` runs a persistent **simulation**, using real public BTCUSDT
prices and the same closed-candle Donchian-20/10 signals. It never reads an
exchange account, sends an order or consumes API credentials. Live flags and
custom exchange hosts are ignored. It cannot be armed for real-money trading.

```bash
python paper_runner.py --once --data-dir paper-data
python paper_runner.py --data-dir paper-data
```

The first command runs one simulated cycle; the second keeps polling every
60 seconds. The worker stores its simulated cash, BTC position, fills, last
processed candle and daily risk baseline in `paper-data/paper.sqlite3`.
A transaction covers each candle and fill, preventing duplicate fills after
restarts or simultaneous cycles. Failed writes roll back the entire fill.
Keep this directory on persistent storage and back it up using SQLite's backup
API while the worker runs. Do not delete it to restart the worker.

Default simulation settings (all configurable with `PAPER_` environment names):

| Setting | Default | Meaning |
| --- | --- | --- |
| `INITIAL_CASH` | 1000 | Simulated starting USDT; immutable for an existing ledger |
| `POSITION_PCT` | 5 | Maximum initial-cash allocation per entry, including fees |
| `DAILY_LOSS_PCT` | 2 | Mark-to-market loss threshold for blocking new entries |
| `MIN_NOTIONAL` | 10 | Minimum simulated entry notional in USDT |
| `FEE_BPS` | 10 | Estimated fee per simulated fill; not the account's actual fee |
| `SLIPPAGE_BPS` | 5 | Estimated adverse slippage per simulated fill |
| `POLL_SECONDS` | 60 | Seconds between successful market checks |

Daily loss includes unrealized losses and estimated fees. A halt persists
through restarts until the next UTC day; the first valid check that day sets
the new baseline. Strategy exits remain allowed during a halt. This threshold
does not guarantee a maximum loss: gaps, downtime and the strategy's exits
can produce larger losses. SELL only consumes this ledger's simulated BTC.
Simulation fills are simplified and do not validate MEXC quantity filters,
liquidity, actual fees, partial fills or real-order reconciliation.

Stale or invalid market data leaves the ledger unchanged. Network errors use
bounded exponential backoff (15 to 300 seconds); logs include only the error
class and HTTP status, never exception URLs. The worker exits cleanly on
SIGTERM/SIGINT and uses the ledger on restart.

The HTTP endpoint `GET /health` on `PORT` (default 8080) exposes only simulated
mode, public-data freshness and failure count, not portfolio balances. It returns
503 until a valid check succeeds, and again when the last valid check is older
than 180 seconds. A running web server alone is not considered healthy.
Other paths return 404. The public GitHub Pages feed remains a separate signal
monitor and does not publish this simulated portfolio.

For a Linux server with Docker Compose:

```bash
docker compose -f compose.paper.yml up -d --build
docker compose -f compose.paper.yml logs --tail=50
curl --fail http://127.0.0.1:8081/health
```

The named volume preserves the ledger; do not use `down --volumes` when
restarting. Restart policy handles process exits; a Docker healthcheck reports
staleness but does not itself restart a container. Hosting availability and
exchange API access still limit uptime.

On Railway, use this directory as the service root, the included Dockerfile,
one replica, sleeping disabled, restart policy `ALWAYS`, and a persistent volume
at `/data` with `PAPER_DATA_DIR=/data/paper`. Start with an isolated service and
no exchange secrets. Verify a terminal successful deployment and a fresh
`/health` response before describing the worker as running.
