"""One playback clock for clean and original. Switching never seeks."""
import threading
import wave
from pathlib import Path

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
        """Whole file, one channel. Qt first: the same FFmpeg Nachhören already plays.

        libsndfile raises ``Internal psf_fseek() failed`` on these FLAC and PCM32
        files under Windows, including while only reading forward.
        """
        import lecture
        try:
            audio, native = _qt_mono(path)
        except Exception:
            import soundfile as sf
            native = sf.info(str(path)).samplerate
            audio = lecture.read_mono(path, native)
        if rate and int(rate) != int(native) and len(audio):
            audio = soxr.resample(audio, int(native), int(rate)).astype(np.float32)
            native = int(rate)
        return audio, int(native)

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


def _mono_from_buffer(buf, channel=0):
    from PySide6.QtMultimedia import QAudioFormat
    fmt = buf.format()
    channels = max(1, fmt.channelCount())
    frames = int(buf.frameCount())
    kind = fmt.sampleFormat()
    Sample = QAudioFormat.SampleFormat
    raw = bytes(buf.constData()[:buf.byteCount()])
    if kind == Sample.Float:
        data = np.frombuffer(raw, dtype='<f4')
    elif kind == Sample.Int32:
        data = np.frombuffer(raw, dtype='<i4').astype(np.float32) / np.float32(2147483648.0)
    elif kind == Sample.Int16:
        data = np.frombuffer(raw, dtype='<i2').astype(np.float32) / np.float32(32768.0)
    elif kind == Sample.UInt8:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - np.float32(128)) / np.float32(128)
    else:
        raise RuntimeError('Unbekanntes Audiodatenformat.')
    data = np.reshape(data[:frames * channels], (frames, channels))
    return np.ascontiguousarray(data[:, min(channel, channels - 1)], dtype=np.float32), int(fmt.sampleRate())


def _qt_mono(path, channel=0):
    """Decode one channel with Qt's FFmpeg backend. Needs a running QApplication."""
    from PySide6.QtCore import QCoreApplication, QEventLoop, QUrl
    from PySide6.QtMultimedia import QAudioDecoder
    if QCoreApplication.instance() is None:
        raise RuntimeError('Qt läuft nicht.')
    decoder = QAudioDecoder()
    if not decoder.isSupported():
        raise RuntimeError('Qt kann diese Datei nicht lesen.')
    decoder.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
    parts, failed, rate = [], [], None
    loop = QEventLoop()

    def take():
        nonlocal rate
        buf = decoder.read()
        if buf is None or not buf.isValid() or not buf.frameCount():
            return
        samples, rate = _mono_from_buffer(buf, channel)
        parts.append(samples)

    decoder.bufferReady.connect(take)
    decoder.finished.connect(loop.quit)
    decoder.error.connect(lambda *_: failed.append(decoder.errorString() or 'Dekoderfehler') or loop.quit())
    decoder.start()
    if not decoder.isDecoding() and (failed or decoder.errorString()):
        raise RuntimeError(failed[-1] if failed else decoder.errorString())
    loop.exec()
    decoder.deleteLater()
    if failed and not parts:
        raise RuntimeError(failed[-1])
    if not parts or rate is None:
        raise RuntimeError('Keine Audiodaten.')
    return np.concatenate(parts), rate
