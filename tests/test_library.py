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
            (folder/'notes.wav').write_bytes(b'')
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

    def test_scan_finds_flac_without_original(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'LocalVoice_2026-10-26_09-00-00_1_clean.flac').write_bytes(b'')
            entries = library.scan(folder)
            self.assertEqual(len(entries), 1)
            self.assertIsNone(entries[0]['original'])

    def test_markdown_lists_bookmarks(self):
        md = library.to_markdown('LocalVoice_x_clean.flac', 'Text', [], 75, [{'t': 65, 'note': 'klausurrelevant'}])
        self.assertIn('**1:05** klausurrelevant', md)

    def test_rename_keeps_files_and_shows_everywhere(self):
        with tempfile.TemporaryDirectory() as folder:
            clean = Path(folder)/'LocalVoice_2026-10-27_08-15-00_1_clean.flac'
            clean.write_bytes(b'')
            self.assertEqual(library.title_of(clean), '27.10.2026 · 08:15')
            library.set_title(clean, '  Thermodynamik VL 3  ')
            self.assertEqual(library.title_of(clean), 'Thermodynamik VL 3')
            self.assertEqual(library.scan(folder)[0]['name'], 'Thermodynamik VL 3')
            self.assertTrue(library.to_markdown(clean, 'x', []).startswith('# Thermodynamik VL 3'))
            self.assertTrue(clean.exists())
            library.set_title(clean, '')
            self.assertEqual(library.title_of(clean), '27.10.2026 · 08:15')

    def test_fallback_single_cue(self):
        self.assertEqual(library.cues('/nope_clean.wav', 'Text', 3.), [(0., 3., 'Text')])


if __name__ == '__main__':
    unittest.main()
