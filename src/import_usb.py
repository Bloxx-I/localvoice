"""Convert a USB WAV to an aligned LocalVoice pair; retain the source file."""
import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from enhance_recording import enhance
from engine import RATE


def import_recording(source, destination, channel='1', attenuation=18):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    info = sf.info(source)
    if not info.frames:
        raise ValueError('Die USB-Aufnahme ist leer.')
    if channel != 'mix' and not 1 <= int(channel) <= info.channels:
        raise ValueError('Der ausgewählte USB-Kanal fehlt.')
    with tempfile.TemporaryDirectory(prefix='usb-import-', dir=destination) as temporary:
        mono_path = Path(temporary) / 'mono48.wav'
        resampler = None if info.samplerate == RATE else soxr.ResampleStream(
            info.samplerate, RATE, 1, dtype='float32', quality='HQ')
        with sf.SoundFile(source) as src, sf.SoundFile(mono_path, 'w', samplerate=RATE,
                channels=1, subtype='PCM_32', format='WAV') as dst:
            for block in src.blocks(blocksize=16384, dtype='float32', always_2d=True):
                mono = block.mean(axis=1) if channel == 'mix' else block[:, int(channel)-1]
                mono = np.ascontiguousarray(mono)
                if resampler:
                    mono = resampler.resample_chunk(mono)
                dst.write(mono)
            if resampler:
                dst.write(resampler.resample_chunk(np.empty(0, np.float32), last=True))
        clean, raw, metadata = enhance(mono_path, destination, attenuation)
    metadata.update(source_usb=str(source), source_sample_rate=info.samplerate,
                    source_channels=info.channels, source_format=info.subtype,
                    selected_usb_channel=channel, original_track='Ausgewähltes Board-Signal, mono auf 48 kHz konvertiert; ursprüngliche USB-WAV bleibt erhalten.')
    metadata_path = Path(clean.replace('_clean.wav', '.json'))
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return {'clean': clean, 'original': raw, **metadata}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('source')
    p.add_argument('--destination', required=True)
    p.add_argument('--channel', default='1')
    p.add_argument('--attenuation', type=float, default=18)
    args = p.parse_args()
    print(json.dumps(import_recording(args.source, args.destination, args.channel, args.attenuation)))
