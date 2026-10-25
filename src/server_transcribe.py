"""Transcribe on the home server: same text track and chunks as locally, best model on its GPU.

Only the prepared 16 kHz text track and the chunk times are sent (tailnet, token). Results
are written in exactly the local format, so listening, export and the library work unchanged.
"""
import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8')


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


class Server:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip('/'), token

    def call(self, method, path, body=None, content_type='application/json', timeout=60):
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        request = urllib.request.Request(self.url+path, data, method=method, headers={
            'Authorization': f'Bearer {self.token}', 'Content-Type': content_type})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', 'replace')
            raise RuntimeError('Token ungültig.' if exc.code == 401 else f'Server meldet {exc.code}: {detail[:200]}')
        except urllib.error.URLError as exc:
            raise RuntimeError(f'Server nicht erreichbar ({exc.reason}). Internetverbindung und Server-Adresse prüfen.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('audio', help='Hörspur (_clean.flac/.wav)')
    parser.add_argument('--url', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--language', default='de')
    parser.add_argument('--context', default='')
    parser.add_argument('--check', action='store_true', help='nur Verbindung prüfen')
    args = parser.parse_args()
    server = Server(args.url, args.token)
    emit(status='Verbindung zum Server …', progress=0.)
    health = server.call('GET', '/v1/health', timeout=15)
    if not args.check and not health.get('ok'):
        raise RuntimeError('Server erreichbar, aber das Sprachmodell fehlt dort.')
    if args.check:
        emit(status=f"Server bereit · {health.get('model')}" if health.get('ok') else 'Server erreichbar, Modell fehlt.', health=health)
        return
    import soundfile as sf
    import lecture
    clean = Path(args.audio).resolve()
    source = lecture.board_source(clean)
    emit(status='Aufnahme wird für den Server vorbereitet …', progress=0.)
    track = lecture.prepare_text_track(lecture.read_mono(source))
    duration = len(track)/lecture.ASR_RATE
    regions = lecture.speech_regions(track)
    chunks = lecture.plan_chunks(regions, duration, track)
    buffer = io.BytesIO()
    sf.write(buffer, track, lecture.ASR_RATE, format='FLAC', subtype='PCM_16')
    job = server.call('POST', '/v1/jobs', {'language': args.language, 'context': args.context,
                                           'chunks': [[round(a, 3), round(b, 3)] for a, b in chunks]})['id']
    emit(status=f'Wird zum Server hochgeladen ({buffer.tell()/2**20:.1f} MB) …', progress=.03)
    server.call('PUT', f'/v1/jobs/{job}/audio', buffer.getvalue(), 'audio/flac', timeout=1800)
    started, shown = time.monotonic(), -1
    try:
        while True:
            time.sleep(2)
            status = server.call('GET', f'/v1/jobs/{job}')
            segments = status.get('segments', [])
            if len(segments) != shown:
                shown = len(segments)
                emit(text=lecture.paragraphs(segments), segments=segments)
            progress = status.get('progress', 0.)
            if status['state'] == 'queued':
                emit(status='Server: wartet auf einen freien Platz …', progress=.08)
            elif status['state'] == 'running':
                elapsed = time.monotonic()-started
                eta = f' · noch etwa {int(elapsed/progress*(1-progress)//60)+1} min' if progress > .05 else ''
                emit(status=f'Server transkribiert · {int(100*progress)} %{eta}', progress=.1+.9*progress)
            elif status['state'] == 'error':
                raise RuntimeError('Server: '+status.get('message', 'Fehler'))
            elif status['state'] == 'done':
                break
    except KeyboardInterrupt:
        server.call('DELETE', f'/v1/jobs/{job}')
        raise
    joined = lecture.paragraphs(segments)
    target = clean.with_suffix('.txt')
    target.write_text(joined, encoding='utf-8')
    metadata = {'model': status.get('model'), 'backend': 'server', 'server': args.url,
        'language': args.language, 'context': args.context, 'audio': clean.name, 'text_source': source.name,
        'text_track': 'Hochpass 80 Hz + automatischer Pegel, ohne KI-Rauschfilter', 'duration_seconds': duration,
        'segments': segments, 'speech_regions': [[round(a, 2), round(b, 2)] for a, b in regions],
        'elapsed_seconds': time.monotonic()-started, 'segment_times_are_not_word_timestamps': True}
    lecture.transcript_path(clean).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    emit(status=f'Transkript vom Server gespeichert · {status.get("model")}', text=joined, segments=segments,
         path=str(target), progress=1.)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
