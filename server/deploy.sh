#!/bin/zsh
# Deploys the LocalVoice transcription service to the home server.
# Usage: zsh server/deploy.sh [ssh-host]   (default: r9700)
set -euo pipefail
cd "${0:A:h}"
host="${1:-r9700}"
dir=/srv/ai/apps/localvoice
ssh "$host" "mkdir -p $dir/models"
rsync -a localvoice_server.py requirements.txt localvoice-asr.service "$host:$dir/"
ssh "$host" bash -s <<REMOTE
set -euo pipefail
cd $dir
free=\$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
[ "\$free" -ge 1 ] || { echo "Zu wenig Platz auf /: \${free} GB"; exit 1; }
for f in Qwen3-ASR-1.7B-Q8_0.gguf mmproj-Qwen3-ASR-1.7B-bf16.gguf; do
  [ -f models/\$f ] || { curl -fsSL -o models/\$f.part "https://huggingface.co/ggml-org/Qwen3-ASR-1.7B-GGUF/resolve/main/\$f" && mv models/\$f.part models/\$f; }
done
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
if [ ! -f .env ]; then
  umask 077
  echo "LOCALVOICE_TOKEN=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env
fi
sed "s/__USER__/\$(id -un)/" localvoice-asr.service | sudo tee /etc/systemd/system/localvoice-asr.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now localvoice-asr.service
sudo systemctl restart localvoice-asr.service
sudo tailscale serve --bg --https=8650 http://127.0.0.1:8650 >/dev/null
# Public (no Tailscale needed on the client): Funnel allows only 443, 8443 and 10000.
sudo tailscale funnel --bg --https=10000 http://127.0.0.1:8650 >/dev/null
sleep 3
systemctl is-active localvoice-asr.service
name=\$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
echo "Öffentlich: https://\$name:10000   ·   Tailnet: https://\$name:8650"
REMOTE
