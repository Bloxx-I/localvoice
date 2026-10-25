import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0, str(SRC))


class EncodingTests(unittest.TestCase):
    def test_worker_output_is_utf8_even_without_utf8_mode(self):
        # On Windows, pipes default to a legacy code page; the app decodes UTF-8.
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONUTF8', 'PYTHONIOENCODING')}
        out = subprocess.run([sys.executable, '-c', "import transcribe; transcribe.emit(text='Länder über schön · ß')"],
                             cwd=SRC, env=env, capture_output=True, check=True).stdout
        self.assertEqual(json.loads(out.decode('utf-8'))['text'], 'Länder über schön · ß')

    def test_transcript_files_round_trip_umlauts(self):
        import library
        with tempfile.TemporaryDirectory() as folder:
            clean = Path(folder)/'LocalVoice_2026-01-01_10-00-00_1_clean.flac'
            clean.write_bytes(b'')
            clean.with_suffix('.txt').write_text('Grüße aus Köln', encoding='utf-8')
            clean.with_suffix('.transcript.json').write_text(json.dumps(
                {'segments': [{'start': 0, 'end': 2, 'text': 'Grüße aus Köln'}]}, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(library.scan(folder)[0]['text'], 'Grüße aus Köln')
            self.assertEqual(library.cues(clean)[0][2], 'Grüße aus Köln')


if __name__ == '__main__':
    unittest.main()
