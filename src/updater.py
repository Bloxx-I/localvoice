"""In-app updates straight from GitHub (public repo Bloxx-I/localvoice, branch main).

check:   python updater.py --check                → prints JSON {current, latest, available, dev}
install: python updater.py --install --pid N      → waits for the app to exit, replaces the program files
         (never recordings, models, settings or the Python environment), updates the packages and
         restarts LocalVoice. Everything is logged to Logs/update.log.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8')

REPO = 'Bloxx-I/localvoice'
BRANCH = 'main'
API = os.environ.get('LOCALVOICE_UPDATE_API', f'https://api.github.com/repos/{REPO}')
DOWNLOAD = os.environ.get('LOCALVOICE_UPDATE_DOWNLOAD', f'https://codeload.github.com/{REPO}/zip')
ROOT = Path(__file__).resolve().parent.parent
# Only these come from the update; user data and runtime folders are never touched.
PROGRAM = ('src', 'installer', 'server', 'tests', 'docs', 'requirements.txt', 'requirements-mac.txt',
           'requirements-windows.txt', 'README.md', 'Installieren-Windows.cmd', 'Deinstallieren-Windows.cmd',
           'Installieren-Mac.command', 'build.sh', 'Launcher.swift',
           'models/DeepFilterNet3_onnx.tar.gz')


def local_version():
    try:
        return json.loads((ROOT/'version.json').read_text(encoding='utf-8')).get('commit')
    except (OSError, ValueError):
        pass
    if (ROOT/'.git').exists():
        try:
            return subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return None


def is_dev_checkout():
    return (ROOT/'.git').exists()


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'LocalVoice-Updater',
                                               'Accept': 'application/vnd.github+json'})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise RuntimeError('GitHub-Limit erreicht, später erneut versuchen.' if exc.code in (403, 429)
                           else f'GitHub meldet {exc.code}.')
    except urllib.error.URLError as exc:
        raise RuntimeError(f'GitHub nicht erreichbar ({exc.reason}).')


def latest():
    with fetch(f'{API}/commits/{BRANCH}') as response:
        data = json.loads(response.read())
    return {'commit': data['sha'][:7], 'sha': data['sha'], 'date': data['commit']['committer']['date'],
            'subject': data['commit']['message'].splitlines()[0]}


def check():
    newest = latest()
    current = local_version()
    return {'current': current, 'latest': newest, 'dev': is_dev_checkout(),
            'available': newest['commit'] != current}


def log(message):
    (ROOT/'Logs').mkdir(exist_ok=True)
    with open(ROOT/'Logs'/'update.log', 'a', encoding='utf-8') as f:
        f.write(time.strftime('%Y-%m-%d %H:%M:%S ')+message+'\n')


def wait_for_exit(pid, seconds=120):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        try:
            if sys.platform == 'win32':
                out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'], capture_output=True, text=True,
                                     creationflags=0x08000000).stdout
                if str(pid) not in out:
                    return
            else:
                os.kill(pid, 0)
        except (ProcessLookupError, OSError):
            return
        time.sleep(.5)
    raise RuntimeError('LocalVoice wurde nicht beendet.')


def install_packages():
    requirements = ROOT/('requirements-windows.txt' if sys.platform == 'win32' else 'requirements-mac.txt')
    uv = next((p for p in (ROOT/'tools'/'uv.exe', ROOT/'tools'/'uv') if p.exists()), None) or shutil.which('uv')
    if (ROOT/'.venv').is_symlink() or not uv:
        log('Pakete: übersprungen (verknüpfte Umgebung oder kein uv).')
        return
    python = ROOT/'.venv'/('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    result = subprocess.run([str(uv), 'pip', 'install', '--python', str(python), '-r', str(requirements)],
                            capture_output=True, text=True, cwd=ROOT,
                            creationflags=0x08000000 if sys.platform == 'win32' else 0)
    log('Pakete: '+('ok' if result.returncode == 0 else 'Fehler '+result.stderr[-800:]))


def restart():
    if os.environ.get('LOCALVOICE_NO_RESTART'):
        return
    python = Path(sys.executable)
    if sys.platform == 'win32':
        gui = python.with_name('pythonw.exe')
        subprocess.Popen([str(gui if gui.exists() else python), str(ROOT/'src'/'combined.py')], cwd=ROOT,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        app = next((a for a in (Path.home()/'Applications'/'LocalVoice.app', ROOT.parent/'LocalVoice-reSpeaker.app') if a.exists()), None)
        if app:
            subprocess.Popen(['open', str(app)])
        else:
            subprocess.Popen([str(python), str(ROOT/'src'/'combined.py')], cwd=ROOT, start_new_session=True)


def install(pid):
    log(f'Update gestartet (aktuell {local_version()}).')
    if is_dev_checkout():
        raise RuntimeError('Entwicklungsordner mit git – bitte mit „git pull“ aktualisieren.')
    newest = latest()
    with tempfile.TemporaryDirectory(prefix='localvoice-update-') as tmp:
        archive = Path(tmp)/'update.zip'
        with fetch(f"{DOWNLOAD}/{newest['sha']}", timeout=600) as response, open(archive, 'wb') as out:
            shutil.copyfileobj(response, out)
        with zipfile.ZipFile(archive) as z:
            z.extractall(Path(tmp)/'new')
        # GitHub archives contain one top-level folder (localvoice-<sha>/).
        new = next((d for d in (Path(tmp)/'new').iterdir() if (d/'src'/'combined.py').exists()), None)
        if new is None:
            raise RuntimeError('Update-Paket ist unvollständig.')
        wait_for_exit(pid)
        for name in PROGRAM:
            source, target = new/name, ROOT/name
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            elif source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        (ROOT/'version.json').write_text(json.dumps(newest, ensure_ascii=False, indent=2), encoding='utf-8')
        log(f'Dateien ersetzt (neu {local_version()}).')
    install_packages()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--pid', type=int, default=0)
    args = parser.parse_args()
    try:
        if args.check:
            print(json.dumps(check(), ensure_ascii=False), flush=True)
        elif args.install:
            try:
                install(args.pid)
                log('Update fertig.')
            finally:
                restart()
    except Exception as exc:
        if args.install:
            log(f'Fehler: {exc}')
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
