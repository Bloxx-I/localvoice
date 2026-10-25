# LocalVoice

Vorlesungen und Vorträge aufnehmen, später nachhören und **vollständig lokal** transkribieren – gebaut für das
**ReSpeaker Lite**, funktioniert aber mit jedem Mikrofon. Audio und Text verlassen den Rechner nie.

## Installation

### Windows 10/11

1. Auf GitHub **Code → Download ZIP**, entpacken.
2. **`Installieren-Windows.cmd`** doppelklicken.

Das Skript installiert Python, alle Pakete und das Sprachmodell nach `%LOCALAPPDATA%\LocalVoice` und legt
**LocalVoice** auf dem Desktop und im Startmenü an. Mit NVIDIA-Grafikkarte wird automatisch die schnelle
GPU-Version (Qwen3-ASR 1.7B) eingerichtet, sonst das kleinere Modell für den Prozessor (Qwen3-ASR 0.6B).
Einmalig nötig: Internet und etwa 3–6 GB Platz. Aufnahmen landen in `Dokumente\LocalVoice Aufnahmen`.

Entfernen: `Deinstallieren-Windows.cmd` – Aufnahmen bleiben erhalten.

### macOS (Apple-Chip)

`Installieren-Mac.command` doppelklicken (beim ersten Mal ggf. Rechtsklick → Öffnen). Danach liegt
**LocalVoice.app** in `~/Programme`. Die Spracherkennung läuft dort über MLX.

## Bedienung

Oben rechts schaltest du zwischen **Einfach** und **Experte** um.

**Einfach** – für alle:
- **Aufnehmen:** großer Knopf (⌘R / Strg+R). Der ReSpeaker wird automatisch gewählt, der Pegel automatisch angepasst.
  Nach dem Stopp entstehen von selbst die Hörspur und das Transkript.
- **Nachhören:** wie ein Podcast – ±15/30 s (← →), Tempo 1–2× ohne Mickymaus-Stimme, der gesprochene Absatz wird
  im Text hervorgehoben, Klick auf einen Absatz springt dorthin. Die App merkt sich, wo du aufgehört hast.
- **Aufnahmen:** Liste links mit Suche in Text und Datum.
- **Export:** Markdown, SRT oder WebVTT.

**Experte** zeigt zusätzlich: Mikrofonauswahl mit Auto-Pegel/Gain/Kanal/Delay pro Mikrofon, Live-Text während der
Aufnahme, Filterstärke und „Neu verbessern“, Textquelle, **Thema/Fachbegriffe** für die Erkennung, Ausgabegerät,
**Pausen überspringen**, **Merker** (Taste M), A/B-Vergleich mit dem Original (Taste O halten) und den
Reiter **reSpeaker · USB-Test**.

## Updates

LocalVoice prüft beim Start auf GitHub, ob es eine neue Version gibt, und zeigt dann oben
**„⬆ Update installieren“**. Ein Klick lädt sie, LocalVoice schließt sich kurz und startet aktualisiert neu.
Ersetzt werden nur Programmdateien – Aufnahmen, Sprachmodell, Einstellungen und Python-Umgebung bleiben.
Protokoll: `Logs/update.log`. Das ZIP muss also nur einmal für die Erstinstallation geladen werden.

Aufnahmen benennen: in der Liste doppelklicken oder Rechtsklick → „Umbenennen …“, beim Nachhören „✎ Umbenennen“.
Die Dateien behalten ihre Namen; der Name gilt für Liste, Suche, Nachhören und Export.

## Auf dem Heimserver transkribieren

**„Auf Server verarbeiten“** (auch im Einfach-Modus) schickt die vorbereitete Textspur an den eigenen Server und
holt das Transkript zurück: **Qwen3-ASR 1.7B** auf der GPU, deutlich schneller als auf einem Laptop-Prozessor.
Gesendet werden nur die 16-kHz-Textspur (FLAC) und die Abschnittszeiten, per HTTPS mit langem Token (256 Bit);
der Server löscht das Audio nach dem Auftrag. Das Ergebnis landet im selben Format wie lokal (Mitlesen, Export, Liste).

