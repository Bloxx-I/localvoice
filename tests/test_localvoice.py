import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np
from scipy.signal import correlate

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from engine import Enhancer, FRAME, pcm
from playback import ComparisonPlayer


class AudioTests(unittest.TestCase):
    def test_model_causal_delay_and_frame_continuity(self):
        # A 1-dB attenuation limit preserves correlation while exercising the
        # STFT pipeline. Zero attenuation is a special direct bypass in libdf.
        rng = np.random.default_rng(17)
        x = rng.normal(0, .08, 60*FRAME).astype(np.float32)
        enhancer = Enhancer(1, attenuation=1, quality='live')
        enhancer.levelers = [None]  # measure the model alone, without automatic gain
        y = np.concatenate([enhancer.process([x[i:i+FRAME]])[0] for i in range(0,len(x),FRAME)])
        enhancer.close()
        c = correlate(y[4800:], x[4800:], method='fft')
        lag = int(np.argmax(c) - (len(x[4800:])-1))
        self.assertEqual(lag, 480)
        reference = x[4800-480:-480]
        self.assertGreater(np.corrcoef(y[4800:], reference)[0,1], .99)
        self.assertGreater(np.std(y[4800:])/np.std(reference), .85)

    def test_momentary_switch_keeps_one_position(self):
        player = ComparisonPlayer()
        player.clean = np.full(2000, .1, np.float32)
        player.raw = np.full(2000, .6, np.float32)
        player.playing = True
        out = np.zeros((480, 1), np.float32)
        player.callback(out, 480, None, None)
        self.assertAlmostEqual(float(out[-1,0]), .1, places=5)

        player.original = True
        player.callback(out, 480, None, None)
        self.assertEqual(player.position, 960)
        self.assertAlmostEqual(float(out[-1,0]), .6, places=5)
        player.original = False
        player.callback(out, 480, None, None)
        self.assertEqual(player.position, 1440)
        self.assertAlmostEqual(float(out[-1,0]), .1, places=5)

    def test_studio_model_delay_matches_original_alignment(self):
        x = np.random.default_rng(23).normal(0,.08,80*FRAME).astype(np.float32)
        enhancer = Enhancer(1,attenuation=1,quality='studio')
        enhancer.levelers = [None]
        y = np.concatenate([enhancer.process([x[i:i+FRAME]])[0] for i in range(0,len(x),FRAME)])
        c = correlate(y[4800:],x[4800:],method='fft')
        self.assertEqual(int(np.argmax(c)-(len(x[4800:])-1)),enhancer.delay_samples)
        enhancer.close()

    def test_two_microphone_models_and_missing_source(self):
        enhancer = Enhancer(2, mode='mix')
        signal = np.full(FRAME, .02, np.float32)
        for _ in range(8):
            clean, raw = enhancer.process([signal, signal], available=[True, True])
        clean, raw = enhancer.process([signal, np.zeros(FRAME,np.float32)], available=[True,False])
        self.assertEqual(clean.shape, (FRAME,))
        self.assertTrue(np.isfinite(clean).all())
        self.assertLessEqual(np.max(np.abs(clean)), .98)
        enhancer.close()

    def test_comparison_files_align_and_match_length(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/name for name in ['clean.wav','original.wav']]
            for p in paths:
                with wave.open(str(p),'wb') as w:
                    w.setparams((1,4,48000,0,'NONE','not compressed'))
                    w.writeframes(pcm(np.full(2000,.1,np.float32)))
            player = ComparisonPlayer()
            player.load(*paths)
            self.assertEqual(len(player.clean),len(player.raw))
            self.assertTrue(np.all(player.raw[:480] == 0))


if __name__ == '__main__': unittest.main()
