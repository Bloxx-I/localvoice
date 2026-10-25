"""LocalVoice transcription service for the home server (r9700).

The app uploads the prepared 16 kHz text track plus its pause-cut chunk list; this service
transcribes every chunk with Qwen3-ASR-1.7B on the GPU and returns timed segments. The audio
is deleted as soon as the job ends. Reachable tailnet-only behind `tailscale serve`, guarded
by a bearer token.

Engine: a private llama.cpp `llama-server` (ROCm, Qwen3-ASR GGUF + audio mmproj) that runs
only while there is work. If the GPU lacks memory to start it, the Model Deck's active LLM is
paused for the batch and re-activated afterwards – one restart per batch, never per chunk.
"""
import base64
import io
import json
import os
import queue
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parent
TOKEN = os.environ.get('LOCALVOICE_TOKEN', '')
LLAMA_SERVER = os.environ.get('LOCALVOICE_LLAMA_SERVER', '/srv/ai/src/llama.cpp/build/bin/llama-server')
MODEL = Path(os.environ.get('LOCALVOICE_MODEL', ROOT/'models'/'Qwen3-ASR-1.7B-Q8_0.gguf'))
MMPROJ = Path(os.environ.get('LOCALVOICE_MMPROJ', ROOT/'models'/'mmproj-Qwen3-ASR-1.7B-bf16.gguf'))
ASR_PORT = int(os.environ.get('LOCALVOICE_ASR_PORT', '8651'))
MODEL_DECK = os.environ.get('LOCALVOICE_MODEL_DECK', 'http://127.0.0.1:4180')
NEED_VRAM_GB = float(os.environ.get('LOCALVOICE_NEED_VRAM_GB', '3.5'))
IDLE_STOP_S = float(os.environ.get('LOCALVOICE_IDLE_STOP_S', '60'))
KEEP_RESULTS_S = 24*3600
UPDATES = ROOT/'updates'   # filled by release.sh on the Mac: latest.zip + latest.json
# Public via Tailscale Funnel: cap uploads and protect the (small) system disk.
MAX_UPLOAD_MB = float(os.environ.get('LOCALVOICE_MAX_UPLOAD_MB', '600'))
MIN_FREE_DISK_GB = float(os.environ.get('LOCALVOICE_MIN_FREE_GB', '1.5'))
LANGUAGES = {'de': 'German', 'en': 'English', 'fr': 'French', 'es': 'Spanish'}
MODEL_NAME = 'Qwen3-ASR-1.7B (Q8_0, Audio-Encoder bf16) · GPU r9700'

app = FastAPI(title='LocalVoice Server', docs_url=None, redoc_url=None)
jobs = {}
work = queue.Queue()
state = {'process': None, 'last_used': 0., 'paused_llm': None, 'busy': False}


def authorized(authorization: str = Header('')):
    if not TOKEN or not secrets.compare_digest(authorization, f'Bearer {TOKEN}'):
        raise HTTPException(401, 'Token fehlt oder ist falsch.')


# --- GPU sharing with the Model Deck ------------------------------------------

def free_vram_gb():
    try:
        out = subprocess.run(['/opt/rocm/bin/rocm-smi', '--showmeminfo', 'vram', '--json'],
                             capture_output=True, text=True, timeout=10).stdout
        card = next(iter(json.loads(out).values()))
        total = int(card['VRAM Total Memory (B)'])
        used = int(card['VRAM Total Used Memory (B)'])
        return (total-used)/2**30
    except Exception:
        return None


def deck(method, path):
    request = urllib.request.Request(MODEL_DECK+path, method=method)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read() or b'{}')


def pause_llm():
    models = deck('GET', '/api/state').get('models', [])
    alias = next((m['alias'] for m in models if m.get('active')), None)
    deck('POST', '/api/server/stop')
    state['paused_llm'] = alias
    print(f'LLM {alias} für die Transkription pausiert.', flush=True)
    for _ in range(60):
        if (free_vram_gb() or 0) >= NEED_VRAM_GB:
            return
        time.sleep(1)


