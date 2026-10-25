"""In-app updates via the home server (the GitHub repo is private).

check:   python updater.py --check --url … --token …            → prints JSON {current, latest, available}
install: python updater.py --install --url … --token … --pid N  → waits for the app to exit, replaces the
         program files (never recordings, models, settings or the Python environment), updates the
         packages and restarts LocalVoice. Everything is logged to Logs/update.log.
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

ROOT = Path(__file__).resolve().parent.parent
# Only these come from the update; user data and runtime folders are never touched.
PROGRAM = ('src', 'installer', 'server', 'tests', 'docs', 'requirements.txt', 'requirements-mac.txt',
           'requirements-windows.txt', 'README.md', 'Installieren-Windows.cmd', 'Deinstallieren-Windows.cmd',
           'Installieren-Mac.command', 'build.sh', 'Launcher.swift', 'release.sh', 'version.json',
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


def request(url, token, path, timeout=30):
    req = urllib.request.Request(url.rstrip('/')+path, headers={'Authorization': f'Bearer {token}'})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise RuntimeError('Token ungültig.' if exc.code == 401 else
                           'Auf dem Server ist noch kein Update veröffentlicht.' if exc.code == 404 else f'Server meldet {exc.code}.')
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Server nicht erreichbar ({exc.reason}).')


def check(url, token):
    with request(url, token, '/v1/update') as response:
        latest = json.loads(response.read())
    current = local_version()
    return {'current': current, 'latest': latest, 'dev': is_dev_checkout(),
            'available': latest.get('commit') != current}


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


def install(url, token, pid):
    log(f'Update gestartet (aktuell {local_version()}).')
    if is_dev_checkout():
        raise RuntimeError('Entwicklungsordner mit git – bitte mit „git pull“ aktualisieren.')
    with tempfile.TemporaryDirectory(prefix='localvoice-update-') as tmp:
        archive = Path(tmp)/'update.zip'
        with request(url, token, '/v1/update/download', timeout=600) as response, open(archive, 'wb') as out:
            shutil.copyfileobj(response, out)
        with zipfile.ZipFile(archive) as z:
            z.extractall(Path(tmp)/'new')
        new = Path(tmp)/'new'
        if not (new/'src'/'combined.py').exists():
            raise RuntimeError('Update-Paket ist unvollständig.')
        wait_for_exit(pid)
        for name in PROGRAM:
            source, target = new/name, ROOT/name
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            elif source.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        log(f'Dateien ersetzt (neu {local_version()}).')
    install_packages()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--pid', type=int, default=0)
    args = parser.parse_args()
    try:
        if args.check:
            print(json.dumps(check(args.url, args.token), ensure_ascii=False), flush=True)
        elif args.install:
            try:
                install(args.url, args.token, args.pid)
                log('Update fertig.')
            finally:
                restart()
    except Exception as exc:
        if args.install:
            log(f'Fehler: {exc}')
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
