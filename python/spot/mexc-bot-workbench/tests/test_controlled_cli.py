import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import socket
import subprocess
import sys
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

import workbench


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = workbench.main(['--data-dir', self.temp.name, *args])
        return code, output.getvalue()

    def test_status_is_paused_and_paper_only_without_network(self):
        code, output = self.call('status', '--json')
        status = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(status['paused'])
        self.assertFalse(status['live'])
        self.assertEqual(status['cash'], '50')

    def test_cli_controls_share_persistent_state_and_kill_cannot_resume(self):
        self.assertEqual(self.call('resume')[0], 0)
        self.assertFalse(json.loads(self.call('status', '--json')[1])['paused'])
        self.assertEqual(self.call('kill')[0], 0)
        self.assertEqual(self.call('resume')[0], 1)
        self.assertEqual(self.call('rearm', '--confirm', 'REARM')[0], 1)
        self.assertEqual(self.call('review', '--note', 'Reviewed the cause and checked all settings.')[0], 0)
        self.assertEqual(self.call('rearm', '--confirm', 'REARM')[0], 0)
        self.assertEqual(json.loads(self.call('status', '--json')[1])['locks'], [])

    def test_init_cannot_create_an_account_above_50(self):
        code, _ = self.call('init', '--initial-cash', '1000')
        self.assertEqual(code, 1)
        self.assertFalse((Path(self.temp.name) / 'controlled-paper.sqlite3').exists())

    def test_no_live_command_exists(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.call('live')
        self.assertEqual(error.exception.code, 2)

    def test_worker_health_tracks_shared_controls_without_exposing_portfolio(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        bootstrap = '''
from decimal import Decimal as D
import workbench
from controlled_paper import MarketEvent
class Market:
    def snapshot(self,symbol,now):
        return MarketEvent(symbol,D('100'),D('1'),False,False,now,now//900000*900000-900000)
    def close(self): pass
workbench.PublicMarket = Market
raise SystemExit(workbench.main(['run']))
'''
        proc = subprocess.Popen([sys.executable,'-u','-c',bootstrap],
                                cwd=Path(workbench.__file__).resolve().parent,
                                env={'PORT':str(port),'WORKBENCH_DATA_DIR':self.temp.name},
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            health = None
            deadline = time.monotonic()+8
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    with urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                        health = json.load(response)
                        break
                except (URLError,OSError):
                    time.sleep(0.05)
            self.assertIsNotNone(health)
            self.assertEqual(health['status'],'ok')
            self.assertTrue(health['paused'])
            self.assertFalse(health['live'])
            self.assertNotIn('cash',health)
            self.assertNotIn('positions',health)
            self.call('kill')
            with urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                self.assertIn('KILL_SWITCH',json.load(response)['locks'])
            proc.terminate()
            proc.communicate(timeout=5)
            self.assertEqual(proc.returncode,0)
            self.assertIn('KILL_SWITCH',json.loads(self.call('status','--json')[1])['locks'])
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
