"""Compact native recording studio. Audio business logic lives in app_core."""
import json
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QProcess, QSize, QUrl, QEvent
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
    QVBoxLayout, QLabel, QPushButton, QCheckBox, QComboBox, QProgressBar,
    QDoubleSpinBox, QSpinBox, QFrame, QTextEdit, QSlider, QScrollArea,
    QSizePolicy, QGridLayout, QLineEdit, QListWidget, QListWidgetItem, QStackedWidget,
    QAbstractSpinBox, QMenu, QFileDialog, QButtonGroup)

import lecture
import library
from app_core import Window, ROOT
from listen import ListenView
from paths import FILE_BROWSER, HOST, asr_ready, default_recordings
from engine import input_devices
from playback import ComparisonPlayer

# Loopback/meeting drivers: listed, but tucked away unless already in use.
VIRTUAL = ('teams', 'zoom', 'blackhole', 'loopback', 'soundflower', 'aggregate', 'virtual', 'background music', 'webex')


ACCENT = '#6ee7d3'
AMBER = '#f5c26b'
RED = '#ff6370'


def label(text, name=None):
    w = QLabel(text)
    if name: w.setObjectName(name)
    return w


def card(name='card'):
    w = QFrame()
    w.setObjectName(name)
    return w


def section(title, spacing=10):
    """Sidebar card with a small caps title; returns (card, inner layout)."""
    box = card()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(14,12,14,14)
    layout.setSpacing(spacing)
    layout.addWidget(label(title,'sectionCaps'))
    return box, layout


