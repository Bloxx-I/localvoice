#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
app="../LocalVoice-reSpeaker.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
CLANG_MODULE_CACHE_PATH="$PWD/.build-cache" swiftc Launcher.swift -o "$app/Contents/MacOS/LocalVoice-reSpeaker"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>LocalVoice-reSpeaker</string>
<key>CFBundleIdentifier</key><string>local.benlamprecht.LocalVoiceReSpeaker</string>
<key>CFBundleName</key><string>LocalVoice + reSpeaker</string>
<key>CFBundleDisplayName</key><string>LocalVoice + reSpeaker</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.2.0</string>
<key>CFBundleVersion</key><string>2</string>
<key>NSMicrophoneUsageDescription</key><string>LocalVoice testet den reSpeaker und verarbeitet Aufnahmen und Transkripte lokal auf diesem Mac.</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$app"
