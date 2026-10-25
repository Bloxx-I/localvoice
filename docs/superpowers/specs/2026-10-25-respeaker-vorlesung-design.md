# LocalVoice für den ReSpeaker Lite – Vorlesungsmodus

Stand: 25.10.2026 · Status: Entwurf zur Freigabe

## Wozu

Ben nimmt Vorlesungen und Vorträge mit einem ReSpeaker Lite auf: der Sprecher ist weit entfernt, der Raum hallt, es gibt Nebengeräusche (Kauen, Rascheln, Lüftung). Das wichtigste Ergebnis ist ein **möglichst fehlerfreies Transkript**. Zweitens will er die Vorlesung **später nachhören** wie einen Podcast. Die App soll so einfach sein, dass sie jeder Student ohne Technikwissen bedienen kann. Ein Expertenmodus zeigt weiterhin alles.

## Ausgangslage (gemessen)

- Mit der installierten USB-Firmware liefert der ReSpeaker Lite **ein einziges Signal**, bereits vom XMOS-Chip bearbeitet (NS/AGC), und kopiert es auf beide USB-Kanäle. In 8 von 8 Aufnahmen, darunter eine neue Testaufnahme vom 25.10.2026, sind Kanal 1 und Kanal 2 bitgleich (Abweichung ≤ 1 LSB, Korrelation 1,000, Versatz 0). Abtastrate 16 kHz.
- Eine andere Lite-Firmware liefert laut Seeed-Forum höchstens „bearbeitet + ein Rohmikrofon“, und das widersprüchlich belegt. Zwei getrennte Rohmikrofone über USB sind nicht dokumentiert. **Wir flashen keine Firmware.**
- Heute: pro Aufnahme native 2-Kanal-PCM32-WAV (16 kHz) sowie `_original.wav` und `_clean.wav` (je 48 kHz, PCM32, mono). Bei 90 Minuten sind das ≈ 2,7 GB. Der `ComparisonPlayer` lädt beide 48-kHz-Spuren vollständig in den Speicher (≈ 2 GB bei 90 min) und resampelt gegebenenfalls zusätzlich.
- Transkription: `transcribe.py` schneidet starr alle 20 s und transkribiert die DeepFilterNet-Spur (`_clean.wav`). Qwen3-ASR in `mlx_audio` akzeptiert ein `system_prompt` (Kontext/Fachbegriffe).

## Ziele

1. Bestmögliches Transkript von Vorlesungen mit dem ReSpeaker Lite.
2. Ein angenehmer **Zuhör-Modus** zum Nachhören.
3. Ein **Schalter Einfach ↔ Experte**. Einfach zeigt nur Aufnehmen, Nachhören, Transkript mit Export und die Aufnahmeliste.
4. Funktioniert mit 90-Minuten-Aufnahmen: Speicherbedarf auf der Platte und im Arbeitsspeicher im Rahmen.

## Nicht-Ziele

- Andere Mikrofone (MacBook, AirPods, iPhone, Teams). Die App ist nur für den ReSpeaker Lite. Mikrofonauswahl und Mischmodus entfallen.
- Firmware flashen, DSP-Register oder LEDs steuern.
- Sprecher trennen (Diarisierung), Wortzeitstempel, Cloud-Dienste.

## Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- Der Einfach-Modus zeigt Fachbegriffe (dBFS, PCM32, Abschnitte, Pipeline) oder verlangt Entscheidungen, die die App selbst treffen kann.
- Eine Aufnahme geht verloren oder wird unvollständig gespeichert. Das gilt auch, wenn der ReSpeaker während der Aufnahme abgezogen wird.
- Audio oder Text verlassen den Mac.
- Die Transkription wird schlechter als heute.

## Wie es funktionieren soll

### Zwei Spuren mit zwei Aufgaben

- **Textspur:** das Board-Signal (16 kHz), schonend aufbereitet: Hochpass um 80 Hz und langsame Pegelangleichung, damit leise, entfernte Sprache gleichmäßig laut ist. **Kein DeepFilterNet.** Sie dient nur der Spracherkennung.
- **Hörspur:** die mit DeepFilterNet verbesserte Fassung (48 kHz), pegelnormiert, für den Zuhör-Modus.

*Annahme, die vor dem Festschreiben geprüft wird:* Die Textspur ergibt ein besseres Transkript als die DeepFilterNet-Spur. Prüfung: Mehrere echte Aufnahmen, mindestens die Testaufnahme und neue Vorlesungs-Probeaufnahmen, werden mit beiden Spuren transkribiert. Ben vergleicht die Ergebnisse nebeneinander. Gewinnt die DeepFilterNet-Spur, wird sie Standard für den Text. Im Expertenmodus bleibt eine Auswahl „Text aus: Board-Signal / verbessert“.

### Aufnahme

- Die App findet den ReSpeaker Lite selbst. Fehlt er, zeigt sie groß „ReSpeaker anschließen“ und deaktiviert den Aufnahmeknopf. Wird er während der Aufnahme abgezogen, bleibt alles bis dahin Aufgenommene gespeichert, mit einer klaren Meldung.
- Standard im Einfach-Modus: **erst aufnehmen, nach Stopp verarbeiten.** Die ganze Datei steht dann für die Pausenerkennung zur Verfügung, das ist am genauesten. Live-Text bleibt als Option im Expertenmodus.
- Pegelanzeige im Einfach-Modus nur als Ampel mit Klartext: „zu leise – näher ran“, „gut“, „zu laut“.

### Schnitte an Sprechpausen

