# LocalVoice + reSpeaker

**App:** `/Users/benlamprecht/Documents/ChatGPT/Mem Mic/LocalVoice-reSpeaker.app`

**Python-Quellcode:** `/Users/benlamprecht/Documents/ChatGPT/Mem Mic/LocalVoiceCombo/src/`

Starten:

```sh
open "/Users/benlamprecht/Documents/ChatGPT/Mem Mic/LocalVoice-reSpeaker.app"
```

Direkter Python-Start (auch zum Entwickeln):

```sh
cd "/Users/benlamprecht/Documents/ChatGPT/Mem Mic"
LocalVoiceCombo/.venv/bin/python LocalVoiceCombo/src/combined.py
```

## Zwei Arbeitsweisen

1. **Live verbessern + fortlaufender Text:** DeepFilterNet verarbeitet 10-ms-Audioblöcke. Qwen3-ASR lädt einmal pro Aufnahme und transkribiert fortlaufend Abschnitte von wahlweise 3, 5 oder 10 Sekunden. Die Abschnitte überlappen um 0,5 Sekunden. Der Text wächst schon während der Aufnahme. Modelllade- und Rechenzeit kommen hinzu; Rückstau wird angezeigt. Dies ist abschnittsweise Transkription, kein ASR-Modell mit nativer wortweiser Streaming-Schnittstelle.
2. **Erst aufnehmen · danach verarbeiten:** Während der Aufnahme wird kein DeepFilterNet-Modell geladen. Nach Stopp wird eine verbesserte Version erzeugt und bei aktivierter Transkription der Text berechnet. Vorhandene Originale und vorherige Versionen bleiben bestehen.

„Live abhören“ gibt das aktuelle Audio optional über den gewählten Ausgang aus. Kopfhörer eignen sich dafür; Lautsprecher können Rückkopplungen verursachen. „Abhörpegel“ verändert die Wiedergabe, nicht die Aufnahme. Im nachträglichen Modus wird beim Live-Abhören das noch nicht zusätzlich verbesserte Signal ausgegeben.

## Eingangsqualität

Jeder aktivierte Eingang wird mit **allen angebotenen Kanälen** in seiner aktuellen nativen Abtastrate zusätzlich als `*_inputN_native.wav` gespeichert. Das PCM32-Containerformat erfindet keine zusätzlichen Hardware-Bits. Die Originaldatei erhält den USB-Eingang vor Gain, Kanalauswahl, Mischbetrieb und DeepFilterNet.

Der angeschlossene ReSpeaker Lite meldet über Core Audio am 25.10.2026 ausschließlich **16.000 Hz**. Echte 48 kHz sind mit seinem aktuellen USB-Profil nicht verfügbar. Das MacBook-Mikrofon liefert aktuell echte 48 kHz. Die App ändert keine Firmware. Der USB-Test zeigt nur ausgelesene native Abtastraten an und lehnt nicht unterstützte Hardware-Raten ab.

DeepFilterNet benötigt 48 kHz. Die Vergleichsspuren `*_original.wav` und `*_clean.wav` sind deshalb Mono-PCM32 mit 48 kHz; sie sind von der nativen Eingangsspur abgeleitet. „Original halten“ vergleicht das Board-/Mikrofonsignal vor zusätzlicher KI mit der KI-Spur an derselben Abspielposition. Der Originalvergleich berücksichtigt die Modellverzögerung. Für unveränderte volle Eingangskanäle die separate native WAV verwenden.

## Integrierte Mem-Mic-Funktionen

Der zweite Reiter **reSpeaker · USB-Test** enthält Pegel/Wellenform pro USB-Kanal, Ruhepegelmessung, vollständige USB-WAV-Aufnahme, Kanalwahl beim Anhören, Ausgangsauswahl, Testton und USB-Diagnose. „Letzte USB-Aufnahme im Studio verbessern“ übernimmt eine gewählte Spur oder den Mix in LocalVoice. „USB-WAV ins Studio …“ öffnet auch bereits gespeicherte Dateien.

