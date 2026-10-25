#!/bin/zsh
# Builds the macOS app bundle that starts LocalVoice from this folder.
# Usage: zsh build.sh [Ziel.app]   (default: ../LocalVoice-reSpeaker.app)
set -euo pipefail
cd "${0:A:h}"
app="${1:-../LocalVoice-reSpeaker.app}"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
CLANG_MODULE_CACHE_PATH="$PWD/.build-cache" swiftc Launcher.swift -o "$app/Contents/MacOS/LocalVoice"
print -r -- "$PWD" > "$app/Contents/Resources/root.txt"
sips -s format icns installer/LocalVoice.png --out "$app/Contents/Resources/AppIcon.icns" >/dev/null 2>&1 || true
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>LocalVoice</string>
<key>CFBundleIdentifier</key><string>local.benlamprecht.LocalVoiceReSpeaker</string>
<key>CFBundleName</key><string>LocalVoice</string>
<key>CFBundleDisplayName</key><string>LocalVoice</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.3.0</string>
<key>CFBundleVersion</key><string>3</string>
<key>NSMicrophoneUsageDescription</key><string>LocalVoice nimmt Vorlesungen auf und verarbeitet Audio und Text ausschließlich lokal auf diesem Mac.</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$app"
echo "App gebaut: $app"
