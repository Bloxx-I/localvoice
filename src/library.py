"""Recording library and transcript export. No Qt; used by the Studio sidebar."""
import json
import re
from datetime import datetime
from pathlib import Path

import lecture
from stream_text import append_text

STAMP = re.compile(r'(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_\d+_?(.*)$')


def metadata_path(clean):
    return lecture.metadata_path(clean)


def duration_of(clean):
    try:
        return float(json.loads(metadata_path(clean).read_text())['duration_seconds'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        import soundfile as sf
        return float(sf.info(str(clean)).duration)
    except Exception:
        return 0.


def scan(folder):
    """All recordings with a listening track in a folder, newest first."""
    entries = []
    for clean in lecture.clean_files(folder):
        original = Path(str(clean).replace(clean.name, lecture.stem_of(clean)+'_original.wav'))
        stem = lecture.stem_of(clean).removeprefix('LocalVoice_')
        match = STAMP.match(stem)
        try:
            created = datetime.strptime(' '.join(match.groups()[:2]), '%Y-%m-%d %H-%M-%S') if match else None
        except ValueError:
            created = None
        text_path = clean.with_suffix('.txt')
        try:
            text = text_path.read_text(encoding='utf-8').strip() if text_path.exists() else ''
        except OSError:
            text = ''
        entries.append({'clean': str(clean), 'original': str(original) if original.exists() else None,
            'created': created or datetime.fromtimestamp(clean.stat().st_mtime),
            'variant': (match.group(3) if match else '').replace('_', ' '),
            'duration': duration_of(clean), 'text': text})
    return sorted(entries, key=lambda e: e['created'], reverse=True)


def cues(clean, text='', duration=0.):
    """Timed text blocks for subtitles. Segment times, not word timestamps."""
    clean = Path(clean)
    deferred = clean.with_suffix('.transcript.json')
    live = clean.with_suffix('.live.json')
    try:
        if deferred.exists():
            result = [(s['start'], s['end'], s['text'].strip())
                      for s in json.loads(deferred.read_text())['segments'] if s['text'].strip()]
            if result: return result
        if live.exists():
            result, joined = [], ''
            for s in json.loads(live.read_text())['segments']:
                # Live chunks overlap by 0.5 s; keep only the words each chunk adds.
                combined = append_text(joined, s['text'])
                added = combined[len(joined):].strip()
                joined = combined
                if added:
                    result.append((s['start'], s['start']+s['duration'], added))
            if result: return result
    except (OSError, ValueError, KeyError):
        pass
    text = text.strip()
    return [(0., duration or 1., text)] if text else []


def stamp(seconds, separator):
    ms = int(round(seconds*1000))
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}{separator}{ms%1000:03}'


def to_srt(items):
    return '\n'.join(f'{i}\n{stamp(a, ",")} --> {stamp(b, ",")}\n{text}\n'
                     for i, (a, b, text) in enumerate(items, 1))


def to_vtt(items):
    return 'WEBVTT\n\n' + '\n'.join(f'{stamp(a, ".")} --> {stamp(b, ".")}\n{text}\n' for a, b, text in items)


def to_markdown(clean, text, items, duration=0., bookmarks=()):
    entry = lecture.stem_of(clean).removeprefix('LocalVoice_')
    lines = [f'# Transkript · {entry}', '',
             f'- Datei: `{Path(clean).name}`',
             f'- Dauer: {int(duration)//60}:{int(duration)%60:02}',
             '- Lokal transkribiert mit Qwen3-ASR', '']
    if len(items) > 1:
        lines += [f'**[{int(a)//60}:{int(a)%60:02}]** {t}' + '\n' for a, _, t in items]
    else:
        lines.append(text.strip() + '\n')
    if bookmarks:
        lines += ['## Merker', '']
        lines += [f'- **{int(b["t"])//60}:{int(b["t"])%60:02}** {b.get("note") or ""}'.rstrip() for b in bookmarks]
        lines.append('')
    return '\n'.join(lines)