- Die Textspur wird über Energie mit adaptiver Schwelle in Sprechabschnitte geteilt, ohne neue Abhängigkeit. Pausen ab etwa 0,4 s gelten als Schnittstelle. Abschnitte dauern 3 bis 25 s. Zu kurze werden zusammengelegt, zu lange an der leisesten Stelle geteilt. Jeder Schnitt bekommt etwa 0,2 s Rand.
- Jeder Abschnitt wird mit Start- und Endzeit transkribiert. Diese Zeiten dienen dem Mitlesen, dem Klick-zum-Springen und dem SRT/VTT-Export. Pro Abschnitt kommt ein Absatz bzw. Satzblock ins Transkript.
- Dieselbe Pausenkarte nutzt der Zuhör-Modus für „Pausen überspringen“.
- Optional im Expertenmodus: ein Feld **„Thema / Fachbegriffe“**, das als Kontext an Qwen3-ASR geht.

### Zuhör-Modus (Nachhören)

- Spielt die Hörspur von der Platte (Qt `QMediaPlayer` mit Tonhöhenkorrektur) und lädt sie nicht komplett in den Speicher.
- Bedienung: Play/Pause (Leertaste), 15 s zurück (←), 30 s vor (→), Geschwindigkeit 1× / 1,25× / 1,5× / 1,75× / 2× ohne höhere Stimme.
- **Mitlesen:** Der laufende Abschnitt wird im Transkript hervorgehoben und mitgescrollt. Ein Klick auf einen Abschnitt springt dorthin.
- **Pausen überspringen** (Expertenmodus): springt über Stellen ohne Sprache, die länger als etwa 1 s dauern.
- **Merker** (Expertenmodus): Taste M bzw. Knopf setzt eine Markierung mit Zeit, optional mit kurzer Notiz. Die Liste ist anklickbar und wird mit exportiert.
- **Weiterhören:** Die letzte Position pro Aufnahme wird gespeichert und beim Öffnen angeboten.
- Der bestehende A/B-Vergleich (Original halten / Taste O) bleibt im Expertenmodus.

### Oberfläche

- **Schalter „Einfach / Experte“** oben rechts, gespeichert in `preferences.json`. Standard für neue Nutzer: Einfach.
- **Einfach** zeigt: Aufnahmeknopf mit Status und Ampel, Wellenform/Fortschritt, Player (Play, ±15/30, Tempo), Transkript mit Mitlesen, Kopieren und Exportieren, Aufnahmeliste mit Suche. Kein USB-Test-Reiter.
- **Experte** zeigt zusätzlich: Verarbeitungsmodus (Live/Danach), Filterstärke, „Neu verbessern“, Textspur-Auswahl, Fachbegriffe, Ausgabegerät, Pausen überspringen, Merker, A/B-Vergleich, native Spur öffnen, Speicherort und den Reiter „reSpeaker · USB-Test“.
- Die Mikrofonliste entfällt. In der Seitenleiste steht stattdessen eine kleine Gerätekarte „ReSpeaker Lite · verbunden · 16 kHz“.

### Speichern

- **Native Board-Spur:** Sind die Kanäle bitgleich (wird pro Aufnahme geprüft), wird **ein** Kanal gespeichert, sonst alle. Format bleibt WAV PCM32, damit nichts verloren geht. 90 min mono ≈ 345 MB.
- **Hörspur:** FLAC, 24 Bit, 48 kHz, mono. Etwa halb so groß wie heute.
- **`_original.wav` (48 kHz) fällt weg.** Für den A/B-Vergleich im Expertenmodus wird es bei Bedarf aus der nativen Spur erzeugt, im Cache oder vorübergehend.
- Bestehende Aufnahmen im alten Format bleiben les- und abspielbar. Sie werden nicht umgewandelt oder gelöscht.
- Weitere Dateien pro Aufnahme: `<stem>.transcript.json` mit Abschnitten und Zeiten, `<stem>.listen.json` mit Merkern und Weiterhör-Position, `<stem>.txt`.

## Randbedingungen

- Python, PySide6 6.11, vorhandene `.venv` (verlinkt auf die alte LocalVoice-Installation). Keine neuen Downloads oder Modelle, keine Netzwerkzugriffe.
- macOS, Apple Silicon. Qwen3-ASR 1.7B 8-Bit über `mlx_audio`. DeepFilterNet3 über ONNX.
- Der Arbeitsspeicher muss bei 90 Minuten vernünftig bleiben: Transkription und Verbesserung arbeiten abschnittsweise bzw. streamend und nicht auf der ganzen Datei im RAM, soweit die heutigen Skripte das nicht schon tun.
- Bestehende Tests anpassen statt löschen. Neue Logik (Pausenerkennung, Abschnittsbildung, Speicherformat, Pausen-Sprungkarte) bekommt Unit-Tests.

## Dem Umsetzenden überlassen

- Genaue Schwellwerte der Pausenerkennung und der Pegelangleichung, wenn die Tests mit den vorhandenen Aufnahmen gut aussehen.
- Aufteilung in Module und Dateien.
- Layout-Details innerhalb der beschriebenen Bereiche, im bestehenden visuellen Stil.

## Calls, die Claude getroffen hat (bitte prüfen)

1. **Standard „erst aufnehmen, danach verarbeiten“** im Einfach-Modus. Live-Text nur im Experten-Modus. Die Frage „Text schon während der Vorlesung?“ ist noch offen.
2. **Textspur ohne DeepFilterNet**, bis der Vergleich mit echten Aufnahmen etwas anderes zeigt.
3. **Speicherformat:** identische Kanäle nur einmal speichern, Hörspur als FLAC 24 Bit, kein 48-kHz-Original mehr dauerhaft.
4. **Andere Mikrofone komplett entfernt**, nicht nur ausgeblendet.
5. **Pausen überspringen und Merker nur im Experten-Modus.**
