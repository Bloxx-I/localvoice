from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QProcess, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox, QTableWidget,
    QTextEdit, QVBoxLayout, QWidget, QHeaderView)

from engine import InputSpec, Recorder, input_devices
from playback import ComparisonPlayer

ROOT = Path(__file__).resolve().parent.parent


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('LocalVoice · Aufnahme & Transkription')
        self.resize(930, 890)
        self.recorder = None
        self.player = ComparisonPlayer()
        self.last = None
        self.voxtral = None
        self.rows = []
        self.folder = ROOT / 'Aufnahmen'
        self.config_path = ROOT / 'preferences.json'
        try:
            self.preferences = json.loads(self.config_path.read_text(encoding='utf-8'))
            self.folder = Path(self.preferences.get('folder', str(self.folder)))
        except (OSError, ValueError):
            self.preferences = {}
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(14)
        top = QHBoxLayout()
        title = QLabel('LocalVoice')
        title.setObjectName('title')
        top.addWidget(title)
        top.addStretch()
        badge = QLabel('●  AUDIO BLEIBT AUF DIESEM MAC')
        badge.setObjectName('badge')
        top.addWidget(badge)
        layout.addLayout(top)
        layout.addWidget(QLabel('Stimme isolieren. Aufnahme vergleichen. Lokal transkribieren.'))

        inputs = QGroupBox('1   Mikrofone auswählen')
        il = QVBoxLayout(inputs)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Aktiv', 'Mikrofon', 'Kanal', 'Gain · dB', 'Delay · ms', 'Pegel'])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 55)
        for col in [2, 3, 4]: self.table.setColumnWidth(col, 100)
        self.table.setColumnWidth(5, 125)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(180)
        il.addWidget(self.table)
        refresh = QPushButton('Geräte aktualisieren')
        refresh.clicked.connect(self.refresh)
        self.refresh_button = refresh
        il.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignRight)
        self.mode = QComboBox()
        self.mode.addItem('Automatisch das Mikrofon mit dem besten KI-Sprachsignal wählen', 'auto')
        self.mode.addItem('Ausgewählte Mikrofone mischen · Delay bei Bedarf anpassen', 'mix')
        il.addWidget(self.mode)
        note = QLabel('Für mehrere Mikrofone: Automatik zuerst testen. Im Mischmodus können Zeitversätze den Klang verändern.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        il.addWidget(note)
        layout.addWidget(inputs)

        record = QGroupBox('2   Aufnahme')
        rl = QVBoxLayout(record)
        strengthrow = QHBoxLayout()
        strengthrow.addWidget(QLabel('Rauschunterdrückung'))
        self.strength = QComboBox()
        for label, value in [('Sanft · 15 dB', 15), ('Ausgewogen · 30 dB', 30), ('Stark · 60 dB', 60)]:
            self.strength.addItem(label, value)
        self.strength.setCurrentIndex(1)
        strengthrow.addWidget(self.strength)
        strengthrow.addStretch()
        self.auto_transcribe = QCheckBox('Nach Stopp transkribieren')
        strengthrow.addWidget(self.auto_transcribe)
        rl.addLayout(strengthrow)
        row = QHBoxLayout()
        self.record_button = QPushButton('●  Aufnahme starten')
        self.record_button.setObjectName('primary')
        self.record_button.clicked.connect(self.record_toggle)
        row.addWidget(self.record_button)
        self.timer_label = QLabel('00:00.0')
        self.timer_label.setObjectName('clock')
        row.addWidget(self.timer_label)
        self.output_meter = QProgressBar()
        self.output_meter.setRange(0, 100)
        self.output_meter.setTextVisible(False)
        row.addWidget(self.output_meter)
        rl.addLayout(row)
        folderrow = QHBoxLayout()
        self.folder_label = QLabel(str(self.folder))
        self.folder_label.setObjectName('muted')
        folderrow.addWidget(self.folder_label, 1)
        self.folder_button = QPushButton('Speicherort wählen')
        self.folder_button.clicked.connect(self.choose_folder)
        folderrow.addWidget(self.folder_button)
        rl.addLayout(folderrow)
        self.status = QLabel('Bereit. Clean und Original werden als WAV gespeichert.')
        self.status.setWordWrap(True)
        rl.addWidget(self.status)
        layout.addWidget(record)

        compare = QGroupBox('3   Anhören & vergleichen')
        cl = QVBoxLayout(compare)
        self.file_label = QLabel('Noch keine Aufnahme')
        self.file_label.setObjectName('muted')
        cl.addWidget(self.file_label)
        playrow = QHBoxLayout()
        self.play_button = QPushButton('▶  Abspielen')
        self.play_button.clicked.connect(self.play_toggle)
        self.play_button.setEnabled(False)
        playrow.addWidget(self.play_button)
        self.hold_button = QPushButton('Original · gedrückt halten')
        self.hold_button.setObjectName('hold')
        self.hold_button.setEnabled(False)
        self.hold_button.setAutoRepeat(False)
        self.hold_button.pressed.connect(lambda: self.set_original(True))
        self.hold_button.released.connect(lambda: self.set_original(False))
        playrow.addWidget(self.hold_button)
        self.source_label = QLabel('KI verbessert')
        playrow.addWidget(self.source_label)
        playrow.addStretch()
        open_button = QPushButton('Ordner öffnen')
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        playrow.addWidget(open_button)
        cl.addLayout(playrow)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(lambda v: self.player.seek(v/1000))
        cl.addWidget(self.seek)
        layout.addWidget(compare)

        transcribe = QGroupBox('4   Voxtral Small · lokale Transkription')
        tl = QVBoxLayout(transcribe)
        tr = QHBoxLayout()
        self.language = QComboBox()
        for name, code in [('Deutsch', 'de'), ('English', 'en'), ('Français', 'fr'), ('Español', 'es')]:
            self.language.addItem(name, code)
        tr.addWidget(self.language)
        self.transcribe_button = QPushButton('Aufnahme transkribieren')
        self.transcribe_button.clicked.connect(self.transcribe)
        self.transcribe_button.setEnabled(False)
        tr.addWidget(self.transcribe_button)
        tr.addStretch()
        self.cancel_transcribe = QPushButton('Abbrechen')
        self.cancel_transcribe.clicked.connect(self.cancel_stt)
        self.cancel_transcribe.setEnabled(False)
        tr.addWidget(self.cancel_transcribe)
        tl.addLayout(tr)
        self.stt_status = QLabel('Voxtral Small 24B · lokale 4-Bit-Version · Modell wird vorbereitet')
        self.stt_status.setWordWrap(True)
        self.stt_status.setObjectName('muted')
        tl.addWidget(self.stt_status)
        self.transcript = QTextEdit()
        self.transcript.setPlaceholderText('Das Transkript erscheint hier und wird neben der WAV-Datei gespeichert.')
        self.transcript.setMinimumHeight(130)
        tl.addWidget(self.transcript)
        layout.addWidget(transcribe, 1)
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(80)
        QApplication.instance().installEventFilter(self)
        # Restore most recent complete paired recording when reopening.
        try:
            files = sorted(self.folder.glob('*_clean.wav'))
            if files:
                raw = Path(str(files[-1]).replace('_clean.wav', '_original.wav'))
                if raw.exists(): self.load_pair(str(files[-1]), str(raw))
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate):
            self.set_original(False)
            self.hold_button.setDown(False)
        return False

    def set_original(self, enabled):
        self.player.original = enabled
        self.source_label.setText('Original' if enabled else 'KI verbessert')

    def refresh(self):
        try:
            devices = input_devices()
            self.rows = []
            self.table.setRowCount(len(devices))
            saved = self.preferences.get('inputs', {})
            for r, (index, info) in enumerate(devices):
                name = info['name']
                prefs = saved.get(name, {})
                active = QCheckBox()
                active.setChecked(prefs.get('active', 'MacBook' in name and 'Microphone' in name))
                label = QLabel(name)
                channel = QSpinBox()
                channel.setRange(1, info['max_input_channels'])
                channel.setValue(prefs.get('channel', 1))
                gain = QDoubleSpinBox()
                gain.setRange(-24, 24)
                gain.setValue(prefs.get('gain_db', 0))
                delay = QSpinBox()
                delay.setRange(0, 250)
                delay.setValue(prefs.get('delay_ms', 0))
                meter = QProgressBar()
                meter.setRange(0, 100)
                meter.setTextVisible(False)
                for c, w in enumerate([active, label, channel, gain, delay, meter]):
                    self.table.setCellWidget(r, c, w)
                self.table.setRowHeight(r, 36)
                self.rows.append((index, name, active, channel, gain, delay, meter))
            if not devices:
                self.status.setText('Keine Mikrofone sichtbar. App außerhalb der Entwicklungsumgebung öffnen.')
        except Exception as e:
            self.status.setText(f'Geräte konnten nicht gelesen werden: {e}')

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Aufnahmen speichern', str(self.folder))
        if path:
            self.folder = Path(path)
            self.folder_label.setText(path)
            self.save_preferences()

    def save_preferences(self):
        data = {'folder': str(self.folder), 'inputs': {name: {'active': active.isChecked(),
            'channel': channel.value(), 'gain_db': gain.value(), 'delay_ms': delay.value()}
            for _, name, active, channel, gain, delay, _ in self.rows}}
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        self.preferences = data

    def record_toggle(self):
        if self.recorder:
            self.recorder.stop()
            self.record_button.setEnabled(False)
            self.status.setText('Aufnahme wird fertig gespeichert …')
            return
        specs = [InputSpec(index, name, channel.value()-1, gain.value(), delay.value())
            for index, name, active, channel, gain, delay, _ in self.rows if active.isChecked()]
        if not specs:
            self.status.setText('Bitte mindestens ein Mikrofon aktivieren.')
            return
        self.player.close()
        self.save_preferences()
        self.recorder = Recorder(specs, self.folder, self.strength.currentData(), self.mode.currentData(), save_raw=True, wet=getattr(self,'wet',1.0))
        self.recorder.start()
        self.set_record_controls(False)
        self.record_button.setText('■  Aufnahme stoppen')
        self.status.setText('Lokale KI wird geladen …')

    def set_record_controls(self, enabled):
        for w in [self.table, self.mode, self.strength, self.refresh_button, self.folder_button]:
            w.setEnabled(enabled)
        self.play_button.setEnabled(enabled and self.last is not None)
        self.hold_button.setEnabled(enabled and self.last is not None)
        self.transcribe_button.setEnabled(enabled and self.last is not None and self.voxtral is None)

    def load_pair(self, clean, original):
        delay = 480
        try:
            metadata = Path(str(clean).replace('_clean.wav','.json'))
            delay = json.loads(metadata.read_text(encoding='utf-8')).get('model_delay_samples',480)
        except (OSError,ValueError):
            pass
        self.player.load(clean, original, delay_samples=delay)
        self.last = {'clean': clean, 'original': original}
        self.file_label.setText(Path(clean).name)
        self.seek.setRange(0, int(len(self.player.clean)/self.player.rate*1000))
        self.play_button.setEnabled(True)
        self.hold_button.setEnabled(True)
        self.transcribe_button.setEnabled(self.voxtral is None)

    def play_toggle(self):
        try:
            self.player.toggle()
        except Exception as e:
            self.status.setText(f'Wiedergabe: {e}')

    def transcribe(self):
        if not self.last or self.voxtral: return
        if not (ROOT/'models/qwen3-asr/.ready.json').exists():
            self.stt_status.setText('Qwen3-ASR wird noch heruntergeladen. Aufnahme und Vergleich funktionieren bereits.')
            return
        self.transcript.clear()
        self.voxtral_buffer = ''
        self.voxtral_error = ''
        self.voxtral = QProcess(self)
        env = self.voxtral.processEnvironment()
        # Inherit normal environment but prohibit hub access during inference.
        from PySide6.QtCore import QProcessEnvironment
        env = QProcessEnvironment.systemEnvironment()
        for key, value in {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false'}.items():
            env.insert(key, value)
        self.voxtral.setProcessEnvironment(env)
        self.voxtral.readyReadStandardOutput.connect(self.stt_output)
        self.voxtral.readyReadStandardError.connect(self.stt_stderr)
        self.voxtral.finished.connect(self.stt_finished)
        self.voxtral.errorOccurred.connect(lambda e: self.stt_status.setText(f'Transkription konnte nicht starten: {e}'))
        self.voxtral.start(sys.executable, ['-u', str(ROOT/'src/transcribe.py'), self.last['clean'],
            '--language', self.language.currentData(), '--model', str(ROOT/'models/qwen3-asr'),
            '--context', self.asr_context(), '--source', self.asr_source()])
        self.transcribe_button.setEnabled(False)
        self.record_button.setEnabled(False)
        self.cancel_transcribe.setEnabled(True)
        self.stt_status.setText('Qwen3-ASR wird lokal geladen …')

    def asr_context(self):
        return ''

    def asr_source(self):
        return 'board'

    def stt_progress(self, event):
        pass

    def stt_output(self):
        self.voxtral_buffer += bytes(self.voxtral.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self.voxtral_buffer:
            line, self.voxtral_buffer = self.voxtral_buffer.split('\n', 1)
            try:
                event = json.loads(line)
                if event.get('status'): self.stt_status.setText(event['status'])
                self.stt_progress(event)
                if event.get('text') is not None: self.transcript.setPlainText(event['text'])
            except ValueError:
                pass

    def stt_stderr(self):
        self.voxtral_error += bytes(self.voxtral.readAllStandardError()).decode('utf-8', errors='replace')[-6000:]

    def stt_finished(self, code, status):
        self.stt_output()
        self.stt_stderr()
        if code:
            self.stt_status.setText('Transkription beendet: ' + (self.voxtral_error[-350:] or 'Abgebrochen.'))
        self.voxtral.deleteLater()
        self.voxtral = None
        self.transcribe_button.setEnabled(self.last is not None)
        self.record_button.setEnabled(True)
        self.cancel_transcribe.setEnabled(False)

    def cancel_stt(self):
        if self.voxtral: self.voxtral.kill()

    def tick(self):
        if self.recorder:
            state = self.recorder.state.copy()
            seconds = state['seconds']
            self.timer_label.setText(f'{int(seconds)//60:02}:{seconds%60:04.1f}')
            self.output_meter.setValue(self.meter_value(state['output']))
            selected = [row for row in self.rows if row[2].isChecked()]
            for row, level in zip(selected, state['levels']):
                row[-1].setValue(self.meter_value(level))
            if state['status'] == 'recording':
                active = self.recorder.specs[state['active']].name
                missing = sum(state['missing_frames'])
                detail = f' · {missing} fehlende Zusatzmikrofon-Blöcke' if missing else ''
                self.status.setText(f'Aufnahme läuft · {active} · KI {state["processing_ms"]:.1f} ms / 10-ms-Block{detail}')
            if state['status'] in ('error', 'done'):
                rec = self.recorder
                self.recorder = None
                self.set_record_controls(True)
                self.record_button.setEnabled(True)
                self.record_button.setText('●  Aufnahme starten')
                if rec.error:
                    self.status.setText('Aufnahme: ' + rec.error)
                elif rec.result and rec.result.get('clean'):
                    self.load_pair(rec.result['clean'], None)
                    self.status.setText(f'Gespeichert · {rec.result["duration_seconds"]:.1f} s')
                    if self.auto_transcribe.isChecked() and not getattr(self,'managed_pipeline',False): self.transcribe()
        self.play_button.setText('Ⅱ  Pause' if self.player.playing else '▶  Abspielen')
        if not self.seek.isSliderDown(): self.seek.setValue(int(self.player.position/self.player.rate*1000))
        if self.player.error:
            self.status.setText('Wiedergabe: ' + self.player.error)
            self.player.error = None
        if self.voxtral is None and (ROOT/'models/qwen3-asr/.ready.json').exists() and self.stt_status.text().endswith('vorbereitet'):
            self.stt_status.setText('Qwen3-ASR 1.7B · bereit · vollständig lokal')

    @staticmethod
    def meter_value(level):
        import math
        return max(0, min(100, int((20*math.log10(max(level, 1e-6))+60)/60*100)))

    def closeEvent(self, event):
        self.set_original(False)
        if self.recorder:
            self.recorder.stop()
            self.status.setText('Aufnahme wird gespeichert. Fenster danach schließen.')
            event.ignore()
            return
        if self.voxtral:
            self.cancel_stt()
            event.ignore()
            return
        self.player.close()
        self.save_preferences()
        event.accept()


STYLE = '''
QWidget { background: #151b24; color: #edf3f8; font-size: 13px; }
QLabel#title { font-size: 32px; font-weight: 700; }
QLabel#badge { color: #74dec1; font-size: 11px; font-weight: 600; }
QLabel#muted { color: #a6b5c5; font-size: 12px; }
QLabel#clock { font-size: 26px; font-family: Menlo; min-width: 132px; }
QGroupBox { border: 1px solid #344052; border-radius: 10px; margin-top: 14px; padding: 18px 14px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 6px; color: #a6b5c5; }
QPushButton { background: #273446; border: 1px solid #40536a; border-radius: 7px; padding: 9px 14px; }
QPushButton:hover { background: #34465d; }
QPushButton:pressed { background: #435b77; }
QPushButton:disabled { color: #647184; background: #1a222e; border-color: #273446; }
QPushButton#primary { background: #74dec1; color: #0c2524; font-weight: 700; padding: 12px 20px; }
QPushButton#hold:pressed { background: #f2c16f; color: #261c0e; }
QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit { border: 1px solid #40536a; border-radius: 5px; padding: 6px; background: #1d2734; }
QTableWidget { border: none; background: #1a2330; gridline-color: #273446; }
QHeaderView::section { background: #202c3b; border: none; padding: 6px; color: #a6b5c5; }
QProgressBar { background: #293546; border: none; border-radius: 4px; max-height: 12px; }
QProgressBar::chunk { background: #74dec1; border-radius: 4px; }
QSlider::groove:horizontal { height: 5px; background: #34465d; }
QSlider::handle:horizontal { width: 13px; margin: -4px 0; background: #74dec1; border-radius: 6px; }
'''

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName('LocalVoice')
    app.setOrganizationName('LocalVoice')
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    sys.exit(app.exec())
