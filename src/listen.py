"""Listen view: replay a lecture like a podcast, with the transcript following along."""
import bisect
import html
import json
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QSlider, QTextEdit, QVBoxLayout, QWidget)

import lecture
import library

SPEEDS = (1., 1.25, 1.5, 1.75, 2.)


def clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds//3600}:{seconds//60%60:02}:{seconds%60:02}' if seconds >= 3600 else f'{seconds//60}:{seconds%60:02}'


def named(widget, name):
    widget.setObjectName(name)
    return widget


class ListenView(QWidget):
    def __init__(self):
        super().__init__()
        self.clean = None
        self.segments = []
        self.starts = []
        self.gaps = []
        self.bookmarks = []
        self.current = -1
        self.user_scrolled = 0.
        self.last_saved = 0.
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.8)
        self.player.setAudioOutput(self.audio)
        if hasattr(self.player, 'setPitchCompensation'):
            self.player.setPitchCompensation(True)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.state_changed)
        self.player.errorOccurred.connect(lambda *_: self.hint.setText('Wiedergabe: '+self.player.errorString()))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        page = named(QFrame(), 'card')
        body = QHBoxLayout(page)
        body.setContentsMargins(20, 16, 20, 16)
        body.setSpacing(16)
        reading = QVBoxLayout()
        head = QHBoxLayout()
        self.title = named(QLabel('Noch keine Aufnahme gewählt'), 'section')
        head.addWidget(self.title)
        head.addStretch()
        self.resume = named(QPushButton(''), 'link')
        self.resume.hide()
        self.resume.clicked.connect(lambda: (self.player.setPosition(0), self.resume.hide()))
        head.addWidget(self.resume)
        reading.addLayout(head)
        self.hint = named(QLabel('Klick auf einen Absatz springt im Audio genau dorthin.'), 'muted')
        reading.addWidget(self.hint)
        self.text = named(QTextEdit(), 'reader')
        self.text.setReadOnly(True)
        self.text.viewport().installEventFilter(self)
        self.text.verticalScrollBar().sliderPressed.connect(self.note_scroll)
        self.text.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        reading.addWidget(self.text, 1)
        body.addLayout(reading, 1)
        self.side = QWidget()
        self.side.setObjectName('clear')
        side = QVBoxLayout(self.side)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(named(QLabel('MERKER'), 'sectionCaps'))
        self.marks = named(QListWidget(), 'marks')
        self.marks.itemClicked.connect(lambda item: self.seek(item.data(Qt.ItemDataRole.UserRole)))
        self.marks.itemDoubleClicked.connect(self.edit_bookmark)
        side.addWidget(self.marks, 1)
        side.addWidget(named(QLabel('Doppelklick: Notiz · Entf: löschen'), 'muted'))
        self.side.setFixedWidth(230)
        body.addWidget(self.side)
        layout.addWidget(page, 1)

        bar = named(QFrame(), 'heroCard')
        controls = QHBoxLayout(bar)
        controls.setContentsMargins(18, 12, 18, 12)
        controls.setSpacing(10)
        self.back = QPushButton('↺ 15')
        self.back.setToolTip('15 Sekunden zurück · ←')
        self.back.clicked.connect(lambda: self.skip(-15))
        self.play = named(QPushButton('▶'), 'playRound')
        self.play.setToolTip('Abspielen/Pause · Leertaste')
        self.play.clicked.connect(self.toggle)
        self.forward = QPushButton('30 ↻')
        self.forward.setToolTip('30 Sekunden vor · →')
        self.forward.clicked.connect(lambda: self.skip(30))
        for widget in (self.back, self.play, self.forward):
            controls.addWidget(widget)
        self.position = named(QLabel('0:00'), 'time')
        controls.addWidget(self.position)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(self.player.setPosition)
        controls.addWidget(self.slider, 1)
        self.length = named(QLabel('0:00'), 'time')
        controls.addWidget(self.length)
        self.speed = QComboBox()
        for value in SPEEDS:
            self.speed.addItem(f'{value:g}×'.replace('.', ','), value)
        self.speed.setToolTip('Tempo – die Stimme bleibt in ihrer Tonhöhe')
        self.speed.currentIndexChanged.connect(lambda: self.player.setPlaybackRate(self.speed.currentData()))
        controls.addWidget(self.speed)
        self.skip_pauses = QCheckBox('Pausen überspringen')
        self.skip_pauses.setToolTip('Springt über Stellen ohne Sprache, die länger als etwa 1 s sind.')
        controls.addWidget(self.skip_pauses)
        self.mark_button = QPushButton('＋ Merker')
        self.mark_button.setToolTip('Stelle merken · Taste M')
        self.mark_button.clicked.connect(self.add_bookmark)
        controls.addWidget(self.mark_button)
        layout.addWidget(bar)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.set_enabled(False)

    # --- loading ---------------------------------------------------------

    def set_enabled(self, on):
        for widget in (self.back, self.play, self.forward, self.slider, self.speed, self.skip_pauses, self.mark_button):
            widget.setEnabled(on)

    def set_expert(self, on):
        self.side.setVisible(on)
        self.skip_pauses.setVisible(on)
        self.mark_button.setVisible(on)

    def set_output(self, name):
        for device in QMediaDevices.audioOutputs():
            if device.description() == name:
                self.audio.setDevice(device)
                return

    def load(self, clean, title=''):
        self.save_position()
        self.player.stop()
        self.clean = Path(clean)
        self.title.setText(title or lecture.stem_of(clean).removeprefix('LocalVoice_').replace('_', ' '))
        state = self.read_state()
        self.bookmarks = state.get('bookmarks', [])
        self.show_bookmarks()
        self.resume_at = float(state.get('position', 0))
        self.resume.hide()
        # Set before the source: durationChanged may fire immediately and applies resume_at.
        self.player.setSource(QUrl.fromLocalFile(str(self.clean)))
        self.reload_transcript()
        self.set_enabled(True)

    def reload_transcript(self):
        if not self.clean:
            return
        duration = library.duration_of(self.clean)
        text_path = self.clean.with_suffix('.txt')
        text = text_path.read_text(encoding='utf-8') if text_path.exists() else ''
        self.segments = library.cues(self.clean, text, duration)
        self.starts = [a for a, _, _ in self.segments]
        try:
            regions = json.loads(lecture.transcript_path(self.clean).read_text()).get('speech_regions', [])
            self.gaps = lecture.skip_map([tuple(r) for r in regions])
        except (OSError, ValueError):
            self.gaps = []
        self.current = -1
        if not self.segments:
            self.text.setHtml('<p style="color:#8d9bb2">Noch kein Transkript. Du kannst trotzdem anhören.</p>')
            return
        blocks = [f'<p style="margin:0 0 14px 0"><span style="color:#6ee7d3;font-family:Menlo,Consolas;font-size:11px">'
                  f'{clock(a)}</span>&nbsp;&nbsp;{html.escape(t)}</p>' for a, _, t in self.segments]
        self.text.setHtml(''.join(blocks))

    def duration_changed(self, ms):
        self.slider.setRange(0, ms)
        self.length.setText(clock(ms/1000))
        resume = getattr(self, 'resume_at', 0)
        if 10 < resume < ms/1000-10:
            self.player.setPosition(int(resume*1000))
            self.resume.setText(f'Weiter ab {clock(resume)} · Von vorn')
            self.resume.show()
        self.resume_at = 0

    # --- playback --------------------------------------------------------

    def toggle(self):
        if not self.clean:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.save_position()
        else:
            self.player.play()

    def stop(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        self.save_position()

    def skip(self, seconds):
        self.seek(self.player.position()/1000+seconds)

    def seek(self, seconds):
        self.player.setPosition(int(max(0, seconds)*1000))
        self.user_scrolled = 0.

    def state_changed(self, state):
        self.play.setText('❚❚' if state == QMediaPlayer.PlaybackState.PlayingState else '▶')

    def tick(self):
        if not self.clean:
            return
        ms = self.player.position()
        seconds = ms/1000
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.position.setText(clock(seconds))
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if playing and self.skip_pauses.isChecked() and self.skip_pauses.isVisible():
            for a, b in self.gaps:
                if a <= seconds < b-.05:
                    self.player.setPosition(int(b*1000))
                    break
        index = bisect.bisect_right(self.starts, seconds)-1
        if index != self.current:
            self.current = index
            self.highlight(index)
        if playing and time.monotonic()-self.last_saved > 5:
            self.save_position()

    def highlight(self, index):
        if index < 0 or index >= len(self.segments):
            self.text.setExtraSelections([])
            return
        block = self.text.document().findBlockByNumber(index)
        mark = QTextEdit.ExtraSelection()
        mark.cursor = QTextCursor(block)
        mark.format.setBackground(QColor('#173b37'))
        mark.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        self.text.setExtraSelections([mark])
        # Follow along unless the reader scrolled away in the last few seconds.
        if time.monotonic()-self.user_scrolled > 4:
            rect = self.text.document().documentLayout().blockBoundingRect(block)
            bar = self.text.verticalScrollBar()
            if rect.top() < bar.value() or rect.bottom() > bar.value()+self.text.viewport().height():
                bar.setValue(int(max(0, rect.top()-self.text.viewport().height()*.3)))

    def note_scroll(self):
        self.user_scrolled = time.monotonic()

    def eventFilter(self, obj, event):
        if obj is self.text.viewport():
            if event.type() == QEvent.Type.Wheel:
                self.note_scroll()
            elif event.type() == QEvent.Type.MouseButtonRelease and self.segments and not self.text.textCursor().hasSelection():
                block = self.text.cursorForPosition(event.position().toPoint()).blockNumber()
                if 0 <= block < len(self.segments):
                    self.seek(self.segments[block][0])
                    if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                        self.player.play()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.marks.currentItem():
            del self.bookmarks[self.marks.currentRow()]
            self.save_state()
            self.show_bookmarks()
            return
        super().keyPressEvent(event)

    # --- bookmarks and resume -------------------------------------------

    def add_bookmark(self):
        if not self.clean:
            return
        self.bookmarks.append({'t': round(self.player.position()/1000, 1), 'note': ''})
        self.bookmarks.sort(key=lambda b: b['t'])
        self.save_state()
        self.show_bookmarks()

    def edit_bookmark(self, item):
        index = self.marks.row(item)
        note, ok = QInputDialog.getText(self, 'Merker', 'Notiz:', text=self.bookmarks[index].get('note', ''))
        if ok:
            self.bookmarks[index]['note'] = note
            self.save_state()
            self.show_bookmarks()

    def show_bookmarks(self):
        self.marks.clear()
        for b in self.bookmarks:
            item = QListWidgetItem(f'{clock(b["t"])}  {b.get("note") or ""}'.rstrip())
            item.setData(Qt.ItemDataRole.UserRole, b['t'])
            self.marks.addItem(item)

    def read_state(self):
        try:
            return json.loads(lecture.listen_path(self.clean).read_text())
        except (OSError, ValueError, TypeError):
            return {}

    def save_state(self, **extra):
        if not self.clean:
            return
        state = {**self.read_state(), 'bookmarks': self.bookmarks, **extra}
        try:
            lecture.listen_path(self.clean).write_text(json.dumps(state, ensure_ascii=False, indent=2))
        except OSError:
            pass

    def save_position(self):
        if self.clean and self.player.duration() > 0:
            self.save_state(position=round(self.player.position()/1000, 1))
            self.last_saved = time.monotonic()