def repolish(widget, name, value):
    if widget.property(name) != value:
        widget.setProperty(name,value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def envelope(signal, bins=650):
    if not len(signal): return np.zeros(bins)
    edges = np.linspace(0, len(signal), bins+1).astype(int)
    return np.array([float(np.max(np.abs(signal[a:b]))) if b>a else 0 for a,b in zip(edges[:-1],edges[1:])])


class Waveform(QWidget):
    seekRequested = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clean = np.zeros(180)
        self.raw = np.zeros(180)
        self.position = 0.
        self.duration = 0.
        self.original = False
        self.live = False
        self.setAccessibleName('Wellenform: verbessert und Original. Zum Springen klicken.')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#0f1620'))
        p.drawRoundedRect(QRectF(self.rect()),10,10)
        width, height = self.width(), self.height()
        left, right = 103, width-18
        lane_h = (height-25)/2
        empty = self.duration <= 0 and not self.live and not np.any(self.clean) and not np.any(self.raw)
        for row, (data, title, color) in enumerate([
                (self.clean, getattr(self,'processed_title','VERBESSERT'), ACCENT), (self.raw, 'ORIGINAL', '#93a3bf')]):
            mid = 14+lane_h*(row+.5)
            # The lane you currently hear is drawn at full strength.
            audible = (row == 1) == self.original or self.live
            p.setPen(QColor(color if audible and not empty else '#5d6b82'))
            p.setFont(QFont('.AppleSystemUIFont',9,QFont.Weight.DemiBold))
            p.drawText(QRectF(14,mid-8,90,20),Qt.AlignmentFlag.AlignLeft,title)
            p.setPen(QPen(QColor('#222e40'),1))
            p.drawLine(left,int(mid),right,int(mid))
            if empty: continue
            points = max(1,min(len(data),int((right-left)/3)))
            indices = np.linspace(0,len(data)-1,points).astype(int)
            pen = QColor(color)
            if not audible: pen.setAlphaF(.35)
            p.setPen(QPen(pen,2.1,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            for i,idx in enumerate(indices):
                x = left+i*(right-left)/max(points-1,1)
                amplitude = max(1,min(lane_h*.42,float(data[idx])*lane_h*2.8))
                p.drawLine(int(x),int(mid-amplitude),int(x),int(mid+amplitude))
        if empty:
            p.setPen(QColor('#6c7a92'))
            p.setFont(QFont('.AppleSystemUIFont',11))
            p.drawText(QRectF(left,0,right-left,height),Qt.AlignmentFlag.AlignCenter,
                'Noch keine Aufnahme · ⌘R startet eine neue')
        if self.duration > 0 and not self.live:
            x = left+(right-left)*min(1,self.position/self.duration)
            color = QColor(AMBER if self.original else '#e8efff')
            p.setPen(QPen(color,1.5))
            p.drawLine(int(x),8,int(x),height-8)
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(x-3.5,4,7,7))

    def sizeHint(self):
        return QSize(600,150)

    def minimumSizeHint(self):
        return QSize(240,130)

    def mousePressEvent(self,event):
        if self.duration and not self.live:
            fraction = max(0,min(1,(event.position().x()-103)/max(1,self.width()-121)))
            self.seekRequested.emit(fraction*self.duration)


class Studio(Window):
    def __init__(self):
        QMainWindow.__init__(self)
        self.setWindowTitle('LocalVoice Studio')
        self.resize(1180,820)
        self.setMinimumSize(960,640)
        self.recorder = None
        self.player = ComparisonPlayer()
        self.last = None
        self.voxtral = None
        self.enhance_process = None
        self.rows = []
        self.wet = 1.0
        self.expert = False
        self.player_loaded = None
        self.auto_boxes = {}
        self.download_process = None
        self.folder = default_recordings()
        self.config_path = ROOT/'preferences.json'
        try:
            self.preferences = json.loads(self.config_path.read_text(encoding='utf-8'))
            self.folder = Path(self.preferences.get('folder',str(self.folder)))
        except (OSError,ValueError): self.preferences = {}

        body = QWidget()
        self.setCentralWidget(body)
        shell = QVBoxLayout(body)
        shell.setContentsMargins(18,14,18,16)
        shell.setSpacing(14)
        self.header_widget = QWidget()
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0,0,0,0)
        header.addWidget(label('◉','mark'))
        header.addWidget(label('LocalVoice','brand'))
        header.addWidget(label('STUDIO','eyebrow'))
        header.addStretch()
        header.addWidget(label(f'●  Lokal auf {HOST}','local'))
        shell.addWidget(self.header_widget)
        workspace = QHBoxLayout()
        workspace.setSpacing(16)
        shell.addLayout(workspace,1)

        # Sidebar: everything that is set up before a recording.
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0,0,8,0)
        side.setSpacing(12)
        inputs, il = section('EINGÄNGE',8)
        sourcehead = il.itemAt(0).widget()
        il.removeWidget(sourcehead)
        head = QHBoxLayout()
        head.addWidget(sourcehead)
        head.addStretch()
        self.refresh_button = QPushButton('Aktualisieren')
        self.refresh_button.setObjectName('link')
        self.refresh_button.setToolTip('Mikrofone neu suchen')
        self.refresh_button.clicked.connect(self.refresh)
        head.addWidget(self.refresh_button)
        il.addLayout(head)
        self.table = QWidget()
        self.table.setObjectName('clear')
        self.sources_layout = QVBoxLayout(self.table)
        self.sources_layout.setContentsMargins(0,0,0,0)
        self.sources_layout.setSpacing(6)
        il.addWidget(self.table)
        self.mode = QComboBox()
        self.mode.addItem('Bestes Sprachsignal wählen','auto')
        self.mode.addItem('Mikrofone mischen','mix')
        self.mode.setToolTip('Ein Mikrofon reicht. Bei mehreren wählt die Automatik das beste Sprachsignal.')
        il.addWidget(self.mode)
        side.addWidget(inputs)

        processing, self.processing_box = section('VERARBEITUNG')
        self.auto_transcribe = QCheckBox('Nach Stopp transkribieren')
        self.processing_box.addWidget(self.auto_transcribe)
        self.text_source = QComboBox()
        self.text_source.addItem('Text aus Board-Signal (empfohlen)','board')
        self.text_source.addItem('Text aus verbesserter Spur','clean')
        self.text_source.setToolTip('Die Spracherkennung trifft auf dem unbearbeiteten Signal meist besser als auf der gefilterten Hörspur.')
        self.processing_box.addWidget(self.text_source)
        self.context_edit = QLineEdit()
        self.context_edit.setPlaceholderText('Thema / Fachbegriffe (optional)')
        self.context_edit.setToolTip('Hilft der Erkennung bei Namen und Fachwörtern, z. B. „Thermodynamik, Entropie, Carnot“.')
        self.processing_box.addWidget(self.context_edit)
        side.addWidget(processing)

        sound, sl = section('SPRACHVERBESSERUNG')
        self.strength = QComboBox()
        for name,value in [('Natürlich · sanft',18),('Ausgewogen',30),('Mehr Ruhe · stärker',45)]:
            self.strength.addItem(name,value)
        self.strength.setToolTip('Studio-Modell mit mehr Kontext. „Natürlich“ erhält mehr vom Klang deiner Stimme.')
        sl.addWidget(self.strength)
        self.enhance_button = QPushButton('Neu verbessern')
        self.enhance_button.setToolTip('Die gespeicherte Originalspur mit der gewählten Stufe erneut verbessern. Erzeugt eine neue Version.')
        self.enhance_button.setEnabled(False)
        self.enhance_button.clicked.connect(self.reprocess)
        sl.addWidget(self.enhance_button)
        side.addWidget(sound)

        self.output_card, self.output_box = section('ABHÖREN')
        self.output_card.hide()
        side.addWidget(self.output_card)

        storage, fl = section('SPEICHERORT',6)
        self.folder_label = label(self.folder.name,'muted')
        self.folder_label.setToolTip(str(self.folder))
        self.folder_label.setWordWrap(True)
        fl.addWidget(self.folder_label)
        folderrow = QHBoxLayout()
        self.folder_button = QPushButton('Ändern …')
        self.folder_button.clicked.connect(self.choose_folder)
        folderrow.addWidget(self.folder_button)
        finder = QPushButton(f'Im {FILE_BROWSER} ↗')
        finder.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        folderrow.addWidget(finder)
        fl.addLayout(folderrow)
        side.addWidget(storage)
        side.addStretch()
        sidescroll = QScrollArea()
        sidescroll.setWidgetResizable(True)
        sidescroll.setWidget(sidebar)
        sidescroll.setFrameShape(QFrame.Shape.NoFrame)
        sidescroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        column = QWidget()
        column.setFixedWidth(292)
        cl = QVBoxLayout(column)
        cl.setContentsMargins(0,0,0,0)
        cl.setSpacing(10)
        device = card('deviceCard')
        dl = QVBoxLayout(device)
        dl.setContentsMargins(14,10,14,10)
        dl.setSpacing(2)
        self.device_label = label('Mikrofon wird gesucht …','deviceTitle')
        self.device_note = label('','muted')
        self.device_note.setWordWrap(True)
        dl.addWidget(self.device_label)
        dl.addWidget(self.device_note)
        cl.addWidget(device)
        switch = QFrame()
        self.side_switch = switch
        switch.setObjectName('segment')
        sw = QHBoxLayout(switch)
        sw.setContentsMargins(3,3,3,3)
        sw.setSpacing(2)
        self.side_pages = QStackedWidget()
        self.side_group = QButtonGroup(self)
        for index, text in enumerate(('Einrichtung', 'Aufnahmen')):
            button = QPushButton(text)
            button.setObjectName('segmentButton')
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.side_group.addButton(button, index)
            sw.addWidget(button)
        self.side_group.idClicked.connect(self.show_side_page)
        cl.addWidget(switch)
        cl.addWidget(self.side_pages,1)
        self.side_pages.addWidget(sidescroll)
        self.side_pages.addWidget(self.build_library())
        workspace.addWidget(column)

        # Main column: record, listen, compare, read.
        center_panel = QWidget()
        main = QVBoxLayout(center_panel)
        main.setContentsMargins(0,0,0,0)
        main.setSpacing(10)
        views = QFrame()
        views.setObjectName('segment')
        vl = QHBoxLayout(views)
        vl.setContentsMargins(3,3,3,3)
        vl.setSpacing(2)
        self.view_group = QButtonGroup(self)
        for index, text in enumerate(('●  Aufnehmen', '▶  Nachhören')):
            button = QPushButton(text)
            button.setObjectName('segmentButton')
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setMinimumWidth(150)
            self.view_group.addButton(button, index)
            vl.addWidget(button)
        self.view_group.idClicked.connect(self.show_view)
        viewrow = QHBoxLayout()
        viewrow.addWidget(views)
        viewrow.addStretch()
        main.addLayout(viewrow)
        self.views = QStackedWidget()
        main.addWidget(self.views,1)
        record_page = QWidget()
        center = QVBoxLayout(record_page)
        center.setContentsMargins(0,0,0,0)
        center.setSpacing(14)
        self.views.addWidget(record_page)
        self.listen = ListenView()
        self.views.addWidget(self.listen)
        workspace.addWidget(center_panel,1)
        recording = card('heroCard')
        rl = QVBoxLayout(recording)
        rl.setContentsMargins(22,18,22,16)
        rl.setSpacing(10)
        controls = QHBoxLayout()
        controls.setSpacing(16)
        self.record_button = QPushButton('●  Aufnahme starten')
        self.record_button.setObjectName('record')
        self.record_button.setToolTip('Aufnahme starten/stoppen · ⌘R')
        self.record_button.clicked.connect(self.record_toggle)
        controls.addWidget(self.record_button)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QHBoxLayout()
        title.addWidget(label('Deine Stimme. Klarer.','hero'))
        self.state_badge = label('BEREIT','stateBadge')
        title.addWidget(self.state_badge)
        title.addStretch()
        text.addLayout(title)
        self.status = label('Bereit für deine nächste Aufnahme.','muted')
        self.status.setWordWrap(True)
        text.addWidget(self.status)
        self.level_hint = label('','levelHint')
        text.addWidget(self.level_hint)
        controls.addLayout(text,1)
        self.listen_button = QPushButton('▶  Nachhören')
        self.listen_button.setObjectName('primary')
        self.listen_button.clicked.connect(lambda:self.show_view(1))
        self.listen_button.hide()
        controls.addWidget(self.listen_button)
        self.timer_label = label('00:00.0','clock')
        controls.addWidget(self.timer_label)
        rl.addLayout(controls)
        self.output_meter = QProgressBar()
        self.output_meter.setObjectName('thin')
        self.output_meter.setRange(0,100)
        self.output_meter.setTextVisible(False)
        rl.addWidget(self.output_meter)
        self.stream_panel = QWidget()
        self.stream_panel.setObjectName('clear')
        self.stream_row = QHBoxLayout(self.stream_panel)
        self.stream_row.setContentsMargins(0,0,0,0)
        rl.addWidget(self.stream_panel)
        self.waveform = Waveform()
        self.waveform.seekRequested.connect(self.player.seek)
        rl.addWidget(self.waveform,1)
        self.compare_panel = QWidget()
        self.compare_panel.setObjectName('clear')
        compare = QVBoxLayout(self.compare_panel)
        compare.setContentsMargins(0,0,0,0)
        compare.setSpacing(8)
        playrow = QHBoxLayout()
        playrow.setSpacing(8)
        self.play_button = QPushButton('▶  Abspielen')
        self.play_button.setObjectName('play')
        self.play_button.setToolTip('Abspielen/Pause · Leertaste')
        self.play_button.clicked.connect(self.play_toggle)
        self.play_button.setEnabled(False)
        playrow.addWidget(self.play_button)
        self.hold_button = QPushButton('Original halten  O')
        self.hold_button.setObjectName('hold')
        self.hold_button.setToolTip('Gedrückt halten (oder Taste O): Original. Loslassen: verbessert. Gleiche Abspielposition.')
        self.hold_button.setAutoRepeat(False)
        self.hold_button.setEnabled(False)
        self.hold_button.pressed.connect(lambda:self.set_original(True))
        self.hold_button.released.connect(lambda:self.set_original(False))
        playrow.addWidget(self.hold_button)
        self.source_label = label('Verbessert','source')
        playrow.addWidget(self.source_label)
        playrow.addStretch()
        self.file_label = label('Aufnehmen, stoppen, direkt vergleichen.','muted')
        playrow.addWidget(self.file_label)
        self.play_time = label('0:00 / 0:00','time')
        playrow.addWidget(self.play_time)
        compare.addLayout(playrow)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0,0)
        self.seek.sliderMoved.connect(lambda v:self.player.seek(v/1000))
        compare.addWidget(self.seek)
        self.file_actions = QHBoxLayout()
        self.open_pair = QPushButton('Aufnahme öffnen …')
        self.open_pair.setObjectName('link')
        self.open_pair.clicked.connect(self.open_recording)
        self.file_actions.addWidget(self.open_pair)
        self.file_actions.addStretch()
        compare.addLayout(self.file_actions)
        rl.addWidget(self.compare_panel)
        center.addWidget(recording,3)

        transcriptcard = card()
        tl = QVBoxLayout(transcriptcard)
        tl.setContentsMargins(20,14,20,14)
        tl.setSpacing(8)
        th = QHBoxLayout()
        th.addWidget(label('Transkript','section'))
        th.addWidget(label('QWEN3-ASR','eyebrow'))
        th.addStretch()
        self.language = QComboBox()
        for name,code in [('Deutsch','de'),('English','en'),('Français','fr'),('Español','es')]: self.language.addItem(name,code)
        th.addWidget(self.language)
        self.transcribe_button = QPushButton('Transkribieren')
        self.transcribe_button.clicked.connect(self.transcribe)
        self.transcribe_button.setEnabled(False)
        th.addWidget(self.transcribe_button)
        self.cancel_transcribe = QPushButton('Abbrechen')
        self.cancel_transcribe.clicked.connect(self.cancel_stt)
        self.cancel_transcribe.setEnabled(False)
        self.cancel_transcribe.hide()
        th.addWidget(self.cancel_transcribe)
        self.download_button = QPushButton('Sprachmodell laden')
        self.download_button.setObjectName('primary')
        self.download_button.setToolTip('Einmalig: Qwen3-ASR herunterladen (etwa 2–4 GB). Danach arbeitet alles offline.')
        self.download_button.clicked.connect(self.download_model)
        self.download_button.setVisible(not asr_ready())
        th.addWidget(self.download_button)
        tl.addLayout(th)
        self.stt_status = label('Kleines offenes Modell · vollständig lokale Verarbeitung','muted')
        self.stt_status.setWordWrap(True)
        tl.addWidget(self.stt_status)
        self.transcript = QTextEdit()
        self.transcript.setPlaceholderText('Aus deiner Aufnahme wird hier Text. Das Transkript wird zusätzlich im Aufnahmeordner gespeichert.')
        self.transcript.setMinimumHeight(90)
        self.transcript.textChanged.connect(self.update_word_count)
        tl.addWidget(self.transcript,1)
        copyrow = QHBoxLayout()
        self.word_count = label('','muted')
        copyrow.addWidget(self.word_count)
        copyrow.addStretch()
        self.export_button = QPushButton('Exportieren ▾')
        self.export_button.setToolTip('Markdown, SRT oder WebVTT. Untertitel nutzen Abschnittszeiten, keine Wortzeitstempel.')
        menu = QMenu(self.export_button)
        for text, kind in (('Markdown (.md)', 'md'), ('Untertitel (.srt)', 'srt'), ('WebVTT (.vtt)', 'vtt')):
            menu.addAction(text, lambda kind=kind:self.export_transcript(kind))
        self.export_button.setMenu(menu)
        copyrow.addWidget(self.export_button)
        self.copy_button = QPushButton('Text kopieren')
        self.copy_button.clicked.connect(self.copy_transcript)
        copyrow.addWidget(self.copy_button)
        tl.addLayout(copyrow)
        center.addWidget(transcriptcard,2)

        # Shortcuts live on the body so they keep working when another window embeds it.
        self.body = body
        for keys, action in [('Ctrl+R', self.shortcut_record), ('Space', self.shortcut_play),
                             ('Left', lambda:self.listen_only(lambda:self.listen.skip(-15))),
                             ('Right', lambda:self.listen_only(lambda:self.listen.skip(30))),
                             ('M', lambda:self.listen_only(self.listen.add_bookmark, expert=True))]:
            shortcut = QShortcut(QKeySequence(keys), body)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(action)

        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(80)
        QApplication.instance().installEventFilter(self)
        try:
            entries = library.scan(self.folder)
            if entries: self.load_pair(entries[0]['clean'],entries[0]['original'])
        except Exception: pass
        self.set_expert(bool(self.preferences.get('expert', False)))

    def build_library(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(8)
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText('Suchen in Text und Datum …')
        self.library_search.setClearButtonEnabled(True)
        self.library_search.textChanged.connect(self.filter_library)
        layout.addWidget(self.library_search)
        self.library_list = QListWidget()
        self.library_list.setObjectName('library')
        self.library_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.library_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.library_list.itemClicked.connect(self.open_library_item)
        layout.addWidget(self.library_list,1)
        self.library_count = label('','muted')
        layout.addWidget(self.library_count)
        return page

    def show_side_page(self, index):
        self.side_pages.setCurrentIndex(index)
        if index == 1: self.refresh_library()

    def refresh_library(self):
        current = self.last['clean'] if self.last else None
        self.library_list.clear()
        try: entries = library.scan(self.folder)
        except OSError: entries = []
        for entry in entries:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            when = entry['created'].strftime('%d.%m.%Y · %H:%M')
            item.setData(Qt.ItemDataRole.UserRole+1, (when+' '+entry['variant']+' '+entry['text']).casefold())
            widget = QWidget()
            widget.setObjectName('libraryRow')
            box = QVBoxLayout(widget)
            box.setContentsMargins(10,8,10,8)
            box.setSpacing(3)
            top = QHBoxLayout()
            top.addWidget(label(when,'libraryTitle'))
            top.addStretch()
            seconds = int(entry['duration'])
            top.addWidget(label(f'{seconds//60}:{seconds%60:02}','time'))
            box.addLayout(top)
            preview = entry['text'].replace('\n',' ')
            text = label((preview[:72].rstrip()+' …' if len(preview) > 72 else preview) or 'Noch kein Transkript','libraryText' if preview else 'muted')
            text.setWordWrap(True)
            box.addWidget(text)
            if entry['variant']:
                box.addWidget(label(entry['variant'],'libraryTag'))
            width = self.library_list.viewport().width() or 280
            item.setSizeHint(QSize(width, box.totalHeightForWidth(width-24)+8))
            self.library_list.addItem(item)
            self.library_list.setItemWidget(item,widget)
            if entry['clean'] == current: item.setSelected(True)
        self.filter_library()

    def filter_library(self):
        needle = self.library_search.text().casefold().strip()
        shown = 0
        for i in range(self.library_list.count()):
            item = self.library_list.item(i)
            hidden = bool(needle) and needle not in item.data(Qt.ItemDataRole.UserRole+1)
            item.setHidden(hidden)
            shown += not hidden
        total = self.library_list.count()
        self.library_count.setText(f'{shown} von {total} Aufnahmen' if needle else f'{total} Aufnahmen · {self.folder.name}')

    def busy(self):
        return bool(self.recorder or self.enhance_process or self.voxtral or getattr(self,'live_stt',None))

    def open_library_item(self, item):
        if self.busy():
            self.status.setText('Bitte laufende Aufnahme oder Verarbeitung zuerst beenden.')
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        self.player.close()
        self.transcript.clear()
        self.load_pair(entry['clean'],entry['original'])
        self.status.setText('Aufnahme vom '+entry['created'].strftime('%d.%m.%Y, %H:%M')+' geladen.')

    def export_transcript(self, kind):
        text = self.transcript.toPlainText().strip()
        if not text or not self.last:
            self.stt_status.setText('Kein Transkript zum Exportieren.')
            return
        clean = Path(self.last['clean'])
        stored = clean.with_suffix('.txt')
        edited = not stored.exists() or stored.read_text(encoding='utf-8').strip() != text
        items = library.cues(clean, text, self.waveform.duration)
        if kind == 'md':
            content = library.to_markdown(clean, text, [] if edited else items, self.waveform.duration)
        else:
            content = (library.to_srt if kind == 'srt' else library.to_vtt)(items)
        default = str(clean.with_suffix('.'+kind)).replace('_clean.'+kind,'.'+kind)
        path,_ = QFileDialog.getSaveFileName(self,'Transkript exportieren',default,f'*.{kind}')
        if not path: return
        try:
            Path(path).write_text(content,encoding='utf-8')
            note = ' · Untertitel nutzen den gespeicherten, nicht den bearbeiteten Text.' if edited and kind != 'md' else ''
            self.stt_status.setText(f'Exportiert: {Path(path).name}{note}')
        except OSError as e:
            self.stt_status.setText(f'Export fehlgeschlagen: {e}')

    def eventFilter(self, obj, event):
        # Hold O to hear the original, like pressing „Original halten“.
        kind = event.type()
        if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() == Qt.Key.Key_O \
                and not event.modifiers() and hasattr(self,'body') and self.body.isVisible() \
                and self.body.window().isActiveWindow() and self.hold_button.isEnabled() and self.hold_button.isVisible():
            typing = isinstance(QApplication.focusWidget(), (QTextEdit, QLineEdit, QAbstractSpinBox))
            if not typing or self.player.original:
                if not event.isAutoRepeat():
                    pressed = kind == QEvent.Type.KeyPress
                    self.set_original(pressed)
                    self.hold_button.setDown(pressed)
                return True
        return super().eventFilter(obj, event)

    # --- modes and views --------------------------------------------------

    def set_expert(self, on):
        self.expert = on
        self.side_switch.setVisible(on)
        if on:
            self.side_pages.setCurrentIndex(self.side_group.checkedId())
        else:
            self.side_pages.setCurrentIndex(1)
            self.refresh_library()
        self.compare_panel.setVisible(on)
        self.stream_panel.setVisible(on)
        self.listen.set_expert(on)
        if not on:
            self.player.close()
            self.set_original(False)
        self.preferences['expert'] = on

    def show_view(self, index):
        self.view_group.button(index).setChecked(True)
        self.views.setCurrentIndex(index)
        if index == 1:
            self.player.close()
        else:
            self.listen.stop()

    def shortcut_record(self):
        if self.body.isVisible() and self.record_button.isEnabled():
            self.show_view(0)
            self.record_button.click()

    def shortcut_play(self):
        if not self.body.isVisible(): return
        if self.views.currentIndex() == 1: self.listen.toggle()
        elif self.play_button.isVisible() and self.play_button.isEnabled(): self.play_button.click()

    def listen_only(self, action, expert=False):
        if self.body.isVisible() and self.views.currentIndex() == 1 and (self.expert or not expert):
            action()

    def asr_context(self):
        return self.context_edit.text().strip() if self.expert else ''

    def asr_source(self):
        return self.text_source.currentData() if self.expert else 'board'

    def simple_input(self):
        """Simple mode: the ReSpeaker if connected, otherwise the system's default microphone."""
        import sounddevice as sd
        for index,name,*_ in self.rows:
            if 'respeaker' in name.lower(): return index,name
        default = sd.default.device[0]
        for index,name,*_ in self.rows:
            if index == default: return index,name
        return (self.rows[0][0],self.rows[0][1]) if self.rows else (None,None)

    def update_device_card(self):
        index,name = self.simple_input()
        if name is None:
            self.device_label.setText('Kein Mikrofon gefunden')
            self.device_note.setText('Mikrofon anschließen und „Aktualisieren“ wählen.')
        elif 'respeaker' in name.lower():
            self.device_label.setText('●  ReSpeaker Lite verbunden')
            self.device_note.setText('Automatischer Pegel · bereit für Vorlesungen')
        else:
            self.device_label.setText('○  Kein ReSpeaker')
            self.device_note.setText(f'Aufnahme über „{name}“ mit automatischem Pegel. Für Vorlesungen den ReSpeaker anschließen.')
        repolish(self.device_label,'ok',bool(name and 'respeaker' in name.lower()))

    def download_model(self):
        if self.download_process: return
        self.download_process = QProcess(self)
        self.download_process.finished.connect(self.download_finished)
        self.download_process.start(sys.executable,['-u',str(ROOT/'src/download_model.py')])
        self.download_button.setEnabled(False)
        self.stt_status.setText('Sprachmodell wird geladen … das passiert nur einmal.')

    def download_finished(self, code, status):
        error = bytes(self.download_process.readAllStandardError()).decode('utf-8','replace')
        self.download_process.deleteLater()
        self.download_process = None
        ready = asr_ready()
        self.download_button.setVisible(not ready)
        self.download_button.setEnabled(True)
        self.stt_status.setText('Sprachmodell bereit · alles läuft offline.' if ready else 'Download fehlgeschlagen: '+error[-200:])
        self.transcribe_button.setEnabled(ready and self.last is not None)

    def save_preferences(self):
        super().save_preferences()
        self.preferences['expert'] = self.expert
        for name,box in self.auto_boxes.items():
            self.preferences.setdefault('inputs',{}).setdefault(name,{})['auto_gain'] = box.isChecked()
        self.config_path.write_text(json.dumps(self.preferences, ensure_ascii=False, indent=2), encoding='utf-8')

    def update_word_count(self):
        words = len(self.transcript.toPlainText().split())
        self.word_count.setText(f'{words} Wörter' if words else '')

    def copy_transcript(self):
        QApplication.clipboard().setText(self.transcript.toPlainText())
        self.copy_button.setText('Kopiert ✓')
        QTimer.singleShot(1400, lambda:self.copy_button.setText('Text kopieren'))

    def set_badge(self, text):
        kind = {'AUFNAHME':'recording','VERARBEITUNG':'busy'}.get(text,'ready')
        self.state_badge.setText(text)
        repolish(self.state_badge,'state',kind)

    def refresh(self):
        while self.sources_layout.count():
            item = self.sources_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.rows = []
        self.auto_boxes = {}
        try:
            devices = input_devices()
            has_respeaker = any('respeaker' in info['name'].lower() for _, info in devices)
            virtual = lambda name: any(word in name.lower() for word in VIRTUAL)
            devices = sorted(devices, key=lambda d: (0 if 'respeaker' in d[1]['name'].lower() else 2 if virtual(d[1]['name']) else 1))
            more = QWidget()
            more.setObjectName('clear')
            more_layout = QVBoxLayout(more)
            more_layout.setContentsMargins(0,0,0,0)
            more_layout.setSpacing(6)
            for index,info in devices:
                name = info['name']
                prefs = self.preferences.get('inputs',{}).get(name,{})
                source = card('mic')
                box = QVBoxLayout(source)
                box.setContentsMargins(10,8,10,8)
                box.setSpacing(5)
                top = QHBoxLayout()
                active = QCheckBox(name.replace('Microphone','Mikrofon').replace(' Pro von Ben',''))
                active.setToolTip(name)
                active.setChecked(prefs.get('active', 'respeaker' in name.lower() if has_respeaker else 'MacBook' in name and 'Microphone' in name))
                top.addWidget(active,1)
                button = QPushButton('⋯')
                button.setObjectName('icon')
                button.setToolTip('Auto-Pegel, Kanal, Gain und Delay')
                top.addWidget(button)
                box.addLayout(top)
                device_note = label(f'{info["default_samplerate"]/1000:g} kHz · {info["max_input_channels"]} Kanal','micMeta')
                box.addWidget(device_note)
                meter = QProgressBar()
                meter.setObjectName('thin')
                meter.setRange(0,100)
                meter.setTextVisible(False)
                box.addWidget(meter)
                advanced = QWidget()
                advanced.setObjectName('clear')
                grid = QGridLayout(advanced)
                grid.setContentsMargins(0,4,0,0)
                grid.setVerticalSpacing(4)
                channel = QSpinBox(); channel.setRange(1,info['max_input_channels']); channel.setValue(prefs.get('channel',1))
                gain = QDoubleSpinBox(); gain.setRange(-24,24); gain.setValue(prefs.get('gain_db',0)); gain.setDecimals(1)
                delay = QSpinBox(); delay.setRange(0,250); delay.setValue(prefs.get('delay_ms',0))
                auto = QCheckBox('Auto-Pegel')
                auto.setToolTip('Passt die Eingangsverstärkung laufend an: leise, entfernte Sprecher werden angehoben, Pausen nicht.')
                auto.setChecked(prefs.get('auto_gain', True))
                gain.setEnabled(not auto.isChecked())
                auto.toggled.connect(lambda on,g=gain:g.setEnabled(not on))
                self.auto_boxes[name] = auto
                grid.addWidget(auto,0,0,1,2)
                for r,(text,spin) in enumerate([('Kanal',channel),('Gain · dB',gain),('Delay · ms',delay)],1):
                    grid.addWidget(label(text,'muted'),r,0); grid.addWidget(spin,r,1)
                box.addWidget(advanced)
                advanced.hide()
                button.clicked.connect(lambda checked=False,w=advanced:w.setVisible(not w.isVisible()))
                active.toggled.connect(lambda on,w=source:repolish(w,'active',on))
                repolish(source,'active',active.isChecked())
                (more_layout if virtual(name) and not active.isChecked() else self.sources_layout).addWidget(source)
                self.rows.append((index,name,active,channel,gain,delay,meter))
            if more_layout.count():
                toggle = QPushButton(f'Weitere Geräte ({more_layout.count()}) ▸')
                toggle.setObjectName('link')
                toggle.setToolTip('Virtuelle Geräte wie Teams oder Zoom')
                def flip(checked=False, w=more, b=toggle, n=more_layout.count()):
                    w.setVisible(not w.isVisible())
                    b.setText(f'Weitere Geräte ({n}) '+('▾' if w.isVisible() else '▸'))
                toggle.clicked.connect(flip)
                self.sources_layout.addWidget(toggle,0,Qt.AlignmentFlag.AlignLeft)
                self.sources_layout.addWidget(more)
                more.hide()
            else:
                more.deleteLater()
        except Exception as e: self.status.setText(f'Mikrofone: {e}')
        self.update_device_card()

    def load_pair(self, clean, original=None):
        self.player.close()
        self.player_loaded = None
        self.set_original(False)
        self.last = {'clean': str(clean), 'original': original}
        duration = library.duration_of(clean)
        self.waveform.clean = lecture.envelope_file(clean)
        self.waveform.raw = lecture.envelope_file(original or lecture.board_source(clean))
        self.waveform.duration = duration
        self.waveform.live = False
        self.waveform.processed_title = 'VERBESSERT'
        self.seek.setRange(0,int(duration*1000))
        name = lecture.stem_of(clean).replace('LocalVoice_','')
        self.file_label.setText(name[:16].replace('_',' ')+'  ·')
        self.file_label.setToolTip(str(clean))
        self.timer_label.setText(self.format_time(duration,tenths=True))
        busy = self.busy()
        for button in (self.play_button,self.hold_button):
            button.setEnabled(not self.recorder)
        self.transcribe_button.setEnabled(self.voxtral is None and asr_ready())
        self.enhance_button.setEnabled(self.enhance_process is None and not busy)
        text_path = Path(clean).with_suffix('.txt')
        self.transcript.setPlainText(text_path.read_text(encoding='utf-8') if text_path.exists() else '')
        self.listen.load(clean, library.title_of(clean))
        self.listen_button.show()
        if hasattr(self,'library_list'): QTimer.singleShot(0,self.refresh_library)

    def play_toggle(self):
        if self.last and self.player_loaded != self.last['clean']:
            try:
                delay = json.loads(lecture.metadata_path(self.last['clean']).read_text(encoding='utf-8')).get('model_delay_samples',480)
            except (OSError,ValueError):
                delay = 480
            try:
                self.status.setText('Vergleich wird vorbereitet …')
                QApplication.processEvents()
                self.player.load(self.last['clean'],self.last['original'],delay_samples=delay)
                self.player_loaded = self.last['clean']
                self.status.setText('Vergleich bereit · „O“ halten für das Original.')
            except Exception as e:
                self.status.setText(f'Wiedergabe: {e}')
                return
        super().play_toggle()

    def reprocess(self, source=None, stem=None):
        if not (self.last or source) or self.recorder or self.enhance_process or self.voxtral: return
        source = source or self.last['original'] or str(lecture.board_source(self.last['clean']))
        self.enhance_stdout = ''
        self.enhance_stderr = ''
        self.enhance_process = QProcess(self)
        self.enhance_process.readyReadStandardOutput.connect(self.enhance_read)
        self.enhance_process.readyReadStandardError.connect(self.enhance_read)
        self.enhance_process.finished.connect(self.enhance_finished)
        self.enhance_process.start(sys.executable,['-u',str(ROOT/'src/enhance_recording.py'),
            source,'--destination',str(self.folder),
            '--attenuation',str(self.strength.currentData())]+(['--stem',stem] if stem else []))
        self.enhance_button.setEnabled(False)
        self.enhance_button.setText('Verbessert …')
        self.record_button.setEnabled(False)
        self.transcribe_button.setEnabled(False)
        self.open_pair.setEnabled(False)
        self.status.setText('Hörspur wird erstellt …' if stem else 'Neue Version mit „'+self.strength.currentText()+'“ wird erstellt …')

    def enhance_read(self):
        if self.enhance_process:
            self.enhance_stdout += bytes(self.enhance_process.readAllStandardOutput()).decode('utf-8',errors='replace')
            self.enhance_stderr += bytes(self.enhance_process.readAllStandardError()).decode('utf-8',errors='replace')

    def enhance_finished(self,code,status):
        self.enhance_read()
        process = self.enhance_process
        self.enhance_process = None
        process.deleteLater()
        if code:
            self.status.setText('Nachbearbeitung: '+(self.enhance_stderr[-300:] or 'Prozess beendet.'))
        else:
            try:
                result = json.loads(self.enhance_stdout.strip().splitlines()[-1])
                self.load_pair(result['clean'],None)
                self.status.setText('Hörspur fertig. Original und vorherige Versionen bleiben erhalten.')
            except (ValueError,KeyError,OSError) as e:
                self.status.setText('Neue Version konnte nicht geladen werden: '+str(e))
        self.enhance_button.setText('Neu verbessern')
        self.enhance_button.setEnabled(self.last is not None)
        self.record_button.setEnabled(True)
        self.transcribe_button.setEnabled(self.last is not None)
        self.open_pair.setEnabled(True)

    def set_record_controls(self,enabled):
        super().set_record_controls(enabled)
        self.enhance_button.setEnabled(enabled and self.last is not None and self.enhance_process is None)
        self.open_pair.setEnabled(enabled)

    def transcribe(self):
        super().transcribe()
        if self.voxtral:
            self.enhance_button.setEnabled(False)
            self.open_pair.setEnabled(False)

    def stt_finished(self,code,status):
        super().stt_finished(code,status)
        self.enhance_button.setEnabled(self.last is not None and self.enhance_process is None)
        self.open_pair.setEnabled(True)
        if code == 0:
            self.listen.reload_transcript()
            self.refresh_library()
            self.stt_status.setText('Transkript fertig · „Nachhören“ zeigt es synchron zum Audio.')

    def open_recording(self):
        from PySide6.QtWidgets import QFileDialog
        path,_ = QFileDialog.getOpenFileName(self,'LocalVoice-Aufnahme öffnen',str(self.folder),'Hörspur (*_clean.flac *_clean.wav)')
        if path:
            raw = Path(path).with_name(lecture.stem_of(path)+'_original.wav')
            self.load_pair(path,str(raw) if raw.exists() else None)

    def choose_folder(self):
        super().choose_folder()
        self.folder_label.setText(self.folder.name)
        self.folder_label.setToolTip(str(self.folder))
        self.refresh_library()

    def set_original(self,enabled):
        super().set_original(enabled)
        self.source_label.setText('Original' if enabled else 'Verbessert')
        if hasattr(self,'source_label'): repolish(self.source_label,'original',enabled)
        if hasattr(self,'waveform'):
            self.waveform.original = enabled
            self.waveform.update()

    def tick(self):
        recording = self.recorder
        super().tick()
        self.set_badge('AUFNAHME' if self.recorder else 'BEREIT')
        repolish(self.record_button,'recording',self.recorder is not None)
        self.cancel_transcribe.setVisible(self.voxtral is not None)
        self.level_hint.setVisible(self.recorder is not None)
        if self.recorder:
            state = self.recorder.state
            self.waveform.live = True
            if state['status'] == 'recording':
                self.update_level_hint(state)
            if state['status'] == 'recording':
                name = self.recorder.specs[state['active']].name.replace('Microphone','Mikrofon')
                self.status.setText(f'{name} · Sprache wird lokal verbessert')
                self.waveform.clean = np.roll(self.waveform.clean,-5)
                self.waveform.raw = np.roll(self.waveform.raw,-5)
                self.waveform.clean[-5:] = max(state.get('waveform_clean',[0]),default=0)
                self.waveform.raw[-5:] = max(state.get('waveform_raw',[0]),default=0)
        elif recording is not None:
            self.waveform.live = False
        self.waveform.position = self.player.position/self.player.rate
        self.waveform.update()
        self.play_time.setText(self.format_time(self.waveform.position)+' / '+self.format_time(self.waveform.duration))
        if self.voxtral is None:
            try:
                data = json.loads((ROOT/'models/download-status.json').read_text(encoding='utf-8'))
                if data['state'] == 'downloading':
                    self.stt_status.setText(f'Modell-Download · {data.get("percent",0):.1f} % · Audio bleibt lokal')
                elif data['state'] == 'ready' and not self.transcript.toPlainText():
                    self.stt_status.setText('Qwen3-ASR bereit · 8 Bit · lokal auf dem M3 Max')
                elif data['state'] == 'error': self.stt_status.setText('Modell-Download: '+data['message'][:150])
            except (OSError,ValueError,KeyError): pass

    def update_level_hint(self, state):
        import math
        level = max(state.get('levels') or [0])
        db = 20*math.log10(max(level,1e-9))
        self.level_db = .9*getattr(self,'level_db',db)+.1*db
        gain = state.get('auto_gain_db',0.)
        if db > -3:
            text, kind = 'Pegel: zu laut – etwas weiter weg', 'bad'
        elif self.level_db < -55 and gain > 17:
            text, kind = 'Pegel: sehr leise – näher an die Sprecher', 'warn'
        else:
            text, kind = 'Pegel: gut', 'good'
        self.level_hint.setText(text+(f' · Auto {gain:+.0f} dB' if self.expert else ''))
        repolish(self.level_hint,'kind',kind)

    def closeEvent(self,event):
        self.listen.stop()
        if self.enhance_process:
            self.status.setText('Die neue Version wird noch gespeichert. Danach schließen.')
            event.ignore()
            return
        super().closeEvent(event)

    @staticmethod
    def format_time(seconds,tenths=False):
        return f'{int(seconds)//60:02}:{seconds%60:04.1f}' if tenths else f'{int(seconds)//60}:{int(seconds)%60:02}'


STYLE = '''
QWidget {background:#0c121b; color:#e8eef7; font-size:12px;}
QLabel {background:transparent;}
QToolTip {background:#1f2b3c; color:#e8eef7; border:1px solid #33435b; padding:6px; border-radius:6px;}
QLabel#brand {font-size:19px; font-weight:700; letter-spacing:-.5px;}
QLabel#mark {font-size:22px; color:#6ee7d3;}
QLabel#eyebrow {font-size:9px; font-weight:600; color:#7f8ea8; letter-spacing:1.3px;}
QLabel#local {color:#6ee7d3; font-size:11px; background:#10302c; border-radius:10px; padding:4px 10px;}
QLabel#section {font-size:15px; font-weight:650;}
QLabel#sectionCaps {font-size:10px; font-weight:700; color:#7f8ea8; letter-spacing:1.2px;}
QLabel#hero {font-size:20px; font-weight:650; letter-spacing:-.4px;}
QLabel#muted {color:#8d9bb2; font-size:11px;}
QLabel#micMeta {color:#76849b; font-size:10px; padding-left:25px;}
QLabel#time {color:#b7c3d6; font-family:Menlo,Consolas; font-size:11px;}
QLabel#clock {font-family:Menlo,Consolas; font-size:30px; font-weight:500; color:#f2f6fc;}
QLabel#stateBadge {color:#6ee7d3; background:#10302c; border-radius:9px; padding:3px 9px; font-size:9px; font-weight:700; letter-spacing:1px;}
QLabel#stateBadge[state="recording"] {color:#ffd9dc; background:#4a1820;}
QLabel#stateBadge[state="busy"] {color:#2c1d05; background:#f5c26b;}
QLabel#source {color:#6ee7d3; font-size:11px; font-weight:600;}
QLabel#source[original="true"] {color:#f5c26b;}
QLabel#banner {background:#10302c; color:#9eeedf; padding:10px 12px; border-radius:8px;}
QFrame#card {background:#141c28; border:1px solid #212c3d; border-radius:12px;}
QFrame#heroCard {background:#141c28; border:1px solid #212c3d; border-radius:14px;}
QFrame#mic {background:#18212f; border:1px solid #232f42; border-radius:9px;}
QFrame#mic[active="true"] {border-color:#2f6f66; background:#152a2c;}
QFrame#card QLabel,QFrame#card QCheckBox,QFrame#mic QLabel,QFrame#mic QCheckBox,QFrame#heroCard QLabel {background:transparent;}
QPushButton {background:#1f2b3c; border:1px solid #2e3d54; border-radius:8px; padding:7px 12px; font-weight:550;}
QPushButton:hover {background:#2a3a51; border-color:#46607f;}
QPushButton:pressed {background:#34486a;}
QPushButton:disabled {background:#151d29; color:#56637a; border-color:#1f2938;}
QPushButton#record {background:#6ee7d3; color:#062a25; border:0; border-radius:20px; padding:12px 22px; font-size:13px; font-weight:700; min-width:170px;}
QPushButton#record:hover {background:#98f0e2;}
QPushButton#record[recording="true"] {background:#ff6370; color:#2b0509;}
QPushButton#record[recording="true"]:hover {background:#ff8a94;}
QPushButton#record:disabled {background:#1f3b39; color:#5e8580;}
QPushButton#primary {background:#6ee7d3; color:#062a25; border:0; font-weight:700;}
QPushButton#primary:hover {background:#98f0e2;}
QPushButton#play {min-width:104px;}
QPushButton#hold:pressed {background:#f5c26b; color:#2c1d05; border-color:#f5c26b;}
QPushButton#link {background:transparent; border:0; color:#8fb6d8; padding:2px 4px; font-size:11px;}
QPushButton#link:hover {color:#c4dcf2;}
QPushButton#link:disabled {color:#56637a;}
QPushButton#icon {background:transparent; border:0; color:#8d9bb2; padding:0 4px; font-size:15px; font-weight:700;}
QPushButton#icon:hover {color:#e8eef7;}
QComboBox,QSpinBox,QDoubleSpinBox {background:#1a2433; border:1px solid #2e3d54; border-radius:7px; padding:5px 8px; min-height:17px;}
QComboBox:hover {border-color:#46607f;}
QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled {color:#56637a; border-color:#1f2938;}
QComboBox::drop-down {border:0; width:20px;}
QComboBox QAbstractItemView {background:#1f2b3c; border:1px solid #33435b; selection-background-color:#2f5f6f; outline:0;}
QCheckBox {spacing:8px; font-size:12px; font-weight:550;}
QCheckBox::indicator {width:16px; height:16px; border:1px solid #5e7090; border-radius:5px; background:#141e2c;}
QCheckBox::indicator:checked {background:#6ee7d3; border-color:#6ee7d3;}
QCheckBox:disabled {color:#56637a;}
QProgressBar {border:0; border-radius:3px; background:#233046; min-height:6px; max-height:6px;}
QProgressBar#thin {min-height:4px; max-height:4px; border-radius:2px;}
QProgressBar::chunk {background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6ee7d3, stop:.75 #6ee7d3, stop:.9 #f5c26b, stop:1 #ff6370); border-radius:2px;}
QTextEdit {background:#0f1620; border:1px solid #212c3d; border-radius:9px; padding:10px; font-size:14px; line-height:150%; selection-background-color:#2f5f6f;}
QTextEdit:focus {border-color:#2f6f66;}
QScrollArea {border:0; background:transparent;}
QScrollBar:vertical {width:6px; background:transparent; margin:2px;}
QScrollBar::handle:vertical {background:#2e3d54; border-radius:3px; min-height:30px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical,QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {height:0; background:transparent;}
QSlider::groove:horizontal {height:4px; background:#2a3a51; border-radius:2px;}
QSlider::sub-page:horizontal {background:#6ee7d3; border-radius:2px;}
QSlider::handle:horizontal {width:12px; height:12px; margin:-4px 0; background:#e8f6f3; border-radius:6px;}
QSlider:disabled {background:transparent;}
QTabWidget::pane {border:0;}
QTabBar {background:transparent;}
QTabBar::tab {background:transparent; color:#8d9bb2; padding:7px 14px; margin:6px 2px; border-radius:8px; font-weight:600;}
QTabBar::tab:selected {background:#1f2b3c; color:#e8eef7;}
QTabBar::tab:hover:!selected {color:#cdd7e6;}
QTabBar::tab:disabled {color:#4d5a6e;}
QGroupBox {background:#141c28; border:1px solid #212c3d; border-radius:12px; margin-top:22px; padding:14px 12px 10px; font-weight:650;}
QGroupBox::title {subcontrol-origin:margin; left:4px; top:2px; padding:0 4px; color:#7f8ea8;}
QWidget#clear {background:transparent;}
QFrame#deviceCard {background:#141c28; border:1px solid #212c3d; border-radius:12px;}
QFrame#deviceCard QLabel {background:transparent;}
QLabel#deviceTitle {font-weight:650; font-size:12px; color:#f5c26b;}
QLabel#deviceTitle[ok="true"] {color:#6ee7d3;}
QLabel#levelHint {font-size:11px; font-weight:600; color:#6ee7d3;}
QLabel#levelHint[kind="warn"] {color:#f5c26b;}
QLabel#levelHint[kind="bad"] {color:#ff6370;}
QPushButton#playRound {background:#6ee7d3; color:#062a25; border:0; border-radius:19px; min-width:38px; max-width:38px; min-height:38px; max-height:38px; padding:0; font-size:14px; font-weight:700;}
QPushButton#playRound:hover {background:#98f0e2;}
QPushButton#playRound:disabled {background:#1f3b39; color:#5e8580;}
QTextEdit#reader {background:transparent; border:0; padding:4px 2px; font-size:15px;}
QListWidget#marks {background:#0f1620; border:1px solid #212c3d; border-radius:9px; outline:0;}
QListWidget#marks::item {padding:6px 8px; border-radius:6px;}
QListWidget#marks::item:selected {background:#2f5f6f;}
QFrame#segment {background:#141c28; border:1px solid #212c3d; border-radius:10px;}
QPushButton#segmentButton {background:transparent; border:0; border-radius:7px; padding:6px 0; color:#8d9bb2; font-weight:600;}
QPushButton#segmentButton:checked {background:#253246; color:#e8eef7;}
QLineEdit {background:#1a2433; border:1px solid #2e3d54; border-radius:8px; padding:6px 9px;}
QLineEdit:focus {border-color:#2f6f66;}
QListWidget#library {background:transparent; border:0; outline:0;}
QListWidget#library::item {background:#141c28; border:1px solid #212c3d; border-radius:9px; margin-bottom:6px;}
QListWidget#library::item:hover {border-color:#33435b;}
QListWidget#library::item:selected {background:#152a2c; border-color:#2f6f66;}
QWidget#libraryRow, QWidget#libraryRow QLabel {background:transparent;}
QLabel#libraryTitle {font-weight:650; font-size:12px;}
QLabel#libraryText {color:#b7c3d6; font-size:11px;}
QLabel#libraryTag {color:#6ee7d3; font-size:10px;}
QMenu {background:#1f2b3c; border:1px solid #33435b; padding:4px; border-radius:8px;}
QMenu::item {padding:6px 18px; border-radius:5px;}
QMenu::item:selected {background:#2f5f6f;}
QPushButton::menu-indicator {width:0;}
QStatusBar {background:#0f1620; color:#b7c3d6; border-top:1px solid #1b2433;}
QStatusBar::item {border:0;}
'''


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName('LocalVoice Studio')
    app.setOrganizationName('LocalVoice')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Studio()
    window.show()
    sys.exit(app.exec())
