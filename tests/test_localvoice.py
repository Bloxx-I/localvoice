import os
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


import engine
native_only = unittest.skipUnless(engine.NATIVE_DF, 'misst das native DeepFilterNet (macOS/Linux)')


class AudioTests(unittest.TestCase):
    @native_only
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

    @native_only
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

    def test_play_starts_when_original_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            clean = Path(directory)/'clean.wav'
            with wave.open(str(clean),'wb') as w:
                w.setparams((1,4,48000,0,'NONE','not compressed'))
                w.writeframes(pcm(np.full(1000,.2,np.float32)))
            player = ComparisonPlayer()
            player.load(clean, Path(directory)/'missing.wav')
            self.assertEqual(len(player.clean), 1000)
            self.assertEqual(len(player.raw), len(player.clean))
            self.assertIn('verbesserte Aufnahme', player.error)

    def test_playback_reads_when_libsndfile_seek_fails(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import soundfile as sf
        sine = (0.4*np.sin(np.linspace(0, 40*np.pi, 24000))).astype(np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'clean.wav'
            with wave.open(str(path), 'wb') as writer:
                writer.setparams((1, 4, 48000, 0, 'NONE', 'not compressed'))
                writer.writeframes(pcm(sine))
            real_seek, real_info = sf.SoundFile.seek, sf.info

            def broken(*_a, **_k):
                raise RuntimeError('Internal psf_fseek() failed.')

            sf.SoundFile.seek = broken
            sf.info = broken
            try:
                audio, rate = ComparisonPlayer.read(path)
            finally:
                sf.SoundFile.seek, sf.info = real_seek, real_info
        self.assertEqual(rate, 48000)
        self.assertGreater(np.corrcoef(audio[:len(sine)], sine)[0, 1], .99)


if __name__ == '__main__': unittest.main()
