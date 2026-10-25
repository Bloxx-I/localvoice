#!/bin/zsh
# LocalVoice für macOS (Apple Silicon) installieren: Doppelklick genügt.
# Richtet Python + Pakete ein, lädt das Sprachmodell und legt LocalVoice.app in ~/Programme ab.
set -euo pipefail
cd "${0:A:h}"
UV_VERSION=0.12.17
print "\n==> LocalVoice wird eingerichtet in: $PWD"
if [[ "$(uname -m)" != "arm64" ]]; then
  print "Hinweis: Die schnelle Spracherkennung (MLX) braucht einen Mac mit Apple-Chip."
fi
if [[ -L .venv ]]; then
  print "==> Vorhandene, verknüpfte Python-Umgebung wird weiterverwendet (nicht verändert)."
else
  uv=$(command -v uv || true)
  if [[ -z "$uv" ]]; then
    print "==> Paketmanager uv $UV_VERSION laden"
    mkdir -p tools
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-aarch64-apple-darwin.tar.gz" | tar -xz -C tools --strip-components 1
    uv="$PWD/tools/uv"
  fi
  print "==> Python 3.12 und Pakete installieren"
  "$uv" venv --allow-existing --python 3.12 .venv
  "$uv" pip install --python .venv/bin/python -r requirements-mac.txt
fi
if [[ ! -f models/qwen3-asr/.ready.json ]]; then
  print "==> Sprachmodell laden (einmalig, etwa 2,5 GB)"
  PYTHONWARNINGS=ignore HF_HUB_DISABLE_PROGRESS_BARS=1 .venv/bin/python src/download_model.py --console
fi
if command -v swiftc >/dev/null; then
  print "==> App erstellen"
  mkdir -p "$HOME/Applications"
  zsh build.sh "$HOME/Applications/LocalVoice.app"
  print "\nFertig. LocalVoice liegt in ~/Programme (Spotlight: „LocalVoice“)."
  open -R "$HOME/Applications/LocalVoice.app"
else
  print "\nFertig. Für eine App-Verknüpfung einmal 'xcode-select --install' ausführen und dieses Skript erneut starten."
  print "Direkt starten: $PWD/.venv/bin/python $PWD/src/combined.py"
fi
