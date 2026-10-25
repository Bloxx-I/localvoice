"""Build the listening track (DeepFilterNet + automatic level) from a board recording.

The source is never modified. Any rate/channel count; output is FLAC 24 bit, 48 kHz, mono.
"""
import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from engine import Enhancer, FRAME, RATE


PROFILES = {18: 'Natürlich', 30: 'Ausgewogen', 45: 'Mehr_Ruhe'}


def source_metadata(source):
    for name in (source.name.replace('_input1_native.wav', '.json'), source.name.replace('_original.wav', '.json')):
        candidate = source.with_name(name)
        if candidate != source and candidate.exists():
            try:
                return json.loads(candidate.read_text())
            except (OSError, ValueError):
                return {}
    return {}


def enhance(source, destination=None, attenuation=18, stem=None, channel=0):
    """stem=None creates a new, separately listed version; a stem writes <stem>_clean.flac."""
    source = Path(source).resolve()
    folder = Path(destination) if destination else source.parent
    folder.mkdir(parents=True, exist_ok=True)
    if stem is None:
        profile = PROFILES.get(int(attenuation), f'{attenuation:g}dB')
        stem = 'LocalVoice_'+datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')+'_'+profile
    clean_path = folder/(stem+'_clean.flac')
    model = Enhancer(1, attenuation=attenuation)
    started = time.perf_counter()
    samples = 0
    try:
        with sf.SoundFile(str(source)) as src, sf.SoundFile(str(clean_path), 'w', samplerate=RATE,
                channels=1, subtype='PCM_24', format='FLAC') as clean:
            if channel != 'mix' and int(channel) >= src.channels:
                raise ValueError('Der gewählte Kanal fehlt in der Aufnahme.')
            resampler = None if src.samplerate == RATE else soxr.ResampleStream(src.samplerate, RATE, 1, dtype='float32', quality='HQ')
            pending = np.empty(0, np.float32)

            def run(x, flush=False):
                nonlocal pending, samples
                pending = np.concatenate((pending, x))
                while len(pending) >= FRAME or (flush and len(pending)):
                    n = min(FRAME, len(pending))
                    frame = np.pad(pending[:n], (0, FRAME-n))
                    pending = pending[n:]
                    y, _ = model.process([frame])
                    clean.write(y[:n])
                    samples += n

            for block in src.blocks(blocksize=1 << 15, dtype='float32', always_2d=True):
                x = block.mean(axis=1) if channel == 'mix' else block[:, int(channel)]
                x = np.ascontiguousarray(x, dtype=np.float32)
                run(resampler.resample_chunk(x) if resampler else x)
            if resampler:
                run(resampler.resample_chunk(np.empty(0, np.float32), last=True))
            run(np.empty(0, np.float32), flush=True)
            tail_frames = model.delay_samples//FRAME+1
            for _ in range(tail_frames):
                y, _ = model.process([np.zeros(FRAME, np.float32)])
                clean.write(y)
                samples += FRAME
        inherited = source_metadata(source)
        metadata = {**inherited, 'source': source.name, 'model': 'DeepFilterNet3',
            'attenuation_limit_db': attenuation, 'auto_gain': True, 'format': 'FLAC 24 bit',
            'model_delay_samples': model.delay_samples, 'model_tail_ms': tail_frames*10,
            'duration_seconds': samples/RATE, 'elapsed_seconds': time.perf_counter()-started,
            'local_only': True, 'sample_rate': RATE,
            # A legacy 48 kHz original points to its native file; otherwise the source is the board file.
            'native_inputs': inherited.get('native_inputs') or [{'file': str(source)}]}
        (folder/(stem+'.json')).write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        return str(clean_path), str(source), metadata
    finally:
        model.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--destination')
    parser.add_argument('--attenuation', type=float, default=18)
    parser.add_argument('--stem', help='gleiche Aufnahme fortführen statt neue Version anlegen')
    parser.add_argument('--channel', default='0')
    args = parser.parse_args()
    clean, source, metadata = enhance(args.source, args.destination, args.attenuation, args.stem,
                                      args.channel if args.channel == 'mix' else int(args.channel))
    print(json.dumps({'clean': clean, 'source': source, **metadata}, ensure_ascii=False))
