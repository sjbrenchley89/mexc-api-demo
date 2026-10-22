"""Owner CLI and continuous public-data paper worker. No live command exists."""
import argparse
from dataclasses import replace
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import threading
import time

from controlled_paper import PaperAccount, RiskSettings, RuleError, SYMBOLS
from technical_market import PublicMarket


def now_ms():
    return int(time.time() * 1000)


def safe_error(exc):
    result = {'mode': 'paper', 'live': False, 'error_type': type(exc).__name__}
    if isinstance(exc, RuleError):
        result['reason'] = str(exc)
    response = getattr(exc, 'response', None)
    if response is not None:
        result['http_status'] = response.status_code
    return result


def show_status(account):
    value = account.status(now_ms())
    print('PAPER ONLY - simulated USDT at USD parity; no account access or live orders.')
    print('Control:', 'PAUSED' if value['paused'] else 'ACTIVE')
    print('Risk locks:', ', '.join(value['locks']) or 'none')
    print('Simulated equity:', value['equity'], '| cash:', value['cash'])
    print('Daily P/L:', value['daily_pnl'], '| daily loss threshold:', value['daily_limit_amount'])
    print('Distance to account floor:', value['distance_to_floor'])
    print('Hard limits: 50 total allocation; 10 per position; 25 account floor.')
    print('All open-position prices fresh:', value['valuation_fresh'])
    print('Reviewed current halt:', value['review_current'])
    print('Simulated fills:', value['fill_count'])
    print('Disabled:', ', '.join(value['disabled_sources']))
    for position in value['positions']:
        print(position['symbol'], 'quantity', position['quantity'], 'stop', position['stop'],
              'target', position['target'], 'allocation', position['allocated'])
    print('Exchange filters, live USD valuation and live execution are not validated or enabled.')


def menu(account):
    actions = {'2': account.resume, '3': account.pause, '7': account.kill}
    while True:
        print('\nPAPER controls: 1 Status | 2 Start/resume paper | 3 Pause | 4 Record review | 5 Re-arm | 7 Kill | 0 Exit')
        choice = input('Choose a number: ').strip()
        try:
            if choice == '0':
                return 0
            if choice == '1':
                show_status(account)
            elif choice in actions:
                actions[choice](now_ms())
                show_status(account)
            elif choice == '4':
                account.review(input('Describe the cause, exposure and checks performed (no credentials): '), now_ms())
                print('Review saved. It does not itself re-arm trading.')
            elif choice == '5':
                account.rearm(input('Type REARM to resume PAPER after review: '), now_ms())
                show_status(account)
            else:
                print('Choose one of the listed actions.')
        except RuleError as exc:
            print('Control rejected:', str(exc))


def run_worker(account, symbols, once=False):
    market, server = PublicMarket(), None
    stop = threading.Event()
    health = {'last_success_ms': 0, 'failures': 0}
    lock = threading.Lock()
    try:
        if not once:
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path != '/health':
                        self.send_error(404)
                        return
                    with lock:
                        fields = dict(health)
                    status = account.status(now_ms())
                    age = (now_ms() - fields['last_success_ms']) / 1000
                    fresh = fields['last_success_ms'] > 0 and 0 <= age <= 180 and status['valuation_fresh']
                    payload = {'mode': 'paper', 'live': False, 'account_access': False,
                               'status': 'ok' if fresh else 'stale', 'paused': status['paused'],
                               'locks': status['locks'], 'data_age_seconds': round(age,1) if fields['last_success_ms'] else None,
                               **fields}
                    body = json.dumps(payload).encode()
                    self.send_response(200 if fresh else 503)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, lambda *_: stop.set())
        failures = 0
        while not stop.is_set():
            succeeded = True
            for symbol in symbols:
                try:
                    event = market.snapshot(symbol, now_ms())
                    result = account.process(event, now_ms())
                    print(json.dumps(result), flush=True)
                except Exception as exc:
                    succeeded = False
                    print(json.dumps(safe_error(exc)), flush=True)
            if once:
                return 0 if succeeded else 1
            failures = 0 if succeeded else failures + 1
            with lock:
                health['failures'] = failures
                if succeeded:
                    health['last_success_ms'] = now_ms()
            stop.wait(60 if succeeded else min(300, 15 * 2 ** min(failures - 1, 5)))
        return 0
    finally:
        if server:
            server.shutdown()
            server.server_close()
        market.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('WORKBENCH_DATA_DIR', 'controlled-paper-data')))
    sub = parser.add_subparsers(dest='command', required=True)
    initial = sub.add_parser('init', help='Initialize a new paused paper ledger; existing settings cannot be reset')
    for name, default in [('initial-cash','50'),('capital-cap','50'),('position-cap','10'),
                          ('floor','25'),('daily-loss-pct','10'),('risk-pct','0.5'),
                          ('fee-bps','10'),('slippage-bps','5'),('min-notional','1')]:
        initial.add_argument('--' + name, type=Decimal, default=Decimal(default))
    initial.add_argument('--max-positions', type=int, default=2)
    status = sub.add_parser('status')
    status.add_argument('--json', action='store_true')
    preflight = sub.add_parser('preflight', help='Inspect public exchange rules; cannot unlock live trading')
    preflight.add_argument('--symbol', choices=SYMBOLS, default='BTCUSDT')
    for command in ('pause','resume','kill','menu'):
        sub.add_parser(command)
    review = sub.add_parser('review')
    review.add_argument('--note', required=True)
    rearm = sub.add_parser('rearm')
    rearm.add_argument('--confirm', required=True)
    worker = sub.add_parser('run', help='Run continuous local-technical paper checks; preserves paused state')
    worker.add_argument('--once', action='store_true')
    worker.add_argument('--symbols', nargs='+', choices=SYMBOLS, default=['BTCUSDT'])
    args = parser.parse_args(argv)
    try:
        if args.command == 'preflight':
            from execution_readiness import run_preflight
            return run_preflight(args.symbol)
        settings = None
        if args.command == 'init':
            settings = replace(RiskSettings(), **{key: getattr(args,key) for key in RiskSettings().export() if hasattr(args,key)})
        account = PaperAccount(args.data_dir, settings)
        if args.command == 'status' and args.json:
            print(json.dumps(account.status(now_ms()), indent=2))
        elif args.command == 'menu':
            return menu(account)
        elif args.command == 'run':
            return run_worker(account, list(dict.fromkeys(args.symbols)), args.once)
        else:
            if args.command in ('pause','resume','kill'):
                getattr(account,args.command)(now_ms())
            elif args.command == 'review':
                account.review(args.note, now_ms())
            elif args.command == 'rearm':
                account.rearm(args.confirm, now_ms())
            show_status(account)
        return 0
    except Exception as exc:
        print(json.dumps(safe_error(exc)), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
