"""Offline Qwen3-ASR worker, one process per recording."""
import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

for key,value in {'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
                  'HF_HUB_DISABLE_TELEMETRY':'1','TOKENIZERS_PARALLELISM':'false'}.items():
    os.environ[key] = value

# Fail closed if a dependency accidentally attempts an outbound connection.
def no_network(*args, **kwargs):
    raise RuntimeError('Die lokale Transkription erlaubt keine Netzwerkverbindung.')
socket.socket.connect = no_network
socket.socket.connect_ex = no_network


def emit(**event):
    print(json.dumps(event,ensure_ascii=False),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('audio')
    parser.add_argument('--model', required=True)
    parser.add_argument('--language', default='de')
    args = parser.parse_args()
    model_path = Path(args.model).resolve()
    audio_path = Path(args.audio).resolve()
    if not (model_path/'.ready.json').exists():
        raise RuntimeError('Qwen3-ASR-Download ist noch nicht vollständig.')
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    import soxr
    from mlx_audio.stt.utils import load_model
    emit(status='Qwen3-ASR wird auf diesem Mac geladen …')
    mx.set_cache_limit(256*1024*1024)
    model = load_model(model_path, strict=True)
    signal, rate = sf.read(audio_path, dtype='float32', always_2d=True)
    signal = signal.mean(axis=1)
    if rate != 16000:
        signal = soxr.resample(signal, rate, 16000)
    segments = []
    parts = []
    duration = len(signal)/16000
    started = time.monotonic()
    chunk_samples = 20*16000
    count = max(1,(len(signal)+chunk_samples-1)//chunk_samples)
    target = audio_path.with_suffix('.txt')
    partial = target.with_suffix('.partial.txt')
    for i,offset in enumerate(range(0,len(signal),chunk_samples)):
        emit(status=f'Lokale Transkription · Abschnitt {i+1} von {count}')
        chunk = np.ascontiguousarray(signal[offset:offset+chunk_samples])
        if np.sqrt(np.mean(chunk.astype(np.float64)**2)) < .0001:
            text = ''
        else:
            language = {'de':'German','en':'English','fr':'French','es':'Spanish'}.get(args.language)
            result = model.generate(chunk, language=language, max_tokens=512, temperature=0.0)
            text = result.text.strip()
        parts.append(text)
        segments.append({'start':offset/16000,'end':min(duration,(offset+chunk_samples)/16000),'text':text})
        joined = '\n\n'.join(t for t in parts if t)
        partial.write_text(joined, encoding='utf-8')
        emit(text=joined)
        mx.clear_cache()
    partial.replace(target)
    metadata = {'model':json.loads((model_path/'.ready.json').read_text()),'language':args.language,
        'audio':audio_path.name,'segments':segments,'elapsed_seconds':time.monotonic()-started,
        'local_only':True,'segment_times_are_not_word_timestamps':True}
    audio_path.with_suffix('.transcript.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    emit(status=f'Transkript gespeichert · {target.name}',text=joined,path=str(target))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(str(e),file=sys.stderr,flush=True)
        sys.exit(1)
