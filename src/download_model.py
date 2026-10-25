"""Download the Qwen3-ASR model once; progress goes to models/download-status.json."""
import argparse
import json
import threading
import time
from pathlib import Path

from asr import default_repo
from paths import ASR_MODEL, DOWNLOAD_STATUS


def status(**data):
    DOWNLOAD_STATUS.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_STATUS.write_text(json.dumps(data, ensure_ascii=False))
    print(json.dumps(data, ensure_ascii=False), flush=True)


def folder_bytes(folder):
    return sum(p.stat().st_size for p in Path(folder).rglob('*') if p.is_file())


def download(repo=None, target=ASR_MODEL):
    from huggingface_hub import HfApi, snapshot_download
    target = Path(target)
    if (target/'.ready.json').exists():
        status(state='ready', percent=100., repo=json.loads((target/'.ready.json').read_text()).get('repo'))
        return
    repo = repo or default_repo()
    info = HfApi().model_info(repo, files_metadata=True)
    total = sum(s.size or 0 for s in info.siblings) or 1
    status(state='downloading', percent=0., repo=repo, total_bytes=total)
    done = threading.Event()

    def report():
        while not done.wait(1.):
            have = folder_bytes(target) if target.exists() else 0
            status(state='downloading', percent=min(99.9, 100*have/total), repo=repo, total_bytes=total)

    watcher = threading.Thread(target=report, daemon=True)
    watcher.start()
    try:
        snapshot_download(repo, revision=info.sha, local_dir=target)
    except BaseException as exc:
        done.set()
        status(state='error', message=str(exc)[:300], repo=repo)
        raise
    done.set()
    (target/'.ready.json').write_text(json.dumps({'repo': repo, 'revision': info.sha,
        'bytes': total, 'downloaded': time.strftime('%Y-%m-%d %H:%M')}, indent=2))
    status(state='ready', percent=100., repo=repo)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', help='z. B. Qwen/Qwen3-ASR-0.6B; Standard: passend zu diesem Rechner')
    args = parser.parse_args()
    download(args.repo)
