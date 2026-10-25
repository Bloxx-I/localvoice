import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]/'src'
SHA = 'abc1234def5678'


def package():
    # Like a GitHub archive: everything below one top-level folder.
    top = f'localvoice-{SHA}/'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr(top+'src/combined.py', '# neue Version\n')
        z.writestr(top+'src/updater.py', (SRC/'updater.py').read_text(encoding='utf-8'))
        z.writestr(top+'README.md', 'neu')
        z.writestr(top+'preferences.json', '{"gefährlich": true}')   # must never be copied
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/commits/main':
            body = json.dumps({'sha': SHA, 'commit': {'message': 'Neue Funktion\n\nDetails',
                                                      'committer': {'date': '2026-10-26T10:00:00Z'}}}).encode()
        elif self.path == f'/zip/{SHA}':
            body = package()
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.root = Path(tempfile.mkdtemp())
        (self.root/'src').mkdir()
        shutil.copy(SRC/'updater.py', self.root/'src'/'updater.py')
        (self.root/'src'/'combined.py').write_text('# alte Version\n')
        (self.root/'preferences.json').write_text('{"server_token": "meins"}')
        (self.root/'Aufnahmen').mkdir()
        (self.root/'Aufnahmen'/'vorlesung_clean.flac').write_bytes(b'audio')
        (self.root/'version.json').write_text(json.dumps({'commit': 'alt000'}))

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()
        shutil.rmtree(self.root)

    def run_updater(self, *args, url=None):
        url = url or self.url
        env = dict(os.environ, LOCALVOICE_NO_RESTART='1', LOCALVOICE_UPDATE_API=url, LOCALVOICE_UPDATE_DOWNLOAD=url+'/zip')
        return subprocess.run([sys.executable, str(self.root/'src'/'updater.py'), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_check_reports_new_version(self):
        info = json.loads(self.run_updater('--check').stdout)
        self.assertTrue(info['available'])
        self.assertEqual((info['current'], info['latest']['commit']), ('alt000', SHA[:7]))
        self.assertEqual(info['latest']['subject'], 'Neue Funktion')

    def test_install_replaces_program_but_keeps_user_data(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass']); dead.wait()
        result = self.run_updater('--install', '--pid', str(dead.pid))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('neue Version', (self.root/'src'/'combined.py').read_text())
        self.assertEqual(json.loads((self.root/'version.json').read_text())['commit'], SHA[:7])
        self.assertFalse(json.loads(self.run_updater('--check').stdout)['available'])
        self.assertEqual(json.loads((self.root/'preferences.json').read_text()), {'server_token': 'meins'})
        self.assertEqual((self.root/'Aufnahmen'/'vorlesung_clean.flac').read_bytes(), b'audio')
        self.assertIn('Update fertig', (self.root/'Logs'/'update.log').read_text(encoding='utf-8'))

    def test_unreachable_github_is_reported(self):
        result = self.run_updater('--check', url='http://127.0.0.1:9')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nicht erreichbar', result.stderr)


if __name__ == '__main__':
    unittest.main()
