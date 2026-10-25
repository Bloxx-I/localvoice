"""Local USB audio capture. No networking, DSP control or firmware writes."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parent.parent


def usb_info():
    """Read descriptors without opening or resetting the USB device."""
    if sys.platform != 'darwin':
        return {'status': 'USB-Details sind unter macOS verfügbar.'}
    try:
        import plistlib
        result = subprocess.run(['/usr/sbin/ioreg', '-a', '-r', '-c', 'IOUSBHostDevice'],
                                capture_output=True, timeout=8, check=True)
        for item in plistlib.loads(result.stdout):
            if item.get('idVendor') == 0x2886 and item.get('idProduct') == 0x0019:
                version = int(item.get('bcdDevice', 0))
                return {'name': item.get('USB Product Name', 'ReSpeaker Lite'),
                        'usb_id': '2886:0019', 'bcdDevice': f'0x{version:04x}',
                        'serial': item.get('USB Serial Number', ''),
                        'note': 'bcdDevice ist die USB-Geräteversion; keine separate Firmware-Abfrage.'}
        return {'status': 'Kein ReSpeaker Lite in den USB-Deskriptoren gefunden.'}
    except Exception as exc:
        return {'status': f'USB-Details nicht lesbar: {exc}'}


def audio_devices():
    return [dict(d, index=i) for i, d in enumerate(sd.query_devices())]


def levels(data):
    samples = data.astype(np.float64) / 2147483648.0
    rms = np.sqrt(np.mean(samples * samples, axis=0))
    peak = np.max(np.abs(samples), axis=0)
    return np.maximum(-96, 20 * np.log10(np.maximum(rms, 1e-12))), peak


class Capture:
    def __init__(self):
        self.stream = None
        self.lock = threading.RLock()
        self.blocks = queue.Queue(maxsize=128)
        self.worker = None
        self.file = None
        self.path = None
        self.channels = 0
        self.rate = 16000
        self.record_frames = 0
        self.frames = 0
        self.dropped = 0
        self.warnings = set()
        self.waveform = np.zeros((1, 1), dtype=np.float32)
        self.rms = np.array([-96.])
        self.peak = np.array([0.])
        self.peak_hold = np.array([0.])
        self.device = None
        self.failure = None

    def start(self, device, channels, rate=16000):
        self.stop()
        self.channels, self.rate, self.device = channels, rate, dict(sd.query_devices(device))
        from coreaudio_rates import supports_rate
        if supports_rate(self.device['name'], rate) is False:
            raise ValueError(f"{self.device['name']}: {rate} Hz sind keine unterstützte Hardware-Abtastrate. Bitte native Rate wählen.")
        sd.check_input_settings(device=device, channels=channels, samplerate=rate, dtype='int32')
        self.blocks = queue.Queue(maxsize=128)
        self.rms = np.full(channels, -96.)
        self.peak = np.zeros(channels)
        self.peak_hold = np.zeros(channels)
        self.waveform = np.zeros((1, channels), dtype=np.float32)
        self.frames = self.dropped = 0
        self.warnings = set()
        self.failure = None
        self.worker = threading.Thread(target=self._consume, name='WAV writer', daemon=True)
        self.worker.start()
        try:
            self.stream = sd.InputStream(device=device, channels=channels, samplerate=rate,
                                         dtype='int32', blocksize=1024, callback=self._callback,
                                         extra_settings=sd.CoreAudioSettings(fail_if_conversion_required=True) if sys.platform=='darwin' else None)
            self.stream.start()
        except Exception:
            self.stop()
            raise

    def _callback(self, data, frames, timing, status):
        # Never perform disk I/O or GUI work on the real-time callback.
        if status:
            self.warnings.add(str(status))
        try:
            self.blocks.put_nowait(data.copy())
        except queue.Full:
            self.dropped += frames

    def _consume(self):
        while True:
            block = self.blocks.get()
            try:
                if block is None:
                    return
                rms, peak = levels(block)
                with self.lock:
                    self.rms, self.peak = rms, peak
                    self.peak_hold = np.maximum(self.peak_hold, peak)
                    self.waveform = block[::max(1, len(block)//480)].astype(np.float32) / 2147483648
                    self.frames += len(block)
                    if self.file is not None:
                        self.file.writeframesraw(block.astype('<i4', copy=False).tobytes())
                        self.record_frames += len(block)
            except Exception as exc:
                self.failure = str(exc)
            finally:
                self.blocks.task_done()

    @property
    def active(self):
        return self.stream is not None and self.stream.active

    @property
    def recording(self):
        return self.file is not None

    def begin_recording(self, directory=ROOT / 'recordings'):
        if not self.active:
            raise RuntimeError('Bitte zuerst die Pegelanzeige starten.')
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        with self.lock:
            if self.file is not None:
                raise RuntimeError('Eine Aufnahme läuft bereits.')
            self.path = directory / f"reSpeaker_{time.strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:6]}.wav"
            f = wave.open(str(self.path), 'wb')
            f.setnchannels(self.channels)
            f.setsampwidth(4)
            f.setframerate(self.rate)
            self.file = f
            self.record_frames = 0
            self.record_dropped_start = self.dropped
            self.record_warning_start = set(self.warnings)
        return self.path

    def end_recording(self):
        with self.lock:
            if self.file is None:
                return None
            self.file.close()
            self.file = None
            metadata = {'file': self.path.name, 'sample_rate': self.rate,
                        'channels': self.channels, 'format': 'PCM signed 32-bit little endian',
                        'duration_seconds': self.record_frames / self.rate,
                        'device': self.device, 'dropped_frames': self.dropped - self.record_dropped_start,
                        'stream_warnings_session': sorted(self.warnings.copy()), 'writer_error': self.failure,
                        'processing': 'USB-Signal unverändert gespeichert; Board-DSP gemäß Firmware. Keine Softwarefilter.',
                        'channel_mapping': 'USB-Kanäle; nicht als einzelne rohe Mikrofone identifiziert.'}
            self.path.with_suffix('.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
            return self.path

    def snapshot(self):
        with self.lock:
            return {'rms': self.rms.copy(), 'peak': self.peak.copy(),
                    'peak_hold': self.peak_hold.copy(), 'waveform': self.waveform.copy(),
                    'frames': self.frames, 'record_frames': self.record_frames,
                    'dropped': self.dropped, 'warnings': sorted(self.warnings.copy()), 'failure': self.failure}

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
            finally:
                self.stream.close()
                self.stream = None
        if self.worker is not None:
            self.blocks.put(None)
            self.worker.join()
            self.worker = None
        return self.end_recording()


def read_wav(path):
    with wave.open(str(path), 'rb') as f:
        if f.getsampwidth() != 4:
            raise ValueError('Erwartet eine eigene PCM32-Aufnahme.')
        rate, channels = f.getframerate(), f.getnchannels()
        # Playback loads only user-selected recordings up to ten minutes.
        if f.getnframes() > rate * 600:
            raise ValueError('Für Dateien über 10 Minuten bitte einen externen Audioplayer verwenden.')
        samples = np.frombuffer(f.readframes(f.getnframes()), dtype='<i4').reshape(-1, channels)
    return samples.astype(np.float32) / 2147483648.0, rate


def output_samples(samples, source_rate, device, gain=.25):
    """Adapt to output rate; Mac speakers often cannot open at 16 kHz."""
    info = sd.query_devices(device, 'output')
    rate = int(info['default_samplerate'])
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    if source_rate != rate:
        n = max(1, round(len(data) * rate / source_rate))
        positions = np.arange(n) * source_rate / rate
        data = np.column_stack([np.interp(positions, np.arange(len(data)), data[:, ch])
                                for ch in range(data.shape[1])]).astype(np.float32)
    max_ch = int(info['max_output_channels'])
    if max_ch < data.shape[1]:
        data = np.mean(data, axis=1, keepdims=True)
    sd.check_output_settings(device=device, channels=data.shape[1], samplerate=rate, dtype='float32')
    sd.play(np.clip(data * gain, -1, 1), samplerate=rate, device=device, blocking=False)
    return len(data) / rate
