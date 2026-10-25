"""Turn a saved USB WAV into a LocalVoice recording; the source file stays untouched."""
import argparse
import json
from pathlib import Path

import soundfile as sf
from enhance_recording import enhance


def import_recording(source, destination, channel='1', attenuation=18):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    info = sf.info(source)
    if not info.frames:
        raise ValueError('Die USB-Aufnahme ist leer.')
    if channel != 'mix' and not 1 <= int(channel) <= info.channels:
        raise ValueError('Der ausgewählte USB-Kanal fehlt.')
    clean, _, metadata = enhance(source, destination, attenuation,
                                 channel='mix' if channel == 'mix' else int(channel)-1)
    metadata.update(source_usb=str(source), source_sample_rate=info.samplerate,
                    source_channels=info.channels, source_format=info.subtype,
                    selected_usb_channel=channel)
    Path(clean.replace('_clean.flac', '.json')).write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return {'clean': clean, 'source': str(source), **metadata}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('source')
    p.add_argument('--destination', required=True)
    p.add_argument('--channel', default='1')
    p.add_argument('--attenuation', type=float, default=18)
    args = p.parse_args()
    print(json.dumps(import_recording(args.source, args.destination, args.channel, args.attenuation), ensure_ascii=False))
