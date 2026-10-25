"""Native-quality recording, live/chunked ASR and deferred processing modes."""
import json
import queue
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton, QTextEdit
from app import Studio
from app_core import ROOT
from engine import InputSpec, Recorder


class IntegratedStudio(Studio):
    def __init__(self):
        self.managed_pipeline = True
        self.live_stt = None
        self.live_rec = None
        self.live_buffer = ''
        self.live_errors = ''
        self.live_submitted = 0
        self.live_completed = 0
        self.live_finishing = False
        self.deferred_pending = False
        self.monitor_device = None
        self.monitor_volume = .35
        self.native_paths = []
        super().__init__()
        self.pipeline_mode = QComboBox()
        self.pipeline_mode.addItem('Live verbessern + fortlaufender Text', 'live')
        self.pipeline_mode.addItem('Erst aufnehmen · danach verarbeiten', 'deferred')
        self.pipeline_mode.setToolTip('Live: Text wächst während der Aufnahme. Danach: Aufnahme zuerst, Verbesserung und Text nach Stopp.')
        self.processing_box.insertWidget(1, self.pipeline_mode)
        self.chunk_size = QComboBox()
        for seconds in (3, 5, 10):
            self.chunk_size.addItem(f'Text alle {seconds} s', seconds)
        self.chunk_size.setCurrentIndex(1)
        self.chunk_size.setToolTip('Audioabschnitte; hinzu kommen Modellladezeit und Rechenzeit. Bei Rückstau 10 s wählen.')
        self.processing_box.insertWidget(2, self.chunk_size)
        self.pipeline_mode.currentIndexChanged.connect(
            lambda: self.chunk_size.setVisible(self.pipeline_mode.currentData() == 'live'))
        self.live_monitor = QCheckBox('Live abhören')
        self.live_monitor.setToolTip('Optional über Kopfhörer. Lautsprecher können Rückkopplungen erzeugen.')
        self.processing_box.addWidget(self.live_monitor)
        self.stream_status = QLabel('Native Eingänge werden zusätzlich mit allen Kanälen als PCM32-WAV erhalten.')
        self.stream_status.setObjectName('muted')
        self.stream_status.setWordWrap(True)
        self.stream_row.addWidget(self.stream_status, 1)
        self.stop_live_text_button = QPushButton('Live-Text stoppen')
        self.stop_live_text_button.clicked.connect(self.cancel_live_text)
        self.stop_live_text_button.hide()
        self.stream_row.addWidget(self.stop_live_text_button)
        self.highlight_timer = QTimer(self)
        self.highlight_timer.setSingleShot(True)
        self.highlight_timer.timeout.connect(lambda: self.transcript.setExtraSelections([]))
        self.auto_transcribe.setText('Transkription aktivieren')
        self.auto_transcribe.setChecked(True)
        self.native_button = QPushButton('Native Eingangsspur öffnen …')
        self.native_button.setObjectName('link')
        self.native_button.clicked.connect(self.open_native)
        self.file_actions.insertWidget(1, self.native_button)

    def cancel_live_text(self):
        if self.live_stt:
            self.live_stt.kill()

    def open_native(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, 'Unveränderte Eingangsspur', str(self.folder), 'Native WAV (*_native.wav)')
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def record_toggle(self):
        if self.recorder:
            self.recorder.stop()
            self.record_button.setEnabled(False)
            self.status.setText('Aufnahme wird vollständig gespeichert …')
            return
        if self.live_stt:
            self.stream_status.setText('Live-Text arbeitet noch. Bitte bis zum Abschluss warten.')
            return
        specs = [InputSpec(index, name, channel.value()-1, gain.value(), delay.value())
                 for index,name,active,channel,gain,delay,_ in self.rows if active.isChecked()]
        if not specs:
            self.status.setText('Bitte mindestens ein Mikrofon aktivieren.')
            return
        self.player.close()
        self.transcript.clear()
        self.stream_status.setText('Native Aufnahme läuft; zusätzliche Verarbeitung nach Stopp.' if self.pipeline_mode.currentData()=='deferred' else 'Live-Verarbeitung wird vorbereitet …')
        self.save_preferences()
        pipeline = self.pipeline_mode.currentData()
        self.recorder = Recorder(specs, self.folder, self.strength.currentData(), self.mode.currentData(),
            save_raw=True, wet=self.wet, pipeline=pipeline,
            monitor=self.live_monitor.isChecked(), monitor_device=self.monitor_device,
            monitor_volume=self.monitor_volume, segment_seconds=self.chunk_size.currentData())
        self.live_rec = self.recorder if pipeline == 'live' else None
        self.deferred_pending = pipeline == 'deferred'
        self.live_finishing = False
        self.recorder.start()
        self.set_record_controls(False)
        self.record_button.setText('■  Aufnahme stoppen')
        self.status.setText('Native Aufnahme und lokale Verarbeitung werden vorbereitet …')
        self.pipeline_mode.setEnabled(False)
        self.chunk_size.setEnabled(False)
        self.live_monitor.setEnabled(False)
        self.auto_transcribe.setEnabled(False)
        if pipeline == 'live' and self.auto_transcribe.isChecked():
            self.start_live_text()

    def start_live_text(self):
        if not (ROOT/'models/qwen3-asr/.ready.json').exists():
            self.stream_status.setText('Live-Text: lokales Modell fehlt. Audioaufnahme läuft weiter.')
            return
        self.transcript.clear()
        self.live_submitted = self.live_completed = 0
        self.live_buffer = self.live_errors = ''
        self.live_stt = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY'):
            env.insert(key, '1')
        self.live_stt.setProcessEnvironment(env)
        self.live_stt.readyReadStandardOutput.connect(self.read_live_text)
        self.live_stt.readyReadStandardError.connect(self.read_live_errors)
        self.live_stt.started.connect(self.send_segments)
        self.live_stt.finished.connect(self.live_text_finished)
        self.live_stt.errorOccurred.connect(self.live_text_error)
        self.live_stt.start(sys.executable, ['-u', str(ROOT/'src/stream_transcribe.py'),
            '--model', str(ROOT/'models/qwen3-asr'), '--language', self.language.currentData()])
        self.stream_status.setText('Live-Text: Modell wird einmal für diese Aufnahme geladen …')
        self.transcribe_button.setEnabled(False)

    def read_live_errors(self):
        if self.live_stt:
            self.live_errors = (self.live_errors + bytes(self.live_stt.readAllStandardError()).decode('utf-8', errors='replace'))[-6000:]

    def read_live_text(self):
        if not self.live_stt:
            return
        self.live_buffer += bytes(self.live_stt.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self.live_buffer:
            line, self.live_buffer = self.live_buffer.split('\n', 1)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if 'text' in event:
                self.show_live_text(event['text'])
            if 'completed' in event:
                self.live_completed = event['completed']
                pending = self.live_submitted-self.live_completed
                self.stream_status.setText(f"Live-Text: {self.live_completed} Abschnitte fertig · {pending} warten · zuletzt {event['inference_seconds']:.1f} s Rechenzeit")
            elif event.get('status'):
                self.stream_status.setText(event['status'])
            elif event.get('finished'):
                self.stream_status.setText('Live-Text vollständig gespeichert · Aufnahme und Transkript bleiben lokal.')

    def show_live_text(self, text):
        old = self.transcript.toPlainText()
        if text == old:
            return
        bar = self.transcript.verticalScrollBar()
        following = bar.value() >= bar.maximum() - 4
        self.transcript.setPlainText(text)
        if following:
            bar.setValue(bar.maximum())
        # Briefly mark the words this chunk added.
        start = len(old.rstrip()) if old and text.startswith(old.rstrip()) else 0
        if len(text) > start:
            mark = QTextEdit.ExtraSelection()
            mark.cursor = QTextCursor(self.transcript.document())
            mark.cursor.setPosition(start)
            mark.cursor.setPosition(len(text), QTextCursor.MoveMode.KeepAnchor)
            mark.format.setBackground(QColor('#1d4a44'))
            self.transcript.setExtraSelections([mark])
            self.highlight_timer.start(1800)

    def send_segments(self):
        if not self.live_rec or not self.live_stt or self.live_stt.state()!=QProcess.Running:
            return
        while True:
            try:
                segment = self.live_rec.segments.get_nowait()
            except queue.Empty:
                break
            path = Path(segment['audio'])
            stem = path.parent.name.removesuffix('_live_segments')
            segment['target'] = str(path.parent.parent/(stem+'_clean.txt'))
            self.live_stt.write((json.dumps(segment)+'\n').encode('utf-8'))
            self.live_submitted += 1
        if self.live_rec.state['status'] in ('done','error') and not self.live_finishing:
            self.live_stt.write(b'{"finish":true}\n')
            self.live_finishing = True

    def live_text_error(self, error):
        if self.live_stt and self.live_stt.state()==QProcess.NotRunning:
            self.live_text_finished(-1, None)

    def live_text_finished(self, code, status):
        if not self.live_stt:
            return
        self.read_live_text()
        self.read_live_errors()
        process, self.live_stt = self.live_stt, None
        process.deleteLater()
        if code:
            self.stream_status.setText('Live-Text beendet: '+(self.live_errors[-300:] or 'Prozess konnte nicht starten. Audio bleibt gespeichert.'))
        self.transcribe_button.setEnabled(self.recorder is None and self.last is not None)
        self.release_pipeline_controls()

    def release_pipeline_controls(self):
        if self.recorder is None and self.live_stt is None and self.enhance_process is None and self.voxtral is None:
            for widget in (self.pipeline_mode, self.chunk_size, self.live_monitor, self.auto_transcribe):
                widget.setEnabled(True)
            self.record_button.setEnabled(True)
            self.set_record_controls(True)

    def tick(self):
        previous = self.recorder
        super().tick()
        finished = previous is not None and self.recorder is None
        self.send_segments()
        self.stop_live_text_button.setVisible(self.live_stt is not None)
        if self.recorder and self.recorder.pipeline == 'deferred':
            self.status.setText('Native Aufnahme läuft · Klangverbesserung und Text folgen nach Stopp.')
            self.waveform.processed_title = 'NACH STOPP'
            self.waveform.clean[:] = 0
        elif self.recorder:
            self.waveform.processed_title = 'VERBESSERT'
        if self.enhance_process or self.voxtral or (self.live_stt and self.recorder is None):
            self.set_badge('VERARBEITUNG')
        if self.recorder and self.recorder.state.get('monitor_error'):
            self.stream_status.setText(self.recorder.state['monitor_error'])
        if finished:
            if previous.result:
                self.native_paths = [entry['file'] for entry in previous.result['native_inputs']]
                native = previous.result['native_inputs'][0]
                self.status.setText(f"Native Quelle: {native['sample_rate']:g} Hz · {native['channels']} Kanal/Kanäle · PCM32. Vergleich/zusätzliche KI: 48 kHz.")
                if self.deferred_pending and not previous.error:
                    self.deferred_pending = False
                    self.reprocess()
            if previous.error:
                self.deferred_pending = False
        if self.live_stt:
            self.transcribe_button.setEnabled(False)
            self.record_button.setEnabled(self.recorder is not None and self.record_button.isEnabled())
            self.enhance_button.setEnabled(False)
            self.open_pair.setEnabled(False)
        self.release_pipeline_controls()

    def load_pair(self, clean, original):
        super().load_pair(clean, original)
        self.waveform.processed_title = 'VERBESSERT' if clean != original else 'NACH STOPP'
        if clean == original:
            self.waveform.clean[:] = 0
            self.source_label.setText('Original · noch nicht verbessert')

    def enhance_finished(self, code, status):
        super().enhance_finished(code, status)
        if code == 0 and self.pipeline_mode.currentData() == 'deferred' and self.auto_transcribe.isChecked():
            self.transcribe()
        self.release_pipeline_controls()

    def stt_finished(self, code, status):
        super().stt_finished(code, status)
        self.release_pipeline_controls()
