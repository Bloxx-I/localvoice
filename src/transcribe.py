"""Offline lecture transcription: board signal → levelled text track → pause-cut chunks → Qwen3-ASR."""
import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
# Windows defaults to a legacy code page for pipes; the app reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8')

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
    parser.add_argument('audio', help='Hörspur (_clean.flac/.wav); Text wird in deren Nähe gespeichert')
    parser.add_argument('--model', required=True)
    parser.add_argument('--language', default='de')
    parser.add_argument('--context', default='', help='Thema / Fachbegriffe')
    parser.add_argument('--source', choices=('board','clean'), default='board',
                        help='board: unverändertes Board-Signal (Standard), clean: verbesserte Hörspur')
    args = parser.parse_args()
    import lecture
    from asr import ASR
    clean = Path(args.audio).resolve()
    source = lecture.board_source(clean) if args.source == 'board' else clean
    emit(status='Sprachmodell wird geladen …')
    model = ASR(args.model)
    emit(status='Aufnahme wird vorbereitet …')
    raw = lecture.read_mono(source)
    track = lecture.prepare_text_track(raw)
    del raw
    duration = len(track)/lecture.ASR_RATE
    regions = lecture.speech_regions(track)
    chunks = lecture.plan_chunks(regions, duration, track)
    started = time.monotonic()
    segments = []
    target = clean.with_suffix('.txt')
    partial = target.with_suffix('.partial.txt')
    for i,(a,b) in enumerate(chunks):
        elapsed = time.monotonic()-started
        remaining = elapsed/i*(len(chunks)-i) if i else 0
        emit(status=f'Transkription · {int(100*i/max(1,len(chunks)))} %'+(f' · noch etwa {int(remaining//60)+1} min' if i > 2 else ''),
             progress=i/max(1,len(chunks)))
        text = model.transcribe(track[int(a*lecture.ASR_RATE):int(b*lecture.ASR_RATE)], args.language, args.context)
        segments.append({'start':round(a,3),'end':round(b,3),'text':text})
        joined = lecture.paragraphs(segments)
        partial.write_text(joined, encoding='utf-8')
        emit(text=joined, segments=segments)
    joined = lecture.paragraphs(segments)
    target.write_text(joined, encoding='utf-8')
    partial.unlink(missing_ok=True)
    metadata = {'model':model.describe(),'backend':model.kind,'language':args.language,'context':args.context,
        'audio':clean.name,'text_source':source.name,'text_track':'Hochpass 80 Hz + automatischer Pegel, ohne KI-Rauschfilter'
        if args.source == 'board' else 'verbesserte Hörspur','duration_seconds':duration,
        'segments':segments,'speech_regions':[[round(a,2),round(b,2)] for a,b in regions],
        'elapsed_seconds':time.monotonic()-started,'local_only':True,'segment_times_are_not_word_timestamps':True}
    lecture.transcript_path(clean).write_text(json.dumps(metadata,ensure_ascii=False,indent=2), encoding='utf-8')
    emit(status=f'Transkript gespeichert · {target.name}',text=joined,segments=segments,path=str(target),progress=1.)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(str(e),file=sys.stderr,flush=True)
        sys.exit(1)
