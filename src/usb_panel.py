#!/usr/bin/env python3
"""reSpeaker Lite USB test bench for macOS. Run: .venv/bin/python app.py"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSlider, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from usb_audio import ROOT, Capture, audio_devices, output_samples, read_wav, usb_info


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        self.data = np.zeros((1, 2))
        self.setMinimumHeight(140)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#0f1620'))
        painter.drawRoundedRect(self.rect(), 10, 10)
        colors = ['#6ee7d3', '#f5c26b', '#a5b9ff', '#ef9fc0']
        if not np.any(self.data):
            painter.setPen(QColor('#6c7a92'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Pegelanzeige starten, um das Signal pro USB-Kanal zu sehen')
        n = self.data.shape[1]
        for ch in range(n):
            h = self.height() / n
            y = h * (ch + .5)
            painter.setPen(QPen(QColor('#222e40'), 1))
            painter.drawLine(0, int(y), self.width(), int(y))
            painter.setPen(QColor(colors[ch % len(colors)]))
            painter.drawText(10, int(h * ch + 18), f'USB {ch + 1}')
            path = QPainterPath()
            for i, v in enumerate(self.data[:, ch]):
                x = i * self.width() / max(1, len(self.data) - 1)
                yy = y - float(v) * h * .43
                if i == 0:
                    path.moveTo(x, yy)
                else:
                    path.lineTo(x, yy)
            painter.setPen(QPen(QColor(colors[ch % len(colors)]), 1.5))
            painter.drawPath(path)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('reSpeaker Lite · USB-Test')
        self.resize(920, 830)
        self.capture = Capture()
        self.devices = []
        self.meters = []
        self.last_path = None
        self.play_until = 0
        self.noise_until = 0
        self.noise_values = []
        self.usb = usb_info()
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 14, 18, 16)
        title = QLabel('reSpeaker Lite')
        title.setObjectName('hero')
        subtitle = QLabel('USB-Teststudio · Pegel · Aufnahme · Wiedergabe')
        subtitle.setObjectName('muted')
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.status = QLabel('Bereit. Starte die Pegelanzeige, um das Mikrofon zu testen.')
        self.status.setWordWrap(True)
        self.status.setObjectName('banner')
        layout.addWidget(self.status)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        test = QWidget()
        tab = QVBoxLayout(test)
        tab.setContentsMargins(16, 16, 16, 16)
        tabs.addTab(test, 'Mikrofon testen')
        box = QGroupBox('Audioverbindung')
        form = QFormLayout(box)
        self.input = QComboBox()
        self.output = QComboBox()
        self.rate = QComboBox()
        self.rate.addItems(['16000', '48000'])
        self.rate.setToolTip('USB-Standardfirmware: 16 kHz. 48 kHz nur bei tatsächlich passender Firmware.')
        self.channels = QComboBox()
        self.input.currentIndexChanged.connect(self.input_changed)
        form.addRow('Eingang', self.input)
        row = QHBoxLayout()
        row.addWidget(QLabel('Hz'))
        row.addWidget(self.rate)
        row.addSpacing(12)
        row.addWidget(QLabel('USB-Kanäle'))
        row.addWidget(self.channels)
        self.refresh_button = QPushButton('Geräte neu suchen')
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        form.addRow('Format', row)
        form.addRow('Wiedergabe über', self.output)
        tab.addWidget(box)
        row = QHBoxLayout()
        self.live_button = QPushButton('Pegelanzeige starten')
        self.live_button.setObjectName('primary')
        self.live_button.clicked.connect(self.toggle_live)
        self.record_button = QPushButton('● Aufnahme starten')
        self.record_button.clicked.connect(self.toggle_recording)
        self.record_button.setEnabled(False)
        row.addWidget(self.live_button)
        row.addWidget(self.record_button)
        tab.addLayout(row)
        self.meter_layout = QVBoxLayout()
        tab.addLayout(self.meter_layout)
        self.wave = Waveform()
        tab.addWidget(self.wave, 1)
        self.stats = QLabel('Noch kein Audiosignal · WAV: unverändertes USB-Signal, PCM 32 Bit')
        self.stats.setWordWrap(True)
        tab.addWidget(self.stats)
        row = QHBoxLayout()
        self.noise_button = QPushButton('Ruhepegel messen · 3 s')
        self.noise_button.setEnabled(False)
        self.noise_button.clicked.connect(self.measure_noise)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(-90, -5)
        self.threshold.setValue(-40)
        self.threshold.setSuffix(' dBFS')
        self.threshold.setToolTip('Einfacher Software-Pegelschwellwert; kein XMOS-VAD.')
        self.activity = QLabel('Signal: —')
        row.addWidget(self.noise_button)
        row.addWidget(QLabel('Schwelle'))
        row.addWidget(self.threshold)
        row.addWidget(self.activity)
        tab.addLayout(row)
        box = QGroupBox('Anhören && Ausgang testen')
        play = QVBoxLayout(box)
        row = QHBoxLayout()
        self.play_channels = QComboBox()
        self.play_channels.addItems(['Alle USB-Kanäle', 'Nur USB 1', 'Nur USB 2'])
        self.play_button = QPushButton('Letzte Aufnahme anhören')
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.play_recording)
        stop = QPushButton('Wiedergabe stoppen')
        stop.clicked.connect(self.stop_playback)
        row.addWidget(self.play_channels)
        row.addWidget(self.play_button)
        row.addWidget(stop)
        play.addLayout(row)
        row = QHBoxLayout()
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(25)
        self.volume_label = QLabel('25 %')
        self.volume.valueChanged.connect(lambda value: self.volume_label.setText(f'{value} %'))
        self.tone_button = QPushButton('Testton · 440 Hz / 1 s')
        self.tone_button.clicked.connect(self.test_tone)
        folder = QPushButton('Aufnahmen öffnen')
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / 'recordings'))))
        row.addWidget(QLabel('Abhörpegel'))
        row.addWidget(self.volume)
        row.addWidget(self.volume_label)
        row.addWidget(self.tone_button)
        row.addWidget(folder)
        play.addLayout(row)
        self.file_label = QLabel('Aufnahmen bleiben lokal im Ordner recordings.')
        self.file_label.setWordWrap(True)
        play.addWidget(self.file_label)
        tab.addWidget(box)
        note = QLabel('USB-Kanäle sind Firmware-Ausgänge, keine zugesicherten Rohmikrofone. '
                      'Für den Ausgangstest Kopfhörer oder Lautsprecher am gewählten Ausgang anschließen.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        tab.addWidget(note)
        details_tab = QWidget()
        details_layout = QVBoxLayout(details_tab)
        tabs.addTab(details_tab, 'Board-Funktionen && Diagnose')
        info = QTextEdit()
        info.setReadOnly(True)
        info.setHtml('''<h2>Was du per USB nutzen kannst</h2>
<p>Aufnehmen und wiedergeben, USB-Kanäle vergleichen, Pegel und Übersteuerung prüfen.
Das Board liefert bereits das von seiner Firmware verarbeitete Signal.</p>
<h3>Verarbeitung auf dem XMOS XU316</h3>
<p>Seeed nennt Interferenzunterdrückung, Echounterdrückung (AEC), Rauschunterdrückung,
Voice-to-Noise Ratio (VNR) und automatische Verstärkung (AGC). Welche Verarbeitung genau
aktiv ist, hängt von der Firmware ab. Diese App kann deren Zustand über USB nicht auslesen.</p>
<p>Für einen praktischen Echo-Test: „ReSpeaker Lite“ als Wiedergabeausgang wählen,
einen Lautsprecher am Board anschließen und während einer Aufnahme den Testton starten.
Damit erhält das Board das USB-Wiedergabesignal als mögliche Echo-Referenz.
Das ist ein Hörtest, keine quantitative AEC-Messung.</p>
<h3>Grenzen dieser USB-Verbindung</h3>
<p>Die offizielle Lite-Dokumentation beschreibt Registerzugriff über I²C mit I2S-Firmware.
Für die USB-Firmware ist keine entsprechende DSP- oder RGB-LED-Steuerung dokumentiert.
Diese App bietet deshalb keine erfundenen AGC-/AEC-/LED-Schalter.</p>
<p>Die WS2812-LED benötigt eine geeignete Hardware-Ansteuerung, z. B. durch einen XIAO.
Die Mute-Taste am Board kannst du direkt drücken und den Effekt am Pegel testen.
Die USR-Taste wird hier nicht als USB-Tasteneingabe ausgewertet.</p>
<h3>Für einen schnellen Vergleich</h3>
<p>3 Sekunden Ruhe messen, 10 Sekunden Sprache aufnehmen, Mute-Taste testen,
danach USB 1 und USB 2 einzeln anhören. Wiederhole die Aufnahme in 1–3 Metern Abstand.</p>
<p>Alles bleibt lokal. Es werden keine Audiodaten übertragen und keine Firmware geändert.</p>
<p><a href="https://github.com/respeaker/ReSpeaker_Lite/">Seeed / reSpeaker Lite: offizielle Firmware und Schnittstellen</a><br>
<a href="https://wiki.seeedstudio.com/reSpeaker_usb_v3/">Seeed: USB-Aufnahme und Hardware</a></p>''')
        details_layout.addWidget(info)
        self.diagnostic = QTextEdit()
        self.diagnostic.setReadOnly(True)
        self.diagnostic.setMaximumHeight(160)
        details_layout.addWidget(self.diagnostic)
        export = QPushButton('Diagnose lokal speichern')
        export.clicked.connect(self.export_diagnostic)
        details_layout.addWidget(export)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(60)
        self.refresh()

    def report_error(self, exc):
        self.status.setText(f'Audiofehler: {exc}')
        QMessageBox.warning(self, 'Audioverbindung prüfen', str(exc) + '\n\n'
            'Bei fehlendem Eingang: USB-Datenkabel und XMOS-USB-Anschluss prüfen. '
            'Unter Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon den gestarteten '
            'Python-/Terminal-Prozess erlauben. Danach die App neu starten.')

    def refresh(self):
        try:
            self.devices = audio_devices()
            previous_in, previous_out = self.input.currentText(), self.output.currentText()
            self.input.blockSignals(True)
            self.input.clear()
            self.output.clear()
            default_out = sd.default.device[1]
            for d in self.devices:
                label = f"{d['name']}  ·  {d['index']}"
                if d['max_input_channels']:
                    self.input.addItem(label, d['index'])
                if d['max_output_channels']:
                    self.output.addItem(label, d['index'])
            idx = next((i for i in range(self.input.count()) if 'respeaker' in self.input.itemText(i).lower()), -1)
            if previous_in:
                idx = self.input.findText(previous_in)
            self.input.setCurrentIndex(max(0, idx))
            out_idx = self.output.findText(previous_out) if previous_out else self.output.findData(default_out)
            self.output.setCurrentIndex(max(0, out_idx))
            self.input.blockSignals(False)
            self.input_changed()
            self.diagnostic.setPlainText(json.dumps({'usb': self.usb, 'audio': self.devices}, indent=2, ensure_ascii=False))
            if not self.input.count():
                self.status.setText('Kein Audioeingang sichtbar. USB und Mikrofonberechtigung prüfen, dann App neu starten.')
            self.live_button.setEnabled(self.input.count() > 0)
        except Exception as exc:
            self.report_error(exc)

    def input_changed(self):
        self.channels.clear()
        if self.input.currentData() is None:
            return
        d = sd.query_devices(self.input.currentData())
        from coreaudio_rates import hardware_rates
        hardware = hardware_rates().get(d['name'], {})
        ranges = hardware.get('ranges', [])
        rates = sorted({int(d['default_samplerate'])} | {int(low) for low, high in ranges if low == high})
        self.rate.clear()
        self.rate.addItems([str(rate) for rate in rates])
        self.rate.setCurrentText(str(int(d['default_samplerate'])))
        maximum = int(d['max_input_channels'])
        for n in range(1, min(maximum, 8) + 1):
            self.channels.addItem(str(n), n)
        self.channels.setCurrentIndex(min(maximum, 2) - 1)

    def set_running(self, running):
        for w in [self.input, self.channels, self.rate, self.refresh_button]:
            w.setEnabled(not running)
        self.record_button.setEnabled(running)
        self.noise_button.setEnabled(running)
        self.live_button.setText('Pegelanzeige stoppen' if running else 'Pegelanzeige starten')

    def build_meters(self, n):
        while self.meter_layout.count():
            item = self.meter_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.meters = []
        for ch in range(n):
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            label = QLabel(f'USB {ch+1}')
            bar = QProgressBar()
            bar.setRange(0, 960)
            bar.setTextVisible(False)
            text = QLabel('−96 dBFS')
            text.setMinimumWidth(180)
            row.addWidget(label)
            row.addWidget(bar, 1)
            row.addWidget(text)
            self.meter_layout.addWidget(w)
            self.meters.append((bar, text))

    def toggle_live(self):
        try:
            if self.capture.stream is not None:
                path = self.capture.stop()
                if path:
                    self.saved(path)
                self.noise_until = 0
                self.set_running(False)
                self.record_button.setText('● Aufnahme starten')
                self.status.setText('Mikrofon gestoppt. Aufnahme gespeichert.' if path else 'Mikrofon gestoppt.')
            else:
                self.stop_playback()
                self.capture.start(self.input.currentData(), self.channels.currentData(), int(self.rate.currentText()))
                self.build_meters(self.capture.channels)
                self.set_running(True)
                self.status.setText('Mikrofon aktiv. Sprich oder teste die Mute-Taste am Board.')
        except Exception as exc:
            self.set_running(False)
            self.report_error(exc)

    def toggle_recording(self):
        try:
            if self.capture.recording:
                self.saved(self.capture.end_recording())
                self.record_button.setText('● Aufnahme starten')
                self.status.setText('Aufnahme gespeichert. Pegelanzeige läuft weiter.')
            else:
                self.stop_playback()
                path = self.capture.begin_recording()
                self.play_button.setEnabled(False)
                self.file_label.setText(path.name)
                self.record_button.setText('■ Aufnahme speichern')
                self.status.setText('Aufnahme läuft · alle ausgewählten USB-Kanäle werden lokal gespeichert.')
        except Exception as exc:
            self.report_error(exc)

    def saved(self, path):
        if path:
            self.last_path = path
            self.file_label.setText(f'Gespeichert: {path.name}')
            self.play_button.setEnabled(True)

    def measure_noise(self):
        self.noise_values = []
        self.noise_until = time.monotonic() + 3
        self.noise_button.setEnabled(False)
        self.status.setText('Ruhepegel: Bitte 3 Sekunden still sein. Danach wird die Signalschwelle angepasst.')

    def tick(self):
        if self.capture.stream is not None and not self.capture.active:
            try:
                self.saved(self.capture.stop())
            except Exception as exc:
                self.report_error(exc)
            self.set_running(False)
            self.record_button.setText('● Aufnahme starten')
            self.status.setText('Audiostream wurde unterbrochen. USB prüfen, danach Pegelanzeige neu starten.')
        if self.capture.active:
            s = self.capture.snapshot()
            for ch, (bar, label) in enumerate(self.meters):
                db, peak = float(s['rms'][ch]), float(s['peak'][ch])
                bar.setValue(int((db + 96) * 10))
                label.setText(f'{db:5.1f} dBFS · Peak {20*np.log10(max(peak, 1e-12)):.1f}')
            self.wave.data = s['waveform']
            self.wave.update()
            high = float(np.max(s['rms']))
            self.activity.setText('Signal über Schwelle' if high > self.threshold.value() else 'Unter Schwelle')
            clipping = ' · Übersteuerung erkannt' if np.max(s['peak_hold']) >= .999 else ''
            self.stats.setText(f"{self.capture.rate:,} Hz · {self.capture.channels} Kanal/Kanäle · "
                f"Aufnahme {s['record_frames']/self.capture.rate:.1f} s · Verlorene Frames: {s['dropped']}{clipping}")
            if s['failure']:
                self.capture.stop()
                self.set_running(False)
                self.record_button.setText('● Aufnahme starten')
                self.status.setText(f"Aufnahmefehler auf dem Datenträger: {s['failure']}. Teilaufnahme im recordings-Ordner prüfen.")
            elif s['dropped'] or s['warnings']:
                self.status.setText('Audiolücken/Streamwarnung: ' + '; '.join(s['warnings']) + f" · {s['dropped']} Frames verloren")
            if self.noise_until:
                self.noise_values.append(high)
                if time.monotonic() >= self.noise_until:
                    floor = float(np.median(self.noise_values))
                    self.noise_until = 0
                    self.noise_button.setEnabled(True)
                    self.threshold.setValue(min(-5, max(-90, floor + 10)))
                    self.status.setText(f'Ruhepegel {floor:.1f} dBFS · Signalschwelle +10 dB. Das ist eine Pegelhilfe, keine Spracherkennung.')
        if self.play_until and time.monotonic() >= self.play_until:
            self.play_until = 0
            self.play_button.setText('Letzte Aufnahme anhören')

    def gain(self):
        return self.volume.value() / 100

    def play_recording(self):
        try:
            if self.capture.recording or self.last_path is None:
                return
            self.stop_playback()
            data, rate = read_wav(self.last_path)
            if not len(data):
                raise ValueError('Die Aufnahme ist leer. Bitte mindestens eine Sekunde aufnehmen.')
            selection = self.play_channels.currentIndex()
            if selection:
                if selection > data.shape[1]:
                    raise ValueError('Dieser USB-Kanal ist in der Aufnahme nicht enthalten.')
                data = data[:, selection-1:selection]
            seconds = output_samples(data, rate, self.output.currentData(), self.gain())
            self.play_until = time.monotonic() + seconds
            self.play_button.setText('Wiedergabe läuft …')
        except Exception as exc:
            self.report_error(exc)

    def stop_playback(self):
        sd.stop()
        self.play_until = 0
        self.play_button.setText('Letzte Aufnahme anhören')

    def test_tone(self):
        try:
            self.stop_playback()
            rate = 16000
            t = np.arange(rate) / rate
            envelope = np.minimum(1, np.minimum(t / .03, (1-t) / .03))
            # Peak -26 dBFS at default volume. Deliberately quiet test signal.
            tone = .2 * np.sin(2 * np.pi * 440 * t) * envelope
            output_samples(tone, rate, self.output.currentData(), self.gain())
        except Exception as exc:
            self.report_error(exc)

    def export_diagnostic(self):
        try:
            path = ROOT / 'diagnose.json'
            path.write_text(self.diagnostic.toPlainText(), encoding='utf-8')
            self.status.setText(f'Diagnose lokal gespeichert: {path.name}')
        except Exception as exc:
            self.report_error(exc)

    def closeEvent(self, event):
        try:
            self.capture.stop()
            sd.stop()
        except Exception as exc:
            QMessageBox.warning(self, 'Speichern prüfen', str(exc))
        event.accept()


STYLE = '''
QWidget { background: #14212d; color: #dce6ef; font-size: 13px; }
QGroupBox { border: 1px solid #354452; border-radius: 8px; margin-top: 12px; padding: 12px 10px 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QPushButton { background: #273b4d; border: 1px solid #455c70; border-radius: 6px; padding: 9px 12px; }
QPushButton:hover { background: #354f64; }
QPushButton:disabled { color: #71818e; border-color: #31404d; background: #1c2c38; }
QPushButton#primary { background: #217c70; border-color: #42b19e; color: white; font-weight: 600; }
QComboBox, QDoubleSpinBox { background: #1d3040; border: 1px solid #40586a; padding: 6px; border-radius: 5px; }
QComboBox QAbstractItemView { background: #23394a; selection-background-color: #217c70; }
QProgressBar { border: 0; background: #0e1821; height: 12px; border-radius: 4px; }
QProgressBar::chunk { background: #54cdb9; border-radius: 4px; }
QTabWidget::pane { border: 1px solid #354452; border-radius: 6px; }
QTabBar::tab { padding: 10px 16px; background: #1c2d3c; }
QTabBar::tab:selected { background: #294657; color: #8ce9d7; }
QTextEdit { border: 0; padding: 8px; }
QSlider::groove:horizontal { height: 5px; background: #405566; }
QSlider::handle:horizontal { background: #7ddac8; width: 14px; margin: -5px 0; border-radius: 7px; }
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnose', action='store_true', help='List devices without opening a microphone')
    args = parser.parse_args()
    if args.diagnose:
        print(json.dumps({'usb': usb_info(), 'audio': audio_devices()}, indent=2, ensure_ascii=False))
        return
    app = QApplication(sys.argv[:1])
    app.setApplicationName('reSpeaker Lite Test')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