Adresse: **`https://r9700.tail759c8d.ts.net:10000`** – öffentlich über Tailscale Funnel, auf dem Laptop ist kein
Tailscale nötig. Ohne gültiges Token antwortet jeder Endpunkt mit 401; Uploads sind auf 600 MB begrenzt und werden
abgelehnt, wenn auf dem Server weniger als 1,5 GB frei sind. Im Tailnet zusätzlich unter Port 8650.

- Einrichten: `zsh server/deploy.sh r9700` – kopiert den Dienst, lädt das Modell (`ggml-org/Qwen3-ASR-1.7B-GGUF`,
  Q8_0 + Audio-Encoder bf16, zusammen 2,6 GB), erzeugt das Token in `/srv/ai/apps/localvoice/.env` und gibt
  ihn öffentlich unter `https://<server>:10000` (Funnel) und im Tailnet unter `:8650` frei.
- Der Dienst startet einen eigenen llama.cpp-Server nur für die Dauer eines Auftrags. Reicht der Grafikspeicher
  nicht, pausiert er das aktive Sprachmodell im Model Deck und startet es danach wieder.
- In der App: Experte → Einrichtung → **Server**: Adresse, Token, „Verbindung testen“ und optional
  „Nach jeder Aufnahme auf dem Server“. Im Einfach-Modus fragt der Knopf beim ersten Mal nach dem Token.

## Wie es arbeitet

- **ReSpeaker Lite:** Die USB-Firmware liefert ein bereits vom Board bearbeitetes Signal und kopiert es auf beide
  Kanäle (gemessen: bitgleich). LocalVoice erkennt das und speichert die native Spur nur einmal.
- **Auto-Pegel** am Eingang: hebt leise, entfernte Sprecher an, ohne in Pausen das Rauschen hochzuziehen.
- **Zwei Spuren:** Die **Hörspur** wird mit DeepFilterNet entrauscht (FLAC, 48 kHz). Die **Textspur** nutzt das
  unveränderte Board-Signal mit Hochpass und Auto-Pegel – im Vergleich mit echten Aufnahmen erkannte Qwen darauf
  in 9 von 10 Fällen mehr und richtiger als auf der gefilterten Spur.
- **Schnitte in Sprechpausen** (3–25 s) statt starr alle 20 s, damit keine Wörter zerschnitten werden. Daraus
  kommen auch die Zeitmarken für Mitlesen und Untertitel (Abschnittszeiten, keine Wortzeitstempel).
- Speicher: etwa **0,6 GB pro 90 Minuten** (native Spur + Hörspur).

Dateien pro Aufnahme: `*_input1_native.wav` (unverändert), `*_clean.flac` (Hörspur), `*_clean.txt`,
`*_clean.transcript.json` (Abschnitte, Sprechpausen), `*.listen.json` (Merker, Position), `*.json` (Metadaten).

## Entwicklung

```sh
.venv/bin/python src/combined.py                      # starten
.venv/bin/python -m unittest discover -s tests -v     # Tests
zsh build.sh ~/Applications/LocalVoice.app            # macOS-App bauen
```

| Datei | Aufgabe |
|---|---|
| `src/combined.py` | Hauptfenster, Einfach/Experte, USB-Reiter, Import |
| `src/app.py`, `src/studio_extension.py` | Studio: Aufnahme, Ansichten, Ablauf |
| `src/listen.py` | Nachhören-Ansicht |
| `src/engine.py` | Aufnahme, Auto-Pegel, DeepFilterNet (nativ auf macOS/Linux, ONNX auf Windows) |
| `src/lecture.py` | Textspur, Pausenerkennung, Abschnitte, Dateizuordnung |
| `src/asr.py`, `src/transcribe.py`, `src/stream_transcribe.py` | Qwen3-ASR über MLX (Mac) oder PyTorch (Windows) |
| `src/enhance_recording.py`, `src/import_usb.py` | Hörspur erzeugen, USB-WAV übernehmen |
| `src/library.py` | Aufnahmeliste, Export |
| `installer/` | Windows-Installation, Icon |

Jeder Push startet einen GitHub-Actions-Lauf, der die Windows-Installation auf einem echten Windows-Rechner
durchspielt: Installer, Tests, Sprache erzeugen → Hörspur → Transkript, App-Start, Deinstallation.

Die Transkription arbeitet offline und blockiert ausgehende Verbindungen; nur der einmalige Modell-Download
braucht Internet.