def resume_llm():
    alias, state['paused_llm'] = state['paused_llm'], None
    if alias:
        try:
            deck('POST', f'/api/models/{alias}/activate')
            print(f'LLM {alias} wieder aktiv.', flush=True)
        except Exception as exc:
            print(f'LLM {alias} konnte nicht wieder gestartet werden: {exc}', flush=True)


# --- ASR engine: private llama-server --------------------------------------------

def asr_url(path):
    return f'http://127.0.0.1:{ASR_PORT}{path}'


def asr_healthy():
    try:
        with urllib.request.urlopen(asr_url('/health'), timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def start_asr():
    if state['process'] and state['process'].poll() is None and asr_healthy():
        return
    stop_asr()
    for attempt in range(2):
        if (free_vram_gb() or 99) < NEED_VRAM_GB and not state['paused_llm']:
            pause_llm()
        log = open(ROOT/'asr-server.log', 'ab')
        state['process'] = subprocess.Popen([LLAMA_SERVER, '-m', str(MODEL), '--mmproj', str(MMPROJ),
            '--host', '127.0.0.1', '--port', str(ASR_PORT), '-ngl', '99', '-c', '8192', '--parallel', '1',
            '--no-webui'], stdout=log, stderr=subprocess.STDOUT)
        for _ in range(120):
            if asr_healthy():
                return
            if state['process'].poll() is not None:
                break
            time.sleep(1)
        stop_asr()
        if state['paused_llm']:
            break
        pause_llm()  # most likely out of GPU memory: free it and try once more
    raise RuntimeError('ASR-Server startet nicht (siehe asr-server.log).')


def stop_asr():
    process, state['process'] = state['process'], None
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.kill()


def wav_base64(audio):
    buffer = io.BytesIO()
    sf.write(buffer, audio, 16000, format='WAV', subtype='PCM_16')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def transcribe(audio, language, context):
    if not len(audio) or float(np.sqrt(np.mean(audio.astype(np.float64)**2))) < 1e-4:
        return ''
    messages = []
    if context:
        messages.append({'role': 'system', 'content': context})
    messages.append({'role': 'user', 'content': [
        {'type': 'input_audio', 'input_audio': {'data': wav_base64(audio), 'format': 'wav'}}]})
    name = LANGUAGES.get(language)
    if name:
        # Qwen3-ASR answers "language X<asr_text>…"; prefilling the language forces it.
        messages.append({'role': 'assistant', 'content': f'language {name}<asr_text>'})
    body = json.dumps({'messages': messages, 'temperature': 0, 'max_tokens': 512}).encode()
    request = urllib.request.Request(asr_url('/v1/chat/completions'), body, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=600) as response:
        text = json.loads(response.read())['choices'][0]['message']['content']
    return re.sub(r'^\s*(language\s+\w+)?\s*<asr_text>', '', text).strip()


def run(job):
    job['state'] = 'running'
    try:
        start_asr()
        audio, rate = sf.read(job['audio'], dtype='float32', always_2d=True)
        audio = audio.mean(axis=1)
        if rate != 16000:
            raise ValueError('Erwartet 16 kHz.')
        chunks = job['chunks'] or [[0, len(audio)/16000]]
        started = time.monotonic()
        for i, (a, b) in enumerate(chunks):
            if job.get('cancelled'):
                raise RuntimeError('Abgebrochen.')
            text = transcribe(audio[int(a*16000):int(b*16000)], job['language'], job['context'])
            job['segments'].append({'start': a, 'end': b, 'text': text})
            job['progress'] = (i+1)/len(chunks)
            state['last_used'] = time.monotonic()
        job['elapsed_seconds'] = time.monotonic()-started
        job['state'] = 'done'
    except Exception as exc:
        job['state'], job['message'] = 'error', str(exc)[:500]
    finally:
        Path(job.pop('audio', '') or '/nonexistent').unlink(missing_ok=True)
        job['finished'] = time.time()
        state['last_used'] = time.monotonic()


def worker():
    while True:
        try:
            job = work.get(timeout=5)
        except queue.Empty:
            # Batch done: free the GPU quickly so the LLM (and its autoscaler) get it back.
            if state['process'] and time.monotonic()-state['last_used'] > IDLE_STOP_S:
                stop_asr()
            if state['paused_llm'] and not state['process']:
                resume_llm()
            now = time.time()
            for key in [k for k, j in jobs.items() if j.get('finished') and now-j['finished'] > KEEP_RESULTS_S]:
                jobs.pop(key, None)
            continue
        state['busy'] = True
        run(job)
        state['busy'] = work.qsize() > 0


threading.Thread(target=worker, daemon=True, name='asr-worker').start()


# --- API ------------------------------------------------------------------------

@app.get('/v1/health', dependencies=[Depends(authorized)])
def health():
    ready = MODEL.exists() and MMPROJ.exists()
    return {'ok': ready, 'model': MODEL_NAME if ready else None, 'busy': state['busy'],
            'queued': work.qsize(), 'engine_running': bool(state['process'])}


@app.get('/v1/update', dependencies=[Depends(authorized)])
def update_info():
    try:
        return json.loads((UPDATES/'latest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise HTTPException(404, 'Kein Update veröffentlicht.')


@app.get('/v1/update/download', dependencies=[Depends(authorized)])
def update_download():
    if not (UPDATES/'latest.zip').exists():
        raise HTTPException(404, 'Kein Update veröffentlicht.')
    return FileResponse(UPDATES/'latest.zip', media_type='application/zip', filename='localvoice.zip')


@app.post('/v1/jobs', dependencies=[Depends(authorized)])
def create_job(spec: dict):
    chunks = [[float(a), float(b)] for a, b in spec.get('chunks', [])]
    job_id = uuid.uuid4().hex
    jobs[job_id] = {'id': job_id, 'state': 'waiting_for_audio', 'progress': 0., 'segments': [],
                    'language': spec.get('language', 'de'), 'context': str(spec.get('context', ''))[:2000],
                    'chunks': chunks, 'message': ''}
    return {'id': job_id}


@app.put('/v1/jobs/{job_id}/audio', dependencies=[Depends(authorized)])
async def upload_audio(job_id: str, request: Request):
    job = jobs.get(job_id)
    if not job or job['state'] != 'waiting_for_audio':
        raise HTTPException(404, 'Auftrag unbekannt.')
    import shutil
    if shutil.disk_usage(tempfile.gettempdir()).free/2**30 < MIN_FREE_DISK_GB:
        raise HTTPException(507, 'Server-Speicher ist voll.')
    handle, path = tempfile.mkstemp(prefix='localvoice-', suffix='.flac')
    size = 0
    with os.fdopen(handle, 'wb') as out:
        async for part in request.stream():
            size += len(part)
            if size > MAX_UPLOAD_MB*2**20:
                out.close()
                Path(path).unlink(missing_ok=True)
                jobs.pop(job_id, None)
                raise HTTPException(413, f'Aufnahme zu groß (max. {MAX_UPLOAD_MB:.0f} MB).')
            out.write(part)
    job['audio'] = path
    job['state'] = 'queued'
    work.put(job)
    return {'id': job_id, 'position': work.qsize()}


@app.get('/v1/jobs/{job_id}', dependencies=[Depends(authorized)])
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, 'Auftrag unbekannt.')
    return {k: v for k, v in job.items() if k not in ('audio', 'chunks')} | {'model': MODEL_NAME}


@app.delete('/v1/jobs/{job_id}', dependencies=[Depends(authorized)])
def cancel(job_id: str):
    job = jobs.get(job_id)
    if job:
        job['cancelled'] = True
    return {'ok': True}
