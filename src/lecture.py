"""Lecture signal chain: text track preparation, pause detection, chunk planning.

Pure numpy/scipy so it runs the same on macOS and Windows and is unit-testable.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

ASR_RATE = 16000
FRAME_S = .02            # analysis hop for pause detection
MIN_PAUSE_S = .4         # silence that may separate two chunks
MIN_CHUNK_S = 3.
TARGET_CHUNK_S = 15.
MAX_CHUNK_S = 25.
EDGE_S = .2              # context kept around every cut


class Leveler:
    """Automatic input gain: brings quiet, distant speakers up without pumping noise.

    Streaming and length-preserving: process() takes consecutive blocks of any size
    (e.g. 10-ms recorder frames). The speech level is only tracked on blocks clearly
    above the running noise floor, so pauses keep the gain instead of raising the noise.
    """

    def __init__(self, rate, target_db=-23., max_gain_db=18., min_gain_db=-6.):
        self.rate = rate
        self.target = 10 ** (target_db/20)
        self.max_gain = 10 ** (max_gain_db/20)
        self.min_gain = 10 ** (min_gain_db/20)
        self.level = None
        self.floor = None
        self.gain = 1.

    @staticmethod
    def _follow(current, value, seconds, tau):
        return current + (1-np.exp(-seconds/tau))*(value-current)

    def process(self, x):
        x = np.asarray(x, np.float32)
        if not len(x):
            return x
        seconds = len(x)/self.rate
        rms = float(np.sqrt(np.mean(x.astype(np.float64)**2)))+1e-9
        # Noise floor: falls fast (0.5 s), rises slowly (8 s) – a minimum tracker.
        self.floor = rms if self.floor is None else self._follow(self.floor, rms, seconds, .5 if rms < self.floor else 8.)
        if rms > 2*self.floor or self.level is None:
            # Speech: follow up in ~0.3 s, down in ~3 s.
            self.level = rms if self.level is None else self._follow(self.level, rms, seconds, .3 if rms > self.level else 3.)
        wanted = min(self.max_gain, max(self.min_gain, self.target/self.level))
        y = x*np.linspace(self.gain, wanted, len(x), dtype=np.float32)
        self.gain = wanted
        peak = float(np.max(np.abs(y)))
        if peak > .97:
            y *= .97/peak
        return y

    def gain_db(self):
        return 20*np.log10(self.gain)


def highpass(x, rate, cutoff=80.):
    sos = butter(2, cutoff, 'highpass', fs=rate, output='sos')
    return sosfilt(sos, x, zi=sosfilt_zi(sos)*x[0] if len(x) else None)[0].astype(np.float32) if len(x) else x


def prepare_text_track(x, rate=ASR_RATE):
    """Board signal → ASR input: rumble removed, distant speech levelled. No neural denoising."""
    x = highpass(np.asarray(x, np.float32), rate)
    leveler = Leveler(rate)
    block = int(rate*.02)
    return np.concatenate([leveler.process(x[i:i+block]) for i in range(0, len(x), block)] or [x])


def frame_db(x, rate=ASR_RATE, hop_s=FRAME_S):
    hop = int(rate*hop_s)
    n = len(x)//hop
    if not n:
        return np.full(1, -120.)
    frames = x[:n*hop].reshape(n, hop).astype(np.float64)
    return 10*np.log10(np.mean(frames**2, axis=1)+1e-12)


def speech_regions(x, rate=ASR_RATE):
    """[(start_s, end_s)] of speech, from energy with an adaptive threshold and hysteresis."""
    db = frame_db(x, rate)
    if len(db) < 3:
        return []
    floor, loud = np.percentile(db, 10), np.percentile(db, 95)
    span = max(loud-floor, 6.)
    on, off = floor+max(6., .35*span), floor+max(4., .25*span)
    active, regions, start = False, [], 0
    for i, value in enumerate(db):
        if not active and value > on:
            active, start = True, i
        elif active and value < off:
            active = False
            regions.append([start, i])
    if active:
        regions.append([start, len(db)])
    # Join gaps shorter than a real pause, drop clicks shorter than 0.15 s.
    joined = []
    for a, b in regions:
        if joined and (a-joined[-1][1])*FRAME_S < MIN_PAUSE_S:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    return [(a*FRAME_S, b*FRAME_S) for a, b in joined if (b-a)*FRAME_S >= .15]


def plan_chunks(regions, duration, x=None, rate=ASR_RATE):
    """Group speech regions into ASR chunks of MIN..MAX seconds, cut only in pauses.

    A region longer than MAX is split at its quietest frame (needs x).
    """
    pieces = []
    for a, b in regions:
        while b-a > MAX_CHUNK_S:
            cut = a+MAX_CHUNK_S*.6
            if x is not None:
                db = frame_db(x[int(a*rate):int((a+MAX_CHUNK_S)*rate)], rate)
                window = slice(int(MAX_CHUNK_S*.4/FRAME_S), len(db))
                if len(db[window]):
                    cut = a+(window.start+int(np.argmin(db[window])))*FRAME_S
            pieces.append((a, cut))
            a = cut
        pieces.append((a, b))
    # Greedy: grow a chunk until it reaches the target length, never past the maximum.
    chunks = []
    for a, b in pieces:
        if chunks and b-chunks[-1][0] <= MAX_CHUNK_S and chunks[-1][1]-chunks[-1][0] < TARGET_CHUNK_S:
            chunks[-1] = (chunks[-1][0], b)
        else:
            chunks.append((a, b))
    # A chunk that stayed shorter than the minimum joins its neighbour if that fits.
    merged = []
    for a, b in chunks:
        if merged and (b-a < MIN_CHUNK_S or merged[-1][1]-merged[-1][0] < MIN_CHUNK_S) and b-merged[-1][0] <= MAX_CHUNK_S:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(max(0., a-EDGE_S), min(duration, b+EDGE_S)) for a, b in merged]


def paragraphs(segments, gap_s=2.5):
    """Plain transcript text: one paragraph per longer pause."""
    out, last_end = [], None
    for segment in segments:
        text = segment['text'].strip()
        if not text:
            continue
        if out and last_end is not None and segment['start']-last_end < gap_s:
            out[-1] += ' '+text
        else:
            out.append(text)
        last_end = segment['end']
    return '\n\n'.join(out)


def skip_map(regions, min_gap_s=1., margin_s=.3):
    """Gaps that the listen view may jump over: [(from_s, to_s)]."""
    gaps = []
    for (a0, b0), (a1, _) in zip(regions, regions[1:]):
        if a1-b0 >= min_gap_s+2*margin_s:
            gaps.append((b0+margin_s, a1-margin_s))
    return gaps


# --- Recording files -------------------------------------------------------

def clean_files(folder):
    folder = Path(folder)
    return list(folder.glob('*_clean.flac'))+list(folder.glob('*_clean.wav'))


def stem_of(clean):
    name = Path(clean).name
    for suffix in ('_clean.flac', '_clean.wav'):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return Path(clean).stem


def metadata_path(clean):
    return Path(clean).with_name(stem_of(clean)+'.json')


def transcript_path(clean):
    return Path(clean).with_suffix('.transcript.json')


def listen_path(clean):
    return Path(clean).with_name(stem_of(clean)+'.listen.json')


def board_source(clean):
    """The unprocessed board recording behind a clean track (native > legacy original > clean)."""
    clean = Path(clean)
    try:
        meta = json.loads(metadata_path(clean).read_text())
        for entry in meta.get('native_inputs', []):
            path = Path(entry['file'] if isinstance(entry, dict) else entry)
            if not path.is_absolute():
                path = clean.parent/path
            if path.exists():
                return path
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for candidate in (clean.with_name(stem_of(clean)+'_input1_native.wav'),
                      clean.with_name(stem_of(clean)+'_original.wav')):
        if candidate.exists():
            return candidate
    return clean


def read_mono(path, rate=ASR_RATE, channel=0):
    """Whole file, one channel, resampled. Memory: ~4 bytes/sample at the target rate."""
    import soundfile as sf
    import soxr
    with sf.SoundFile(str(path)) as f:
        parts = []
        resampler = None if f.samplerate == rate else soxr.ResampleStream(f.samplerate, rate, 1, dtype='float32', quality='HQ')
        for block in f.blocks(blocksize=1 << 16, dtype='float32', always_2d=True):
            x = np.ascontiguousarray(block[:, min(channel, block.shape[1]-1)])
            parts.append(resampler.resample_chunk(x) if resampler else x)
        if resampler:
            parts.append(resampler.resample_chunk(np.empty(0, np.float32), last=True))
    return np.concatenate(parts) if parts else np.empty(0, np.float32)
