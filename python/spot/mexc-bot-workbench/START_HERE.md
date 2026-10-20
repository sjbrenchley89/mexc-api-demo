# Start the controlled paper Workbench

This release simulates trades. It cannot connect an exchange account or place a
real order. No API keys, exchange passwords or TradingView subscription are needed.
It does not promise profits. Turning $50 into $10,000 in a week is not a credible
systematic target; do not use the simulator as evidence that it is achievable.

## 1. Check the current scope — safe to do now

The implemented first milestone is a command-line control panel, persistent paper
portfolio and one local technical strategy on MEXC public data. A command-line
interface is a text menu. Paper trading records hypothetical fills rather than
exchange transactions. A kill switch is a persistent emergency stop.

TradingView, scanner, news, FOMO/Astral copy signals, Tradovate, portal imports,
backtesting and the web dashboard are not implemented in this release. They cannot
be enabled by setting an environment variable. There is no live-mode switch.

Before using a MEXC feed, confirm your exact country is permitted under its current
[User Agreement](https://www.mexc.com/terms). The agreement checked on 20 October
2026 prohibits UK users and several Asia-Pacific jurisdictions, including Mainland
China, Singapore, Hong Kong and Malaysia. EU or Asia-Pacific registration alone
does not prove eligibility. Do not bypass regional restrictions.

Expected result: you understand this is a local-data paper experiment. If you need
real execution or your jurisdiction is not permitted, stop here.

## 2. Install Docker — safe to do now

Download Docker Desktop from Docker's official website for Windows or Mac, install
it, and open it. Docker packages the program and its dependencies together. Linux
users should follow Docker's official Docker Engine and Compose installation guide.

Expected result: Docker says its engine is running. If not, follow its setup
instructions, restart if requested, and do not continue until it is running.

## 3. Download the project — safe to do now

Open <https://github.com/sjbrenchley89/mexc-api-demo>. Click **Code**, then
**Download ZIP**. Extract the ZIP into a normal folder. Open **python**, then
**spot**, then **mexc-bot-workbench**. Read this START_HERE file before starting.

Expected result: you see start-paper.bat, start-paper.command and compose.paper.yml.
If they are absent, you have an older download; obtain the current main branch.

## 4. Open the paper controls — safe simulation only

On Windows, double-click **start-paper.bat**. On Mac, open **start-paper.command**
in Terminal after reviewing the file. If macOS refuses the downloaded script,
use the terminal alternative below rather than disabling system security.

For Mac/Linux or a terminal alternative: open Terminal in this Workbench folder.
Type **docker compose -f compose.paper.yml up -d --build**, then press Enter.
After it succeeds, type **docker compose -f compose.paper.yml exec paper python
workbench.py menu**, then press Enter. These are launcher commands, not source code.

Expected result: a numbered PAPER controls menu opens. The initial build may take
several minutes. If Docker reports an error, keep the worker stopped and resolve
that error; do not paste credentials into the terminal to troubleshoot it.

The worker runs in the background and reads public prices every minute. It starts
paused on the first run. Later restarts preserve your saved state, including an
active state. Closing the menu does not stop the background worker.

## 5. Check the status before starting — safe simulation only

Choose **1 Status**. You should see **PAPER ONLY**, **PAUSED**, simulated equity
of **50**, no risk locks, hard limits of **50 total**, **10 per position** and
an account floor of **25**. All optional sources must be disabled.

The values are simulated USDT treated as equal to USD. They are not a validated
real-world USD valuation model. The initial minimum notional and quantity step are
paper assumptions, not verified MEXC symbol rules. A notional is the value of an
order before fees. Fees and slippage are estimated costs; slippage means receiving
a worse execution price than the observed price.

If the values differ, choose **3 Pause** and check the ledger's saved settings.
Do not delete a ledger to hide losses or make performance look better.

## 6. Start and pause the simulation

Choose **2 Start/resume paper**. The status should become **ACTIVE**. The worker
waits for a new eligible signal; no fill is required immediately. A signal already
observed while paused will not be replayed after Resume.

Choose **3 Pause** to block all further simulated buys and sells. Choose **1** to
verify the saved state. If a risk lock exists, Resume will be rejected.

Pause, Kill and risk halts do not liquidate positions. Open simulated positions
continue to change in value. As requested, a halt blocks all fills, including
strategy exits and simulated stops. This is not an exchange-hosted protective stop.

## 7. Understand the strategy and sizing

The default symbol is BTCUSDT, using closed 15-minute candles. The worker computes
a 50-period simple moving average, 14-period RSI, MACD (12/26/9), and 14-period ATR
locally from anonymous MEXC candles. RSI describes recent gains versus losses;
MACD compares smoothed prices; ATR describes recent price movement.

Entry requires a close above the moving average, RSI crossing above 50 but staying
below 70, and a positive MACD histogram. Exit occurs when RSI is below 45 or the
close falls below the moving average. The initial simulated stop is two ATR below
the entry and the target is four ATR above entry. Stops are checked on each fresh
price observation, including within the same 15-minute candle, while active.

Position size is the smaller of the ATR risk budget, available simulated cash,
remaining total allocation and position cap. Entry and estimated stop-exit fees
and slippage count toward planned risk. A price gap can still exceed planned risk.
An exchange or paper minimum that cannot fit the cap causes a skipped trade; the
bot never increases a position above its cap to meet a minimum.

## 8. Review a halt and re-arm deliberately

A 10% daily mark-to-market loss latches a daily halt. It includes fees, realized
losses and changes in open positions. The baseline uses the prior persisted marks
at the first new-day observation, so a midnight gap is not silently erased.

After a daily halt, wait until a later UTC day. Choose **4 Record review** and
write what caused the problem, what exposure remains and what checks you performed.
Do not put credentials in the note. This records a review but does not resume.
Then choose **5 Re-arm** and type **REARM** exactly. Resume alone cannot bypass it.

If equity is below 25, the account-floor lock also latches. Re-arming remains
blocked while equity is below the floor. A new risk event or another kill invalidates
an earlier review. All open-position marks must be fresh before re-arming.

Expected result: a rejected action explains why. If it says the day, equity,
freshness or review condition is not met, keep the worker paused and address it.
There is deliberately no balance-reset command.

## 9. Test the emergency stop and restart

Choose **7 Kill**. Status should show **KILL_SWITCH** and **PAUSED**. Close and
reopen the menu; the lock must still be present. It requires a new review and the
explicit re-arm action. Do not expect a restart or midnight to clear it.

To stop the background service entirely, open Docker Desktop, select the paper
service and click **Stop**. To start it later, run the launcher again. Keep the
named paper-ledger storage volume. Never use an option that deletes volumes merely
to restart the program. Back up a running SQLite ledger with SQLite's backup API;
do not assume copying an active database file is a consistent backup.

## 10. Check health and understand hosting

Open <http://localhost:8081/health> while Docker is running. It exposes only mode,
pause/lock state, freshness and failure count, not balances or review notes.
Healthy means a recent valid public-data check and fresh open-position marks; a
paused worker can be healthy. Before the first valid check, or after more than
180 seconds without a successful check, it reports stale with HTTP status 503.

If stale, pause the simulation and check Docker's logs and internet access. A
Docker healthcheck reports the problem but does not automatically restart an
unhealthy container. Network failures retry with bounded backoff. No guarantee of
24/7 availability is implied.

Your Railway workspace previously rejected a new service because its free-plan
resource limit was reached. This release does not claim a hosted deployment.
Local operation lasts only while Docker and your computer are running and awake.

## Saved settings and files

The default settings are 50 initial simulated USDT, 50 total allocation, 10 per
position, two open positions, 0.5% planned equity risk per trade, 10% daily loss
threshold, 25 account floor, 10 basis points estimated fee and 5 basis points
estimated slippage. A basis point is one hundredth of one percent. The simulation
uses a 1 USDT minimum entry notional and a 0.00000001 quantity step as assumptions.

An advanced user can lower caps or set risk up to 1% when initializing a genuinely
new experiment through the CLI's **init** options. Use **python workbench.py init
--help** to see them. Settings are immutable for an existing ledger. The code
rejects cap increases above 50/10, a floor below 25, or a daily loss threshold above
10%. Profits cannot automatically raise the allocated-capital ceiling. Caps apply
to entry capital and costs; market movements can change a position's market value.

**workbench.py** provides the CLI and worker; **controlled_paper.py** holds the
transactional ledger and risk controls; **technical_market.py** reads public data
and computes indicators. **compose.paper.yml** runs the container. The named volume
stores **/data/controlled/controlled-paper.sqlite3**. Outside Docker, the default
directory is **controlled-paper-data**. Old paper-data files are not imported or
overwritten. **paper_runner.py** is the older, separate Donchian simulator with
different defaults; do not use it for this controlled workflow.

## What must wait

The dashboard, historical backtests, mover scanner, news feed, portal imports and
provider adapters require later implementation and separate validation. This
milestone proves controls, not strategy profitability. Do not risk real funds based
on it. Paper cost assumptions omit order-book liquidity, partial fills, exchange
filters and account-specific fees.

TradingView's current [terms](https://www.tradingview.com/policies/) prohibit using
its data and webhooks for automated trading and algorithmic decision-making; a paid
plan alone does not authorize this. Copy signals need the exact provider identity,
official permitted API and applicable data rights; no scraping or credential sharing.
Tradovate is futures-focused and its standard API requirements exceed this $50
budget. Portal recommendations have not been supplied and are not invented here.
No live permissions, exchange credentials or account connections are part of this
release. No real order should be used to test installation.
