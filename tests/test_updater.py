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
TOKEN = 'x'*43


def package(commit):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('src/combined.py', f'# neue Version {commit}\n')
        z.writestr('src/updater.py', (SRC/'updater.py').read_text(encoding='utf-8'))
        z.writestr('README.md', 'neu')
        z.writestr('preferences.json', '{"gefährlich": true}')   # must never be copied
        z.writestr('version.json', json.dumps({'commit': commit}))
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Authorization') != f'Bearer {TOKEN}':
            self.send_response(401); self.end_headers(); return
        body = json.dumps({'commit': 'neu123', 'subject': 'Test'}).encode() if self.path == '/v1/update' else package('neu123')
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

    def run_updater(self, *args, token=TOKEN):
        env = dict(os.environ, LOCALVOICE_NO_RESTART='1')
        return subprocess.run([sys.executable, str(self.root/'src'/'updater.py'), '--url', self.url, '--token', token, *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_check_reports_new_version(self):
        info = json.loads(self.run_updater('--check').stdout)
        self.assertTrue(info['available'])
        self.assertEqual((info['current'], info['latest']['commit']), ('alt000', 'neu123'))

    def test_install_replaces_program_but_keeps_user_data(self):
        dead = subprocess.Popen([sys.executable, '-c', 'pass']); dead.wait()
        result = self.run_updater('--install', '--pid', str(dead.pid))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('neue Version neu123', (self.root/'src'/'combined.py').read_text())
        self.assertEqual(json.loads((self.root/'version.json').read_text())['commit'], 'neu123')
        self.assertEqual(json.loads((self.root/'preferences.json').read_text()), {'server_token': 'meins'})
        self.assertEqual((self.root/'Aufnahmen'/'vorlesung_clean.flac').read_bytes(), b'audio')
        self.assertIn('Update fertig', (self.root/'Logs'/'update.log').read_text(encoding='utf-8'))

    def test_wrong_token_is_reported(self):
        result = self.run_updater('--check', token='falsch')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Token ungültig', result.stderr)


if __name__ == '__main__':
    unittest.main()
