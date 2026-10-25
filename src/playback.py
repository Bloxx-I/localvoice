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
    def read(path):
        with wave.open(str(path), 'rb') as w:
            if w.getnchannels() != 1 or w.getsampwidth() not in (2, 4):
                raise ValueError('Vergleich benötigt Mono-PCM16/PCM32-Dateien.')
            return np.frombuffer(w.readframes(w.getnframes()), dtype='<i2' if w.getsampwidth()==2 else '<i4').astype(np.float32)/(32768 if w.getsampwidth()==2 else 2147483648), w.getframerate()

    def load(self, clean, raw, delay_samples=480):
        self.close()
        self.clean, self.rate = self.read(clean)
        self.raw, rate = self.read(raw)
        if rate != self.rate:
            raise ValueError('Die Vergleichsspuren haben unterschiedliche Abtastraten.')
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
        self.error = None

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
