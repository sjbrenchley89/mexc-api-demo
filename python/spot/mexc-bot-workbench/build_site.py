"""Render a static, public-only status site from a validated monitor snapshot."""

import argparse
from datetime import datetime, timezone
import html
import json
import math
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit
from xml.sax.saxutils import escape as xml_escape


BASE_URL = 'https://sjbrenchley89.github.io/mexc-api-demo/'
REPO_URL = 'https://github.com/sjbrenchley89/mexc-api-demo'
TEMPLATES = Path(__file__).with_name('site_templates')


def public_snapshot(value, now):
    if not isinstance(value, dict):
        raise ValueError('Expected a public snapshot')
    expected = {'mode': 'public_signal_monitor', 'symbol': 'BTCUSDT',
                'interval': '1m', 'position_state': 'unknown'}
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError('Only the public BTCUSDT monitor can be published')
    if value.get('live') is not False or value.get('account_access') is not False:
        raise ValueError('Account-backed or executing data cannot be published')
    try:
        observed = datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
        price = float(value['price'])
        closed_ms = int(value['last_closed_candle_ms'])
        signals = value['signals']
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('Invalid public snapshot') from exc
    if observed.tzinfo is None or (observed - now).total_seconds() > 300:
        raise ValueError('Invalid observation timestamp')
    if isinstance(value['price'], bool) or not math.isfinite(price) or price <= 0:
        raise ValueError('Invalid public price')
    if not 0 < closed_ms <= int(observed.timestamp() * 1000):
        raise ValueError('Invalid closed-candle timestamp')
    if not isinstance(signals, dict) or signals.get('without_position') not in ('BUY', 'HOLD') or signals.get('with_position') not in ('SELL', 'HOLD'):
        raise ValueError('Invalid conditional signals')
    # Copy only approved public fields, including a whitelist inside signals.
    return {
        **expected, 'observed_at': observed.astimezone(timezone.utc).isoformat(),
        'last_closed_candle_ms': closed_ms, 'price': price,
        'signals': {key: signals[key] for key in ('without_position', 'with_position')},
        'account_access': False, 'live': False,
    }


def publish_site(snapshot, destination, *, now=None, base_url=BASE_URL, run_url=None):
    now = now or datetime.now(timezone.utc)
    snapshot = public_snapshot(snapshot, now)
    base_url = base_url.rstrip('/') + '/'
    parsed = urlsplit(base_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Expected a public HTTPS site URL')
    if run_url is not None and not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+', run_url):
        raise ValueError('Expected a GitHub Actions run URL')
    observed = datetime.fromisoformat(snapshot['observed_at'])
    recent = (now - observed).total_seconds() <= 1800
    feed = {'schema_version': 1, 'generated_at': now.isoformat(), 'snapshot': snapshot}
    if run_url:
        feed['run_url'] = run_url
    schema = {
        '@context': 'https://schema.org', '@type': 'WebApplication',
        'name': 'MEXC Bot Workbench public status', 'url': base_url,
        'applicationCategory': 'FinanceApplication', 'operatingSystem': 'Any',
        'description': 'Public BTCUSDT Donchian signal monitoring. No account access or order execution.',
        'isAccessibleForFree': True, 'inLanguage': 'en',
        'dateModified': snapshot['observed_at'],
    }
    substitutions = {
        '__BASE__': html.escape(base_url, quote=True),
        '__PRICE__': f"{snapshot['price']:,.2f}",
        '__OBSERVED__': html.escape(snapshot['observed_at'], quote=True),
        '__OBSERVED_LABEL__': observed.strftime('%d %b %Y, %H:%M:%S UTC'),
        '__ENTRY__': snapshot['signals']['without_position'],
        '__EXIT__': snapshot['signals']['with_position'],
        '__FRESHNESS__': 'Recent snapshot' if recent else 'Stale snapshot',
        '__FRESHNESS_CLASS__': 'recent' if recent else 'stale',
        '__RUN_URL__': html.escape(run_url or REPO_URL + '/actions/workflows/mexc-workbench.yml', quote=True),
        '__SCHEMA__': json.dumps(schema, indent=2).replace('<', '\\u003c'),
    }
    page = (TEMPLATES / 'index.html').read_text(encoding='utf-8')
    for key, value in substitutions.items():
        page = page.replace(key, value)
    api = {
        'openapi': '3.1.0',
        'info': {'title': 'MEXC Workbench public status', 'version': '1.0.0',
                 'description': 'Read-only public market snapshots. No authentication, account data, or execution.'},
        'servers': [{'url': base_url.rstrip('/')}],
        'paths': {'/status.json': {'get': {'operationId': 'getPublicSnapshot',
            'summary': 'Get the last published public BTCUSDT signal snapshot',
            'responses': {'200': {'description': 'The latest successful public snapshot; inspect observed_at for freshness.',
                'content': {'application/json': {'schema': {'type': 'object',
                    'required': ['schema_version', 'generated_at', 'snapshot'],
                    'properties': {'schema_version': {'const': 1},
                        'generated_at': {'type': 'string', 'format': 'date-time'},
                        'snapshot': {'type': 'object', 'description': 'Conditional market signals. Holdings are unknown.'},
                        'run_url': {'type': 'string', 'format': 'uri'}}}, 'example': feed}}}}}}},
    }
    llms = f'''# MEXC Bot Workbench

> Public BTCUSDT market monitoring using closed one-minute candles. No account data, brokerage access or order execution.

## Status and data
- [Public status page]({base_url}): latest successful market snapshot, source and freshness.
- [Public JSON feed]({base_url}status.json): schema_version 1; observed_at is inside snapshot. Check its age before using signals.
- [OpenAPI description]({base_url}openapi.json): read-only GET /status.json. No authentication.
- [Source repository]({REPO_URL}): Python implementation, tests and GitHub Actions workflow.
- [Monitoring workflow]({REPO_URL}/actions/workflows/mexc-workbench.yml): tests and execution history.

## Interpretation
The feed reports separate scenarios: without_position is BUY or HOLD; with_position is SELL or HOLD. Actual holdings are unknown. Signals use a20-period prior-high entry channel and10-period prior-low exit channel. Open candles are excluded. Market data comes from MEXC for BTCUSDT; Astral BTCUSD is a different feed and is not represented here.

Checks target minutes7,22,37,52 UTC. GitHub may delay or skip runs. A snapshot older than30minutes is labelled stale. A failed run leaves the prior successful snapshot visible. This is neither a paper portfolio nor a trading recommendation or an executed order.
'''
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'index.html').write_text(page, encoding='utf-8')
    (destination / 'status.json').write_text(json.dumps(feed, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (destination / 'openapi.json').write_text(json.dumps(api, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (destination / 'llms.txt').write_text(llms, encoding='utf-8')
    (destination / 'robots.txt').write_text(f'User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n', encoding='utf-8')
    (destination / 'sitemap.xml').write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{xml_escape(base_url)}</loc><lastmod>{xml_escape(snapshot["observed_at"])}</lastmod></url></urlset>\n', encoding='utf-8')
    (destination / '.nojekyll').touch()
    for filename in ('styles.css', 'status.js'):
        shutil.copyfile(TEMPLATES / filename, destination / filename)
    return feed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('site-output'))
    parser.add_argument('--base-url', default=BASE_URL)
    parser.add_argument('--run-url')
    args = parser.parse_args()
    publish_site(json.loads(args.snapshot.read_text()), args.output_dir,
                 base_url=args.base_url, run_url=args.run_url)
    print('Public status site generated.')


if __name__ == '__main__':
    main()