Der Reiterwechsel wird während Aufnahme oder Verarbeitung gesperrt, damit nicht zwei Recorder oder widersprüchliche Einstellungen gleichzeitig laufen. Eine laufende reine Pegelanzeige wird beim Wechsel ins Studio beendet. Der USB-Test steuert keine undokumentierten DSP-/RGB-Register. Originalaufnahmen werden nicht überschrieben.

## Lokal gespeicherte Dateien

- `LocalVoiceCombo/Aufnahmen/`: Studio-Aufnahmen, native Eingänge, Versionen, Metadaten, Text und Live-Abschnitte.
- `LocalVoiceCombo/recordings/`: neue USB-Testaufnahmen. Bisherige Testaufnahmen liegen zusätzlich im übergeordneten `recordings/` und können importiert werden.
- `LocalVoiceCombo/Logs/app.log`: Start-/Fehlerprotokoll der App.
- `LocalVoiceCombo/Pruefaufnahmen/`: die bei der Entwicklung erzeugten kurzen Prüfaufnahmen.

Die alte LocalVoice-Installation bleibt unverändert. Die neue App nutzt deren bestehende Python-Umgebung und ASR-Modelle über lokale Verknüpfungen. `LocalVoiceCombo/.venv` und `LocalVoiceCombo/models/qwen3-asr` zeigen deshalb auf die alte Installation. Diese Basis nicht löschen oder verschieben, solange sie als Laufzeit dient. Es wurden keine zusätzlichen ASR-Modelle heruntergeladen. Die ASR-Prozesse arbeiten offline und sperren ausgehende Verbindungen.

## Was du testen kannst (jeweils unter zwei Minuten)

1. **Live-Text:** ReSpeaker wählen, „Live verbessern + fortlaufender Text“, „Transkription aktivieren“. 15 Sekunden sprechen. Der Text sollte vor Stopp wachsen; danach wird auch der Rest gespeichert.
2. **Nachträglich:** „Erst aufnehmen · danach verarbeiten“ wählen, 10 Sekunden sprechen, stoppen. Klangverbesserung und Text sollten erst nach Stopp beginnen.
3. **A/B:** Eine fertige Aufnahme abspielen, „Original halten“ gedrückt halten und loslassen. Die Wiedergabe sollte ohne Positionssprung wechseln.
4. **USB-Test/Mute:** Im USB-Reiter Pegel starten, sprechen und die Mute-Taste am Board drücken. Danach eine WAV speichern und mit dem Studio-Knopf übernehmen.
5. **Qualität:** Eine `*_native.wav` prüfen: beim aktuellen ReSpeaker 16 kHz, zwei Kanäle, PCM32; Vergleichsspuren 48 kHz, mono, PCM32. Bei MacBook-Eingang soll die native Datei 48 kHz enthalten.

## Entwicklung und Prüfungen

`src/combined.py`: gemeinsame Oberfläche, Reiter und Importübergabe. `src/studio_extension.py`: Betriebsmodi und Live-Text-Steuerung. `src/engine.py`: native Aufnahme, zusätzliche KI, Abschnittsbildung. `src/stream_transcribe.py`: persistenter lokaler ASR-Prozess. `src/usb_panel.py` und `src/usb_audio.py`: integrierter USB-Test. `src/playback.py`: synchroner Originalvergleich und Ausgangsanpassung.

```sh
LocalVoiceCombo/.venv/bin/python -m unittest discover -s LocalVoiceCombo/tests -v
zsh LocalVoiceCombo/build.sh
```

Quellcodeänderungen sind nach einem App-Neustart aktiv. Ein Neubau ist nur für Änderungen am nativen Starter bzw. Bundle nötig. Die Hardware-Prüfskripte starten echte lokale Aufnahmen; nicht während einer wichtigen Aufnahme parallel ausführen.
