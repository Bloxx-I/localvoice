import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import library


class LibraryTests(unittest.TestCase):
    def test_scan_pairs_newest_first_with_text(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for stem in ('LocalVoice_2026-10-25_21-53-22_1', 'LocalVoice_2026-10-25_22-00-00_2_Natürlich'):
                (folder/f'{stem}_clean.wav').write_bytes(b'')
                (folder/f'{stem}_original.wav').write_bytes(b'')
                (folder/f'{stem}.json').write_text(json.dumps({'duration_seconds': 12.5}))
            (folder/'LocalVoice_2026-10-25_21-53-22_1_clean.txt').write_text('Hallo Welt')
            (folder/'orphan_clean.wav').write_bytes(b'')
            entries = library.scan(folder)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]['variant'], 'Natürlich')
            self.assertEqual(entries[1]['text'], 'Hallo Welt')
            self.assertEqual(entries[1]['duration'], 12.5)

    def test_live_cues_drop_overlapping_words(self):
        with tempfile.TemporaryDirectory() as folder:
            clean = Path(folder)/'x_clean.wav'
            clean.with_suffix('.live.json').write_text(json.dumps({'segments': [
                {'start': 0, 'duration': 5, 'text': 'eins zwei drei'},
                {'start': 4.5, 'duration': 5, 'text': 'drei vier fünf'}]}))
            items = library.cues(clean)
            self.assertEqual([t for _, _, t in items], ['eins zwei drei', 'vier fünf'])
            srt = library.to_srt(items)
            self.assertIn('00:00:04,500 --> 00:00:09,500\nvier fünf', srt)
            self.assertTrue(library.to_vtt(items).startswith('WEBVTT'))

    def test_fallback_single_cue(self):
        self.assertEqual(library.cues('/nope_clean.wav', 'Text', 3.), [(0., 3., 'Text')])


if __name__ == '__main__':
    unittest.main()
