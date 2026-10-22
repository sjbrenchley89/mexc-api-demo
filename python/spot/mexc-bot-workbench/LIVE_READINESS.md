# Check the prerequisites for future live execution

This is a read-only preparation tool. It does not install a live trader, connect
your account, approve a strategy or place an order. Its report always says
**live_ready: false** and **order_submission_available: false**. A successful
public check cannot unlock trading. The existing paper worker keeps its controls
and its saved ledger.

## 1. Check eligibility first — public data only

Confirm the exact country where you live and operate the account, and check the
current [MEXC User Agreement](https://www.mexc.com/terms). An existing account or
API key does not prove permission to use a service. The jurisdiction checks in
START_HERE still apply. Never bypass a location restriction.

Expected result: you have independently confirmed the permitted use for your
country and account type. If uncertain, ask MEXC before attempting any account
connection. Do not send passwords, API keys or secret values in chat.

## 2. Download the current release — safe to do now

Open the project on GitHub, choose **Code**, then **Download ZIP**, and extract it.
Open **python**, **spot**, then **mexc-bot-workbench**. Follow START_HERE to install
Docker if needed. Keep your existing named paper-ledger volume; do not delete or
reset an experiment to upgrade the files.

Expected result: the folder includes **execution_readiness.py** and this guide.
The new file implements the public preflight, which means an inspection before
execution. If it is absent, download the current version again.

## 3. Run the public inspection — no real money or account access

For a local Docker installation, open Terminal in the Workbench folder. Type
**docker compose -f compose.paper.yml build** and press Enter. This builds the
updated image without restarting an existing worker.

Then type **docker compose -f compose.paper.yml run --rm --no-deps paper python
workbench.py preflight --symbol BTCUSDT** and press Enter. This runs one inspection
in a temporary container and removes that container when it finishes. It does not
publish a port or start a second trading worker. ETHUSDT is the other supported
symbol. Keep all API credential fields empty; this tool does not use them.

Expected result: a report headed **execution_preflight**, with **account_access:
false**, **live: false**, **live_ready: false** and a list of remaining blockers.
An existing paper ledger must not change. A successful command or a public check
labelled **passed** only means the public inspection completed within its limits.

If the report shows an error or **public_check: blocked**, read its fixed reason
and stop. Rate limits and network failures are not proof of a failed account or
missing credentials. Wait before retrying; do not add keys to troubleshoot public
requests. Do not change any live flag.

## 4. Understand what was inspected

The tool makes three anonymous requests to MEXC: exchange information, the best
displayed buy/sell quotes, and server time. It checks that the symbol is online
for spot API trading, supports market orders in both directions, and provides the
required numeric rules. It rejects malformed or missing values and observations
that take more than 15 seconds, or server clocks outside the five-second margin.

MEXC documents **baseSizePrecision** as the minimum quantity, and
**baseAssetPrecision** as quantity decimal precision. They are different rules.
The preflight rounds the estimated minimum quantity up to the decimal precision,
then accounts for the market minimum, advertised fee, a fee estimate of at least
0.1%, and estimated slippage of 0.05%. It checks whether this estimated entry cost
fits the 10-USDT position cap. These percentages are conservative assumptions,
not verified fees for your account. If the minimum does not fit, the tool reports
a blocker; it never increases the cap.

A spread is the gap between the displayed buy and sell prices. A spread above
50 basis points (0.5%), or too little displayed quantity for the minimum, is a
public blocker. Displayed quotes can change, and responses have no guarantee of
available liquidity or execution. Filter names are listed for later execution
validation; the preflight does not claim every venue filter is enforced.

Public rules follow the [official MEXC spot API documentation](https://mexcdevelop.github.io/apidocs/spot_v3_en/#exchange-information).
They can change. USDT/USD parity is still an assumption. No account-specific fee,
available balance, actual holding, API permission or jurisdiction is checked.

## 5. Complete these items before any future live implementation

- Confirm country/account eligibility and permitted use independently.
- Verify trade-only API permissions, disabled withdrawals and an IP allowlist;
  store credentials in a secrets manager, never source code or chat.
- Reconcile every existing holding, locked amount, pending order and fee currency.
  The bot must never sell holdings it does not own or reserve the same cash twice.
- Implement live dollar valuation and persistent risk enforcement for 50 total,
  10 per position, the 25 floor and the 10% daily loss limit. A breached daily limit
  requires both a later UTC day and a recorded manual review with explicit re-arm.
- Implement durable order identities, pending reservations, partial fills and
  reconciliation after timeouts or restarts. An unknown order result must halt
  submissions rather than trigger a blind retry.
- Validate venue filters and protective exits against actual holdings and fills.
  A paused paper worker does not liquidate positions or provide exchange stops.
- Backtest and paper-trade with realistic costs. Require a documented acceptance
  review, loss/timeout/restart drills and evidence the strategy survives costs;
  there is no validated profitable backtest in this release.
- Keep TradingView and optional providers disconnected until their data rights,
  terms and permitted official interfaces are confirmed. A paid plan is not
  permission to use data for automated trading.
- For any later live release, start within 50 total and 10 per position, keep the
  kill switch available, and review weekly. This release does not offer that mode.

The complete checklist has not been passed. Running this inspection is not a
go-live approval. **workbench.py** and its container remain paper-only.
