"""LocalVoice Studio and reSpeaker USB diagnostics in one coordinated app."""
import json
import os
# Worker processes inherit this: UTF-8 for pipes and files on Windows too.
os.environ['PYTHONUTF8'] = '1'
import sys
from pathlib import Path

import sounddevice as sd
from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton, QSlider, QTabWidget,
    QVBoxLayout, QWidget, QScrollArea, QLayout)
from PySide6.QtCore import Qt

from app import STYLE
from studio_extension import IntegratedStudio
from app_core import ROOT
from paths import HOST
from usb_panel import Window as USBWindow
from usb_audio import audio_devices


class Combined(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('LocalVoice + reSpeaker')
        self.resize(1240, 860)
        self.setMinimumSize(1040, 680)
        self.current_tab = 0
        self.import_process = None
        self.import_output = ''
        self.import_errors = ''
        self.studio = IntegratedStudio()
        self.usb = USBWindow()
        self.tabs = QTabWidget()
        self.tabs.setObjectName('mainTabs')
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.studio_body = self.studio.takeCentralWidget()
        self.studio.header_widget.hide()
        self.studio_body.layout().setSizeConstraint(QLayout.SetMinimumSize)
        self.studio_scroll = QScrollArea()
        self.studio_scroll.setWidgetResizable(True)
        self.studio_scroll.setWidget(self.studio_body)
        self.tabs.addTab(self.studio_scroll, 'Studio')
        self.usb_body = self.usb.takeCentralWidget()
        self.usb_body.layout().setSizeConstraint(QLayout.SetMinimumSize)
        self.usb_scroll = QScrollArea()
        self.usb_scroll.setWidgetResizable(True)
        self.usb_scroll.setWidget(self.usb_body)
        self.tabs.addTab(self.usb_scroll, 'reSpeaker · USB-Test')
        for scroll in (self.studio_scroll, self.usb_scroll):
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(18, 4, 18, 0)
        for text, name in (('◉', 'mark'), ('LocalVoice', 'brand')):
            item = QLabel(text)
            item.setObjectName(name)
            brand_row.addWidget(item)
        self.tabs.setCornerWidget(brand, Qt.TopLeftCorner)
        actions = QWidget()
        action_row = QHBoxLayout(actions)
        action_row.setContentsMargins(0, 4, 18, 0)
        action_row.setSpacing(12)
        mode = QFrame()
        mode.setObjectName('segment')
        mode_row = QHBoxLayout(mode)
        mode_row.setContentsMargins(3, 3, 3, 3)
        mode_row.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        for index, text in enumerate(('Einfach', 'Experte')):
            button = QPushButton(text)
            button.setObjectName('segmentButton')
            button.setCheckable(True)
            button.setMinimumWidth(78)
            self.mode_group.addButton(button, index)
            mode_row.addWidget(button)
        self.mode_group.idClicked.connect(lambda index: self.set_expert(index == 1))
        action_row.addWidget(mode)
        self.update_button = QPushButton('⬆  Update installieren')
        self.update_button.setObjectName('primary')
        self.update_button.hide()
        self.update_button.clicked.connect(self.install_update)
        action_row.addWidget(self.update_button)
        self.import_button = QPushButton('USB-WAV importieren …')
        self.import_button.setToolTip('Eine gespeicherte USB-Aufnahme im Studio verbessern. Die WAV bleibt unverändert.')
        self.import_button.clicked.connect(self.choose_usb)
        action_row.addWidget(self.import_button)
        local = QLabel(f'●  Lokal auf {HOST}')
        local.setObjectName('local')
        local.setToolTip('Native Quelle bleibt erhalten · Audio und Text verlassen diesen Mac nicht.')
        action_row.addWidget(local)
        self.tabs.setCornerWidget(actions, Qt.TopRightCorner)
        layout.addWidget(self.tabs)
        self.setCentralWidget(root)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.studio.refresh_button.clicked.disconnect()
        self.studio.refresh_button.clicked.connect(self.refresh_studio)
        # The embedded USB view is a diagnostic tool, not a second recorder
        # running unnoticed behind the Studio.
        old_toggle = self.usb.toggle_live
        self.usb.live_button.clicked.disconnect()
        self.usb.live_button.clicked.connect(self.toggle_usb_live)
        self._usb_toggle = old_toggle
        self.usb_import_button = QPushButton('Letzte USB-Aufnahme im Studio verbessern  →')
        self.usb_import_button.setObjectName('primary')
        self.usb_import_button.clicked.connect(self.import_latest)
        self.usb_body.layout().addWidget(self.usb_import_button)
        candidates = list((ROOT/'recordings').glob('*.wav')) + list((ROOT.parent/'recordings').glob('*.wav'))
        if candidates:
            latest = max(candidates, key=lambda p: p.stat().st_mtime)
            self.usb.saved(latest)
        self.add_output_controls()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_controls)
        self.timer.start(120)
        ready = ROOT/'models/qwen3-asr/.ready.json'
        self.studio.stt_status.setText('Qwen3-ASR · lokales Modell bereit' if ready.exists() else 'Für Text einmalig „Sprachmodell laden“ wählen.')
        self.set_expert(self.studio.expert)
        self.studio.update_check_button.clicked.connect(lambda: self.check_update(manual=True))
        QTimer.singleShot(4000, self.check_update)

    # --- updates via the home server ---------------------------------------------

    def check_update(self, manual=False):
        process = QProcess(self)
        def done(code, status):
            out = bytes(process.readAllStandardOutput()).decode('utf-8', 'replace').strip()
            err = bytes(process.readAllStandardError()).decode('utf-8', 'replace').strip()
            process.deleteLater()
            try:
                info = json.loads(out.splitlines()[-1])
            except (IndexError, ValueError):
                if manual:
                    self.statusBar().showMessage('Update-Prüfung: '+(err.splitlines() or ['keine Antwort'])[-1], 8000)
                return
            latest = info['latest']
            when = latest.get('date', '')[:16].replace('T', ' ')
            if info['dev']:
                if manual:
                    self.statusBar().showMessage(f"Entwicklungsordner (git) – neueste Version {latest['commit']} mit „git pull“ holen.", 8000)
            elif info['available']:
                self.update_info = latest
                self.update_button.setToolTip(f"Neu: {latest.get('subject', '')}\nStand {when} · {latest['commit']}")
                self.update_button.show()
            elif manual:
                self.statusBar().showMessage(f"LocalVoice ist aktuell ({latest['commit']}, {when}).", 8000)
        process.finished.connect(done)
        process.start(sys.executable, ['-u', str(ROOT/'src'/'updater.py'), '--check'])

    def install_update(self):
        if self.busy() or self.usb.capture.recording:
            self.statusBar().showMessage('Bitte laufende Aufnahme oder Verarbeitung zuerst beenden.', 6000)
            return
        info = getattr(self, 'update_info', {})
        answer = QMessageBox.question(self, 'Update installieren',
            f"Neue Version: {info.get('subject', '')}\n\nLocalVoice schließt sich kurz, installiert das Update und "
            'startet neu (etwa eine Minute). Aufnahmen, Sprachmodell und Einstellungen bleiben erhalten.')
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.studio.save_preferences()
        ok = QProcess.startDetached(sys.executable, ['-u', str(ROOT/'src'/'updater.py'), '--install',
            '--pid', str(os.getpid())], str(ROOT))
        ok = ok[0] if isinstance(ok, tuple) else ok
        if not ok:
            self.statusBar().showMessage('Update konnte nicht gestartet werden.', 8000)
            return
        self.close()

    def set_expert(self, on):
        if not on and self.tabs.currentIndex() != 0:
            if self.busy() or self.usb.capture.recording:
                self.mode_group.button(1).setChecked(True)
                return
            self.tabs.setCurrentIndex(0)
        self.mode_group.button(1 if on else 0).setChecked(True)
        self.studio.set_expert(on)
        self.tabs.setTabVisible(1, on)
        self.import_button.setVisible(on)

    def add_output_controls(self):
        box = self.studio.output_box
        self.output = QComboBox()
        self.output.setToolTip('Ausgang für Wiedergabe und Live-Abhören')
        default = sd.default.device[1]
        devices = [d for d in audio_devices() if d['max_output_channels']]
        # Windows lists every device once per audio API; keep the default device's API only.
        api = next((d.get('hostapi') for d in devices if d['index'] == default), None)
        devices = [d for d in devices if api is None or d.get('hostapi') == api]
        for d in devices:
            self.output.addItem(d['name'], d['index'])
        current = self.output.findData(default)
        if 'respeaker' in self.output.itemText(current).lower():
            # A new USB ReSpeaker often becomes the default output, usually without a speaker.
            current = next((i for i in range(self.output.count()) if 'respeaker' not in self.output.itemText(i).lower()), current)
        self.output.setCurrentIndex(max(0, current))
        self.output.currentIndexChanged.connect(self.change_output)
        box.addWidget(self.output)
        level = QHBoxLayout()
        level.addWidget(QLabel('Pegel'))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(60)
        self.volume.valueChanged.connect(lambda v: setattr(self.studio.player, 'volume', v/100))
        self.studio.player.volume = .6
        self.studio.monitor_volume = .6
        self.volume.valueChanged.connect(lambda v: setattr(self.studio, 'monitor_volume', v/100))
        level.addWidget(self.volume, 1)
        box.addLayout(level)
        self.studio.output_card.show()
        self.change_output()

    def change_output(self):
        self.studio.player.close()
        self.studio.player.device = self.output.currentData()
        self.studio.monitor_device = self.output.currentData()

    def busy(self):
        return bool(self.studio.recorder or self.studio.voxtral or self.studio.enhance_process or self.studio.live_stt or self.import_process)

    def restore_tab(self):
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(self.current_tab)
        self.tabs.blockSignals(False)

    def tab_changed(self, index):
        if self.busy() or self.usb.capture.recording:
            self.restore_tab()
            self.statusBar().showMessage('Bitte laufende Aufnahme oder Verarbeitung zuerst beenden.', 5000)
            return
        if self.usb.capture.stream is not None:
            self._usb_toggle()
        self.usb.stop_playback()
        self.studio.player.close()
        self.studio.listen.stop()
        self.current_tab = index

    def toggle_usb_live(self):
        if self.busy():
            self.statusBar().showMessage('Das Studio verarbeitet noch eine Aufnahme.', 5000)
            return
        self._usb_toggle()

    def refresh_studio(self):
        if not self.busy() and self.usb.capture.stream is None:
            self.studio.player.close()
            self.studio.refresh()

    def choose_usb(self):
        if self.busy() or self.usb.capture.recording:
            return
        initial = str(self.usb.last_path.parent if self.usb.last_path else ROOT.parent/'recordings')
        path, _ = QFileDialog.getOpenFileName(self, 'USB-Aufnahme ins Studio übernehmen', initial, 'WAV (*.wav)')
        if path:
            self.start_import(path)

    def import_latest(self):
        if self.usb.last_path:
            self.start_import(str(self.usb.last_path))
        else:
            self.usb.status.setText('Bitte zuerst eine USB-Aufnahme aufnehmen und speichern.')

    def start_import(self, path):
        if self.busy() or self.usb.capture.recording:
            return
        import soundfile as sf
        try:
            info = sf.info(path)
            options = [f'USB {i+1}' for i in range(info.channels)]
            if info.channels > 1:
                options.append('Alle Kanäle mischen')
            choice, ok = QInputDialog.getItem(self, 'Kanal für die Sprachverbesserung',
                'Die ursprüngliche WAV bleibt erhalten. Welchen Kanal verwenden?', options, 0, False)
            if not ok:
                return
            channel = 'mix' if choice == 'Alle Kanäle mischen' else str(options.index(choice)+1)
            if self.usb.capture.stream is not None:
                self._usb_toggle()
            self.usb.stop_playback()
            self.studio.player.close()
            self.tabs.setCurrentIndex(0)
            self.import_output = self.import_errors = ''
            self.import_process = QProcess(self)
            self.import_process.readyReadStandardOutput.connect(self.read_import)
            self.import_process.readyReadStandardError.connect(self.read_import)
            self.import_process.finished.connect(self.import_finished)
            self.import_process.errorOccurred.connect(self.import_error)
            self.studio_body.setEnabled(False)
            self.import_process.start(sys.executable, ['-u', str(ROOT/'src/import_usb.py'), path,
                '--destination', str(self.studio.folder), '--channel', channel,
                '--attenuation', str(self.studio.strength.currentData())])
            self.statusBar().showMessage('USB-WAV wird lokal umgewandelt und verbessert …')
        except Exception as exc:
            QMessageBox.warning(self, 'USB-WAV konnte nicht übernommen werden', str(exc))

    def read_import(self):
        if self.import_process:
            self.import_output += bytes(self.import_process.readAllStandardOutput()).decode('utf-8', errors='replace')
            self.import_errors += bytes(self.import_process.readAllStandardError()).decode('utf-8', errors='replace')

    def import_error(self, error):
        if self.import_process and self.import_process.state() == QProcess.NotRunning:
            self.import_finished(-1, None)

    def import_finished(self, code, status):
        if self.import_process is None:
            return
        self.read_import()
        process, self.import_process = self.import_process, None
        process.deleteLater()
        self.studio_body.setEnabled(True)
        if code:
            self.statusBar().showMessage('USB-Import: '+(self.import_errors[-350:] or 'Prozess konnte nicht starten.'))
            return
        try:
            result = json.loads(self.import_output.strip().splitlines()[-1])
            self.studio.load_pair(result['clean'], None)
            self.studio.status.setText('USB-Aufnahme übernommen · Hörspur bereit.')
            self.statusBar().showMessage('Import fertig.', 8000)
            if (ROOT/'models/qwen3-asr/.ready.json').exists():
                self.studio.transcribe()
        except Exception as exc:
            self.statusBar().showMessage('USB-Import konnte nicht geladen werden: '+str(exc))

    def update_controls(self):
        locked = self.busy() or self.usb.capture.recording
        self.import_button.setEnabled(not locked)
        self.usb_import_button.setEnabled(not locked and self.usb.last_path is not None)
        self.output.setEnabled(not locked)
        self.tabs.tabBar().setEnabled(not locked)
        self.usb.output.setEnabled(not self.import_process)

    def closeEvent(self, event):
        if self.busy() or self.usb.capture.recording:
            self.statusBar().showMessage('Bitte Aufnahme/Verarbeitung zuerst beenden; danach schließen.', 5000)
            event.ignore()
            return
        self.usb.capture.stop()
        self.usb.stop_playback()
        self.studio.player.close()
        self.studio.listen.stop()
        self.studio.save_preferences()
        event.accept()


if __name__ == '__main__':
    if sys.stdout is None or sys.stderr is None:
        # Started without a console (pythonw on Windows): keep a log for support.
        (ROOT/'Logs').mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(ROOT/'Logs'/'app.log', 'a', encoding='utf-8', buffering=1)
    application = QApplication(sys.argv)
    application.setApplicationName('LocalVoice + reSpeaker')
    application.setOrganizationName('LocalVoiceCombo')
    application.setStyle('Fusion')
    from PySide6.QtGui import QIcon
    application.setWindowIcon(QIcon(str(ROOT/'installer'/'LocalVoice.png')))
    application.setStyleSheet(STYLE)
    window = Combined()
    window.show()
    sys.exit(application.exec())
