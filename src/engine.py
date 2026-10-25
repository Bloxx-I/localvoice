"""Offline-only, streaming microphone recorder. No network calls."""
from __future__ import annotations

import json
import queue
import threading
import time
import wave
import sys
import importlib.util
import configparser
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soxr
# The package __init__ also imports an unused ONNX runtime with telemetry.
# Load only the audited native streaming wrapper and its bundled Rust model.
_package = Path(importlib.util.find_spec('dfnstream_py').origin).parent
_spec = importlib.util.spec_from_file_location('_localvoice_df', _package / 'dfnstream.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
DeepFilterNetStreaming = _module.DeepFilterNetStreaming

from monitor import LiveMonitor

RATE = 48000
FRAME = 480


def rms(x):
    return float(np.sqrt(np.mean(np.square(x), dtype=np.float64))) if len(x) else 0.0


def pcm(x):
    return (np.clip(np.nan_to_num(x), -1, 1) * 2147483647).astype('<i4').tobytes()


@dataclass
class InputSpec:
    device: int
    name: str
    channel: int = 0
    gain_db: float = 0.0
    delay_ms: int = 0
    sample_rate: int | None = None


class Enhancer:
    def __init__(self, count, attenuation=20.0, mode='auto', specs=None, quality='studio', wet=1.0):
        self.models = []
        model_path = Path(__file__).resolve().parent.parent/'models/DeepFilterNet3_onnx.tar.gz' if quality == 'studio' else _package/'models/DeepFilterNet3_ll_onnx.tar.gz'
        config = configparser.ConfigParser()
        with tarfile.open(model_path) as archive:
            config.read_string(archive.extractfile(next(n for n in archive.getnames() if n.endswith('config.ini'))).read().decode())
        self.delay_samples = config.getint('df','fft_size') - config.getint('df','hop_size') + config.getint('deepfilternet','conv_lookahead')*FRAME
        self.wet = wet
        try:
            for _ in range(count):
                self.models.append(DeepFilterNetStreaming(
                    model_path=str(model_path), atten_lim=attenuation, compensate_delay=False, log_level='error'))
        except BaseException:
            self.close()
            raise
        if any(m.frame_length != FRAME for m in self.models):
            self.close()
            raise RuntimeError('Das KI-Modell hat eine unerwartete Blockgröße.')
        self.mode = mode
        self.specs = specs or [InputSpec(i, str(i)) for i in range(count)]
        self.delays = [np.zeros(s.delay_ms * RATE // 1000, np.float32) for s in self.specs]
        self.dry_delays = [np.zeros(self.delay_samples,np.float32) for _ in self.specs]
        self.scores = np.full(count, -30.0)
        self.active = 0
        self.hold = 0
        self.weights = np.eye(count, dtype=np.float32)[0]
        self.clip_count = 0

    def process(self, frames, available=None):
        available = available if available is not None else [True] * len(frames)
        raw, clean = [], []
        for i, (frame, model, spec) in enumerate(zip(frames, self.models, self.specs)):
            x = np.asarray(frame, dtype=np.float32) * np.float32(10 ** (spec.gain_db / 20))
            self.clip_count += int(np.count_nonzero(np.abs(x) >= 1))
            x = np.clip(x, -1, 1)
            n = len(self.delays[i])
            if n:
                joined = np.concatenate((self.delays[i], x))
                x, self.delays[i] = joined[:FRAME], joined[FRAME:]
            y, snr = model.process_frame(np.ascontiguousarray(x))
            joined_dry = np.concatenate((self.dry_delays[i], x))
            aligned_dry, self.dry_delays[i] = joined_dry[:FRAME], joined_dry[FRAME:]
            y = y*self.wet + aligned_dry*(1-self.wet)
            self.scores[i] = .95 * self.scores[i] + .05 * (float(snr) if available[i] else -100)
            raw.append(x)
            clean.append(y)
        old = self.weights.copy()
        if self.mode == 'mix':
            target = np.asarray(available, dtype=np.float32)
            target /= max(1, target.sum())
        else:
            best = int(np.argmax(self.scores))
            if not available[self.active] or (self.hold <= 0 and self.scores[best] > self.scores[self.active] + 4):
                self.active = best
                self.hold = 30
            self.hold -= 1
            target = np.eye(len(frames), dtype=np.float32)[self.active]
        self.weights = .5 * old + .5 * target
        ramp = np.linspace(0, 1, FRAME, dtype=np.float32)[:, None]
        weights = old[None, :] * (1-ramp) + self.weights[None, :] * ramp
        enhanced = np.sum(np.stack(clean, axis=1) * weights, axis=1)
        original = np.sum(np.stack(raw, axis=1) * weights, axis=1)
        # Only prevent overload; preserve voice dynamics below the limiter.
        peak = float(np.max(np.abs(enhanced)))
        if peak > .98:
            enhanced *= .98 / peak
        return enhanced.astype(np.float32), original.astype(np.float32)

    def close(self):
        for model in self.models:
            model.close()
        self.models = []


class Capture:
    def __init__(self, spec, native_path):
        from coreaudio_rates import supports_rate
        info = sd.query_devices(spec.device)
        self.rate = float(spec.sample_rate or info['default_samplerate'])
        if supports_rate(info['name'], self.rate) is False:
            raise ValueError(f"{info['name']}: {self.rate:g} Hz werden von der Hardware nicht unterstützt.")
        self.spec = spec
        self.channels = int(info['max_input_channels'])
        self.native_path = Path(native_path)
        self.native_frames = 0
        self.queue = queue.Queue(maxsize=200)
        self.pending = np.empty(0, np.float32)
        self.resampler = None if self.rate == RATE else soxr.ResampleStream(self.rate, RATE, 1, dtype='float32', quality='HQ')
        self.error = None
        self.last_callback = time.monotonic()
        self.native = None
        extra = sd.CoreAudioSettings(fail_if_conversion_required=True) if sys.platform == 'darwin' else None
        self.stream = sd.InputStream(device=spec.device, samplerate=self.rate,
            channels=self.channels, dtype='int32', blocksize=0, latency='low',
            extra_settings=extra, callback=self.callback)
        try:
            self.native = wave.open(str(native_path), 'wb')
            self.native.setparams((self.channels, 4, int(self.rate), 0, 'NONE', 'not compressed'))
        except Exception:
            self.stream.close()
            raise

    def callback(self, data, frames, timing, status):
        self.last_callback = time.monotonic()
        if status:
            self.error = f'{self.spec.name}: Audio-Unterbrechung ({status}). Aufnahme wurde beendet.'
        try:
            self.queue.put_nowait(data.copy())
        except queue.Full:
            self.error = f'{self.spec.name}: Die Verarbeitung kommt nicht nach. Aufnahme wurde beendet.'

    def append(self, x, last=False):
        if x.ndim == 2:
            self.native.writeframesraw(x.astype('<i4', copy=False).tobytes())
            self.native_frames += len(x)
            x = x[:, self.spec.channel].astype(np.float32) / 2147483648.0
        if self.resampler is not None:
            x = self.resampler.resample_chunk(x, last=last)
        if len(x):
            self.pending = np.concatenate((self.pending, x))

    def drain(self):
        while True:
            try:
                self.append(self.queue.get_nowait())
            except queue.Empty:
                break

    def take(self):
        n = min(FRAME, len(self.pending))
        x = np.zeros(FRAME, np.float32)
        x[:n] = self.pending[:n]
        self.pending = self.pending[n:]
        return x, n == FRAME

    def close(self):
        self.stream.close()
        if self.native:
            self.native.close()
            self.native = None


class Recorder:
    def __init__(self, specs, folder, attenuation=20, mode='auto', save_raw=True, wet=1.0,
                 pipeline='live', monitor_device=None, monitor=False, monitor_volume=.35,
                 segment_seconds=5):
        if not specs:
            raise ValueError('Mindestens ein Mikrofon auswählen.')
        self.specs, self.folder = specs, Path(folder)
        self.attenuation, self.mode, self.save_raw = attenuation, mode, save_raw
        self.wet, self.pipeline = wet, pipeline
        self.monitor_device, self.monitor_enabled, self.monitor_volume = monitor_device, monitor, monitor_volume
        self.segment_seconds = segment_seconds
        self.segments = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None
        self.state = {'status': 'ready', 'seconds': 0, 'levels': [0]*len(specs), 'output': 0,
                      'active': 0, 'processing_ms': 0, 'samples': 0, 'missing_frames': [0]*len(specs)}
        self.result = None
        self.error = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name='LocalVoice recorder', daemon=False)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self):
        captures, enhancer, writers, monitor = [], None, [], None
        metadata_target = None
        try:
            self.state['status'] = 'loading'
            self.folder.mkdir(parents=True, exist_ok=True)
            stem = 'LocalVoice_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')
            target = self.folder / (stem + '_clean.wav')
            raw_target = self.folder / (stem + '_original.wav')
            metadata_target = self.folder / (stem + '.json')
            if self.pipeline == 'live':
                enhancer = Enhancer(len(self.specs), self.attenuation, self.mode, self.specs, wet=self.wet)
            for i, spec in enumerate(self.specs):
                captures.append(Capture(spec, self.folder / f'{stem}_input{i+1}_native.wav'))
            for capture in captures:
                capture.stream.start()
            for path in ([target, raw_target] if enhancer else [raw_target]):
                writer = wave.open(str(path), 'wb')
                writer.setparams((1, 4, RATE, 0, 'NONE', 'not compressed'))
                writers.append(writer)
            if self.monitor_enabled:
                monitor = LiveMonitor(self.monitor_device, self.monitor_volume)
            self.state['status'] = 'recording'
            missing = [0]*len(captures)
            samples, frames_count, proc_total, peak_raw = 0, 0, 0., 0.
            segment_buffer = np.empty(0, np.float32)
            segment_index = 0
            segment_start = 0
            segment_folder = self.folder / (stem + '_live_segments')
            overlap = RATE // 2

            def save_segment(data, start):
                nonlocal segment_index
                segment_folder.mkdir(exist_ok=True)
                path = segment_folder / f'{segment_index:05}.wav'
                with wave.open(str(path), 'wb') as writer:
                    writer.setparams((1, 4, RATE, 0, 'NONE', 'not compressed'))
                    writer.writeframes(pcm(data))
                self.segments.put({'audio': str(path), 'index': segment_index, 'start': start/RATE})
                segment_index += 1

            def process_one(valid_samples=FRAME):
                nonlocal samples, frames_count, proc_total, peak_raw, segment_buffer, segment_start
                frames, available = [], []
                for i, capture in enumerate(captures):
                    x, valid = capture.take()
                    if not valid and valid_samples == FRAME:
                        missing[i] += 1
                    frames.append(x)
                    available.append(valid or valid_samples < FRAME)
                started = time.perf_counter()
                if enhancer:
                    clean, original = enhancer.process(frames, available)
                    active = enhancer.active
                    writers[0].writeframesraw(pcm(clean[:valid_samples]))
                    writers[1].writeframesraw(pcm(original[:valid_samples]))
                else:
                    original = np.mean(frames, axis=0) if self.mode == 'mix' else frames[0]
                    clean, active = original, 0
                    writers[0].writeframesraw(pcm(original[:valid_samples]))
                elapsed = (time.perf_counter()-started)*1000
                if monitor:
                    monitor.push(clean[:valid_samples])
                if self.pipeline == 'live':
                    segment_buffer = np.concatenate((segment_buffer, clean[:valid_samples]))
                    required = int(self.segment_seconds * RATE)
                    if len(segment_buffer) >= required:
                        save_segment(segment_buffer[:required], segment_start)
                        advance = required - overlap
                        segment_buffer = segment_buffer[advance:]
                        segment_start += advance
                samples += valid_samples
                frames_count += 1
                proc_total += elapsed
                peak_raw = max(peak_raw, max(float(np.max(np.abs(x))) for x in frames))
                self.state.update(seconds=samples/RATE, levels=[rms(x) for x in frames], output=rms(clean),
                    active=active, processing_ms=elapsed, samples=samples, missing_frames=missing[:],
                    monitor_error=monitor.error if monitor else None,
                    waveform_clean=clean[::12].tolist(), waveform_raw=original[::12].tolist())

            while not self.stop_event.is_set():
                for capture in captures:
                    if capture.error:
                        raise RuntimeError(capture.error)
                    if time.monotonic() - capture.last_callback > 3:
                        raise RuntimeError(f'{capture.spec.name}: Kein Audiosignal vom Gerät. Verbindung prüfen.')
                try:
                    captures[0].append(captures[0].queue.get(timeout=.1))
                except queue.Empty:
                    continue
                for capture in captures[1:]:
                    capture.drain()
                while len(captures[0].pending) >= FRAME:
                    process_one()
            for capture in captures:
                capture.stream.stop()
                capture.drain()
                capture.append(np.empty(0, np.float32), last=True)
            while len(captures[0].pending):
                process_one(min(FRAME, len(captures[0].pending)))
            tail_frames = enhancer.delay_samples//FRAME + 1 if enhancer else 0
            for capture in captures:
                capture.pending = np.zeros(tail_frames*FRAME, np.float32)
            for _ in range(tail_frames):
                process_one()
            if self.pipeline == 'live' and len(segment_buffer) > (overlap if segment_index else 0):
                save_segment(segment_buffer, segment_start)
            metadata = {'sample_rate': RATE, 'channels': 1, 'format': 'PCM32 WAV',
                'model': 'DeepFilterNet3 studio' if enhancer else None, 'pipeline': self.pipeline,
                'mode': self.mode, 'attenuation_limit_db': self.attenuation,
                'inputs': [vars(spec) for spec in self.specs], 'duration_seconds': samples/RATE,
                'native_inputs': [{'file': str(c.native_path), 'sample_rate': c.rate,
                    'channels': c.channels, 'bits':32, 'frames':c.native_frames} for c in captures],
                'average_processing_ms_per_10ms': proc_total/max(frames_count,1),
                'missing_frames': missing, 'input_peak': peak_raw, 'model_tail_ms': tail_frames*10,
                'model_delay_samples':enhancer.delay_samples if enhancer else 0,
                'wet':self.wet, 'local_only':True,
                'original_note':'Originalspur: gewählter Kanal/Mix auf 48 kHz. Unveränderte volle Eingänge separat als native WAV.'}
            metadata_target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
            self.result = {'clean': str(target) if enhancer else str(raw_target), 'original': str(raw_target),
                           'metadata': str(metadata_target), **metadata}
        except BaseException as exc:
            self.error = str(exc)
            if metadata_target:
                metadata_target.write_text(json.dumps({'error':self.error,
                    'native_inputs':[str(c.native_path) for c in captures], 'local_only':True}, indent=2))
        finally:
            for capture in captures:
                try:
                    capture.stream.stop()
                    capture.drain()
                    capture.close()
                except Exception:
                    pass
            for writer in writers:
                writer.close()
            if monitor:
                monitor.close()
            if enhancer:
                enhancer.close()
            self.state['status'] = 'error' if self.error else 'done'


def input_devices():
    return [(i, d) for i, d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0]
