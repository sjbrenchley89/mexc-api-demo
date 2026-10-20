from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from build_site import publish_site


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def snapshot():
    return {
        'mode': 'public_signal_monitor', 'symbol': 'BTCUSDT', 'interval': '1m',
        'observed_at': '2026-10-20T11:59:00+00:00',
        'last_closed_candle_ms': 1792497480000, 'price': 81607.0,
        'signals': {'without_position': 'BUY', 'with_position': 'HOLD'},
        'position_state': 'unknown', 'account_access': False, 'live': False,
    }


class SiteTests(unittest.TestCase):
    def publish(self, value, folder):
        return publish_site(value, Path(folder), now=NOW)

    def test_builds_an_accessible_site_and_documented_public_feed(self):
        with tempfile.TemporaryDirectory() as folder:
            self.publish(snapshot(), folder)
            base = Path(folder)
            html = (base / 'index.html').read_text()
            feed = json.loads((base / 'status.json').read_text())
            self.assertIn('81,607.00', html)
            self.assertIn('Recent snapshot', html)
            self.assertIn('<h1', html)
            self.assertIn('application/ld+json', html)
            self.assertIn('Without a position', html)
            self.assertEqual(feed['snapshot']['signals']['without_position'], 'BUY')
            self.assertEqual(feed['schema_version'], 1)
            api = json.loads((base / 'openapi.json').read_text())
            self.assertIn('/status.json', api['paths'])
            self.assertIn('https://sjbrenchley89.github.io/mexc-api-demo/', (base / 'sitemap.xml').read_text())
            for filename in ('styles.css', 'status.js', 'llms.txt', 'robots.txt', '.nojekyll'):
                self.assertTrue((base / filename).is_file(), filename)

    def test_extra_account_or_secret_fields_are_never_published(self):
        data = snapshot()
        data['api_key'] = 'private-test-key'
        data['balances'] = {'USDT': 'private-test-balance'}
        data['signals']['private_note'] = 'private-test-note'
        with tempfile.TemporaryDirectory() as folder:
            self.publish(data, folder)
            all_text = '\n'.join(p.read_text() for p in Path(folder).rglob('*') if p.is_file())
            self.assertNotIn('private-test-', all_text)
            self.assertNotIn('api_key', json.loads((Path(folder) / 'status.json').read_text())['snapshot'])

    def test_nonpublic_or_invalid_snapshots_are_rejected(self):
        for key, value in [('live', True), ('account_access', True), ('price', float('nan')),
                           ('symbol', 'private-account'), ('observed_at', '<script>')]:
            data = snapshot()
            data[key] = value
            with self.subTest(field=key), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    self.publish(data, folder)
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_stale_data_is_labelled_in_the_server_rendered_page(self):
        data = snapshot()
        data['observed_at'] = '2026-10-20T10:00:00+00:00'
        data['last_closed_candle_ms'] = 1792490340000
        with tempfile.TemporaryDirectory() as folder:
            self.publish(data, folder)
            self.assertIn('Stale snapshot', (Path(folder) / 'index.html').read_text())

    def test_future_timestamps_and_invalid_signals_are_rejected(self):
        for change in ('future', 'signal'):
            data = snapshot()
            if change == 'future':
                data['observed_at'] = '2026-10-21T11:59:00+00:00'
            else:
                data['signals']['without_position'] = 'EXECUTE'
            with self.subTest(case=change), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    self.publish(data, folder)


if __name__ == '__main__':
    unittest.main()
