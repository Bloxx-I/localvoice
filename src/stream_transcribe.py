"""Persistent offline Qwen ASR worker. One model load; incremental audio chunks."""
import argparse
import json
import sys
import time
from pathlib import Path

# Set offline flags and prohibit outbound connections before importing ML code.
from transcribe import emit
from stream_text import append_text


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', required=True)
    p.add_argument('--language', default='de')
    args = p.parse_args()
    model_path = Path(args.model).resolve()
    if not (model_path/'.ready.json').exists():
        raise RuntimeError('Das lokale ASR-Modell fehlt.')
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    import soxr
    from mlx_audio.stt.utils import load_model
    mx.set_cache_limit(256*1024*1024)
    emit(status='Live-Text: lokales Modell wird geladen …')
    model = load_model(model_path, strict=True)
    emit(status='Live-Text bereit · wartet auf den ersten Audioabschnitt')
    language = {'de':'German','en':'English','fr':'French','es':'Spanish'}.get(args.language)
    text, segments, target = '', [], None
    for line in sys.stdin:
        event = json.loads(line)
        if event.get('finish'):
            if target:
                target.write_text(text, encoding='utf-8')
                target.with_suffix('.live.json').write_text(json.dumps({'segments':segments,
                    'local_only':True, 'mode':'incremental_audio_chunks',
                    'note':'Abschnittsweise Transkription mit 0,5 s Überlappung; keine Wortzeitstempel.'}, ensure_ascii=False, indent=2))
            emit(finished=True, text=text, path=str(target) if target else None)
            return
        path = Path(event['audio'])
        target = Path(event['target'])
        started = time.monotonic()
        data, rate = sf.read(path, dtype='float32')
        if rate != 16000:
            data = soxr.resample(data, rate, 16000)
        if len(data) and np.sqrt(np.mean(data.astype(np.float64)**2)) >= .0001:
            result = model.generate(np.ascontiguousarray(data), language=language, max_tokens=512, temperature=0.0)
            incoming = result.text.strip()
        else:
            incoming = ''
        text = append_text(text, incoming)
        target.with_suffix('.partial.txt').write_text(text, encoding='utf-8')
        segments.append({'index':event['index'], 'start':event['start'],
                         'duration':len(data)/16000, 'inference_seconds':time.monotonic()-started,
                         'text':incoming})
        emit(text=text, completed=event['index']+1, inference_seconds=segments[-1]['inference_seconds'])
        mx.clear_cache()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
