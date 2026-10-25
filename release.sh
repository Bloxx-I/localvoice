#!/bin/zsh
# Publish the current commit: push to GitHub and offer it as in-app update via the home server.
# Usage: zsh release.sh [ssh-host]   (default: r9700)
set -euo pipefail
cd "${0:A:h}"
host="${1:-r9700}"
[[ -z "$(git status --porcelain)" ]] || { echo "Erst committen – es gibt ungespeicherte Änderungen."; exit 1; }
git push -q
commit=$(git rev-parse --short HEAD)
tmp=$(mktemp -d)
git archive --format=zip -o "$tmp/latest.zip" HEAD
python3 - "$tmp" "$commit" <<'PY'
import json, subprocess, sys, zipfile
tmp, commit = sys.argv[1], sys.argv[2]
info = {'commit': commit,
        'date': subprocess.check_output(['git', 'log', '-1', '--format=%cI']).decode().strip(),
        'subject': subprocess.check_output(['git', 'log', '-1', '--format=%s']).decode().strip()}
with zipfile.ZipFile(f'{tmp}/latest.zip', 'a') as z:
    z.writestr('version.json', json.dumps(info, ensure_ascii=False, indent=2))
json.dump(info, open(f'{tmp}/latest.json', 'w'), ensure_ascii=False, indent=2)
PY
ssh "$host" mkdir -p /srv/ai/apps/localvoice/updates
scp -q "$tmp/latest.zip" "$host:/srv/ai/apps/localvoice/updates/latest.zip.part"
scp -q "$tmp/latest.json" "$host:/srv/ai/apps/localvoice/updates/latest.json.part"
ssh "$host" 'cd /srv/ai/apps/localvoice/updates && mv latest.zip.part latest.zip && mv latest.json.part latest.json'
rm -rf "$tmp"
echo "Veröffentlicht: $commit – erscheint in LocalVoice als Update."
