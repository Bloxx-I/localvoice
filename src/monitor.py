"""Bounded live headphone output, resampled to the real output rate."""
import queue
import numpy as np
import sounddevice as sd
import soxr


class LiveMonitor:
    def __init__(self, device=None, volume=.35):
        self.rate = int(sd.query_devices(device, 'output')['default_samplerate'])
        self.volume = volume
        self.queue = queue.Queue(maxsize=40)
        self.pending = np.empty(0, np.float32)
        self.resampler = None if self.rate == 48000 else soxr.ResampleStream(48000, self.rate, 1, dtype='float32', quality='HQ')
        self.error = None
        self.started = False
        self.stream = sd.OutputStream(device=device, samplerate=self.rate, channels=1,
            dtype='float32', blocksize=max(1, self.rate//100), latency=.05, callback=self.callback)

    def push(self, signal):
        data = self.resampler.resample_chunk(signal) if self.resampler else signal
        if not len(data):
            return
        try:
            self.queue.put_nowait(data.copy())
        except queue.Full:
            self.error = 'Live-Abhören kommt nicht nach; Aufnahme läuft weiter.'
            return
        if not self.started and self.queue.qsize() >= 3:
            self.stream.start()
            self.started = True

    def callback(self, out, frames, timing, status):
        out[:] = 0
        if status:
            self.error = f'Live-Abhören: {status}'
        while len(self.pending) < frames:
            try:
                self.pending = np.concatenate((self.pending, self.queue.get_nowait()))
            except queue.Empty:
                break
        n = min(frames, len(self.pending))
        out[:n, 0] = np.clip(self.pending[:n] * self.volume, -1, 1)
        self.pending = self.pending[n:]

    def close(self):
        self.stream.close()
