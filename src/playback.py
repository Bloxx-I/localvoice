"""One playback clock for clean and original. Switching never seeks."""
import threading
import wave

import numpy as np
import sounddevice as sd
import soxr


class ComparisonPlayer:
    def __init__(self):
        self.stream = None
        self.position = 0
        self.original = False
        self.playing = False
        self.clean = np.empty(0, np.float32)
        self.raw = np.empty(0, np.float32)
        self.error = None
        self.rate = 48000
        self.blend = 0.
        self.volume = 1.
        self.device = None

    @staticmethod
    def read(path, rate=None):
        import lecture
        import soundfile as sf
        native = sf.info(str(path)).samplerate
        return lecture.read_mono(path, rate or native), rate or native

    def load(self, clean, raw=None, delay_samples=480):
        """raw=None: the original is derived from the board recording behind clean."""
        import lecture
        self.close()
        self.clean, self.rate = self.read(clean)
        derived = raw is None
        if derived:
            raw = lecture.board_source(clean)
        fallback = None
        try:
            self.raw, rate = self.read(raw, self.rate)
        except Exception as exc:
            # Abspielen must play the improved track even if the original cannot be read.
            fallback = str(exc)
            self.raw = np.copy(self.clean)
            derived = False
        if derived:
            # Same automatic level as the listening track, so A/B compares sound, not loudness.
            self.raw = lecture.prepare_text_track(self.raw, self.rate)
        if fallback is None:
            # Match the causal STFT delay in this low-lookahead model (960 FFT,
            # 480 hop, no network lookahead). Verify this in the signal test.
            self.raw = np.pad(self.raw, (delay_samples, 0))[:len(self.clean)]
            if len(self.raw) < len(self.clean):
                self.raw = np.pad(self.raw, (0, len(self.clean)-len(self.raw)))
        self._source_clean = self.clean
        self._source_raw = self.raw
        self._source_rate = self.rate
        self.position = 0
        self.original = False
        self.blend = 0.
        self.error = ('Original nicht vergleichbar (' + fallback + '). Es läuft die verbesserte Aufnahme.') if fallback else None

    def callback(self, out, frames, timing, status):
        out[:] = 0
        if status:
            self.error = str(status)
        if not self.playing:
            return
        end = min(self.position + frames, len(self.clean))
        n = end - self.position
        if n <= 0:
            self.playing = False
            return
        target = 1. if self.original else 0.
        # Short crossfade prevents clicks; shared position preserves continuity.
        count = min(n, 240)
        blend = np.full(n, target, np.float32)
        blend[:count] = np.linspace(self.blend, target, count)
        self.blend = target
        clean = self.clean[self.position:end]
        raw = self.raw[self.position:end]
        audio = clean * (1-blend) + raw * blend
        out[:n] = audio[:, None] * getattr(self, 'volume', 1.)
        self.position = end
        if end == len(self.clean):
            self.playing = False

    def toggle(self):
        if self.stream is None:
            output_rate = int(sd.query_devices(self.device, 'output')['default_samplerate'])
            if output_rate != self.rate:
                seconds = self.position/self.rate
                clean = getattr(self, '_source_clean', self.clean)
                raw = getattr(self, '_source_raw', self.raw)
                source_rate = getattr(self, '_source_rate', self.rate)
                self.clean = soxr.resample(clean, source_rate, output_rate).astype(np.float32) if source_rate != output_rate else clean
                self.raw = soxr.resample(raw, source_rate, output_rate).astype(np.float32) if source_rate != output_rate else raw
                self.rate = output_rate
                self.position = min(int(seconds*self.rate),len(self.clean))
            sd.check_output_settings(device=self.device, samplerate=self.rate, channels=1, dtype='float32')
            self.stream = sd.OutputStream(device=self.device, samplerate=self.rate, channels=1,
                dtype='float32', blocksize=480, callback=self.callback)
            self.stream.start()
        if self.position >= len(self.clean):
            self.position = 0
        self.playing = not self.playing

    def seek(self, seconds):
        self.position = min(max(int(seconds * self.rate), 0), len(self.clean))

    def close(self):
        self.playing = False
        if self.stream:
            self.stream.close()
            self.stream = None
