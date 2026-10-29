import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import lecture as L

RATE = 16000


def speech_like(seconds, level, rng):
    t = np.arange(int(seconds*RATE))/RATE
    envelope = (np.sin(2*np.pi*3*t) > -.2).astype(np.float32)
    return (level*envelope*np.sin(2*np.pi*220*t)*(1+.3*rng.normal(size=len(t)))).astype(np.float32)


class LecturePipelineTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(5)
        noise = lambda s: (.002*self.rng.normal(size=int(s*RATE))).astype(np.float32)
        # 4 s talk, 1.5 s pause, 30 s talk, 2 s pause, 3 s talk – quiet speaker, ~-40 dBFS.
        parts = [speech_like(4, .01, self.rng), noise(1.5), speech_like(30, .01, self.rng), noise(2), speech_like(3, .01, self.rng)]
        self.x = np.concatenate(parts)+noise(sum(len(p) for p in parts)/RATE)

    def test_leveler_raises_quiet_speech_but_not_pauses(self):
        y = L.prepare_text_track(self.x)
        rms = lambda v: 20*np.log10(np.sqrt(np.mean(v**2)))
        talk, pause = slice(int(10*RATE), int(30*RATE)), slice(int(4.2*RATE), int(5.3*RATE))
        self.assertGreater(rms(y[talk])-rms(self.x[talk]), 10)
        # Pauses keep the speech gain; they are never boosted more than the speech itself.
        self.assertLessEqual(rms(y[pause])-rms(self.x[pause]), rms(y[talk])-rms(self.x[talk])+1)
        self.assertLessEqual(float(np.max(np.abs(y))), .97+1e-6)

    def test_leveler_keeps_block_length(self):
        leveler = L.Leveler(48000)
        for n in (480, 1, 1000):
            self.assertEqual(len(leveler.process(np.ones(n, np.float32)*.01)), n)

    def test_chunks_cut_in_pauses_and_respect_limits(self):
        y = L.prepare_text_track(self.x)
        regions = L.speech_regions(y)
        self.assertGreaterEqual(len(regions), 3)
        chunks = L.plan_chunks(regions, len(y)/RATE, y)
        for a, b in chunks:
            self.assertLessEqual(b-a, L.MAX_CHUNK_S+2*L.EDGE_S+1e-6)
        starts = [a for a, _ in chunks]
        self.assertEqual(starts, sorted(starts))
        self.assertGreaterEqual(len(chunks), 2)  # 30 s of continuous talk must be split
        gaps = L.skip_map(regions)
        self.assertTrue(any(a > 35 and b < 38 for a, b in gaps))  # the 2-s pause

    def test_read_mono_survives_libsndfile_seek_failure(self):
        """Abspielen used to die with 'Internal psf_fseek() failed' on the FLAC Hörspur.

        soundfile seeks after every read. Nachhören never does. Reading must not seek.
        """
        import soundfile as sf
        sine = (0.4*np.sin(np.linspace(0, 80*np.pi, 48000))).astype(np.float32)
        other = np.full(len(sine), 1000, np.int32)
        pcm = np.column_stack([(sine*2147483647).astype('<i4'), other]).tobytes()
        with tempfile.TemporaryDirectory() as directory:
            wav = Path(directory)/'native.wav'
            with wave.open(str(wav), 'wb') as writer:
                writer.setparams((2, 4, 16000, 0, 'NONE', 'not compressed'))
                writer.writeframes(pcm)
            flac = Path(directory)/'clean.flac'
            sf.write(flac, sine, 16000, format='FLAC', subtype='PCM_16')
            real = sf.SoundFile.seek

            def broken(self, frames, whence=os.SEEK_SET):
                raise RuntimeError('Internal psf_fseek() failed.')

            sf.SoundFile.seek = broken
            try:
                mono = L.read_mono(wav, 16000, channel=0)
                heard = L.read_mono(flac, 16000)
                envelope = L.envelope_file(flac)
                with L.PcmFile(wav) as src:
                    native = list(src.blocks(dtype='int32'))
            finally:
                sf.SoundFile.seek = real
        self.assertEqual(len(mono), len(sine))
        self.assertGreater(np.corrcoef(mono, sine)[0, 1], .99)
        self.assertEqual(len(heard), len(sine))
        self.assertGreater(np.corrcoef(heard, sine)[0, 1], .99)
        self.assertGreater(float(envelope.max()), .2)
        stacked = np.concatenate(native)
        self.assertEqual(stacked.shape, (len(sine), 2))
        self.assertTrue(np.all(stacked[:, 1] == 1000))

    def test_paragraphs_break_on_long_pause(self):
        text = L.paragraphs([{'start': 0, 'end': 3, 'text': 'Eins.'}, {'start': 3.5, 'end': 5, 'text': 'Zwei.'},
                             {'start': 9, 'end': 10, 'text': 'Drei.'}])
        self.assertEqual(text, 'Eins. Zwei.\n\nDrei.')


if __name__ == '__main__':
    unittest.main()
