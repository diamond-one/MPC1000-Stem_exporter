from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QRectF, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

from .exporter import StemExporter
from . import __version__
from .model import BANKS, PAD_ORDER, Project, Track, plan_filenames, safe_stem
from .setup_dialog import SetupDialog

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
from .theme import STYLE, BLUE, RED, YELLOW, PAPER, SURFACE, INK, MUTED, load_fonts, app_icon, apply_theme, is_dark, pad_palette, sync_titlebar

ACCENT = BLUE


def label(text, role=None):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    return widget


def button(text, callback, role=None):
    widget = QPushButton(text)
    if role:
        widget.setObjectName(role)
    widget.clicked.connect(callback)
    return widget


def duration(seconds):
    seconds = math.ceil(seconds)
    return f"{seconds // 60:02}:{seconds % 60:02}"


class NameEdit(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)


class Pad(QPushButton):
    toggle_track = Signal(int)
    focus_track = Signal(int)
    rename_track = Signal(int)
    navigate = Signal(int)

    def __init__(self, track):
        super().__init__()
        self.track = track
        self.editable = True
        self.phase = 0
        self.pending_number = track.number
        self.double_click = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.toggle_track.emit(self.pending_number))
        self.setMinimumSize(108, 82)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_track(track)

    def set_track(self, track):
        self.track = track
        self.setAccessibleName(f"Track {track.number}, {track.name}")
        self.setAccessibleDescription(f"{'Selected' if track.selected else 'Not selected'}. {track.status}. Space selects, F2 renames.")
        self.setToolTip(f"Track {track.number} · {track.bank}{track.pad:02}\n{track.name}\nClick to select · Double-click or F2 to rename")
        self.update()

    def focusInEvent(self, event):
        self.focus_track.emit(self.track.number)
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.focus_track.emit(self.track.number)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            if self.double_click:
                self.double_click = False
            elif self.editable:
                self.pending_number = self.track.number
                self.timer.start(QApplication.doubleClickInterval())
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.timer.stop()
            self.double_click = True
            if self.editable:
                self.rename_track.emit(self.track.number)
            event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        moves = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -4, Qt.Key.Key_Down: 4}
        if key in moves:
            self.navigate.emit(moves[key])
        elif self.editable and key in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggle_track.emit(self.track.number)
        elif self.editable and key == Qt.Key.Key_F2:
            self.rename_track.emit(self.track.number)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        t = self.track
        palette = pad_palette()
        background, foreground, edge = palette['pad'], palette['ink'], palette['pad']
        state, marker = ('Selected', RED) if t.selected else ('Not selected', palette['muted'])
        if t.selected:
            background, foreground, edge = RED, '#FFF8E7', RED
        if t.status == 'complete':
            background, foreground, edge = palette['saved'], palette['saved_ink'], palette['focus']
            state, marker = 'Saved', palette['saved_ink']
        elif t.status == 'error':
            background, foreground, edge = palette['error'], palette['error_ink'], RED
            state, marker = 'Error', palette['error_ink']
        elif t.status == 'recording':
            background, foreground, edge = RED, '#FFF8E7', RED
            state, marker = 'Recording', '#FFF8E7'
        elif t.status == 'queued':
            state, marker = 'Waiting', '#FFF8E7' if t.selected else palette['focus']
        p.setBrush(QColor(background))
        p.setPen(QPen(QColor(edge), 1))
        p.drawRect(r)
        if self.hasFocus():
            p.setPen(QPen(QColor('#FFF8E7' if background == RED else palette['focus']), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(4, 4, -4, -4))
        p.setPen(QColor(foreground))
        p.setFont(QFont('Space Grotesk', 18, QFont.Weight.DemiBold))
        p.drawText(r.adjusted(13, 7, -12, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, f'{t.number:02}')
        p.setFont(QFont('Segoe UI', 9))
        p.drawText(r.adjusted(0, 11, -13, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, f'{t.bank}{t.pad:02}')
        p.setFont(QFont('Segoe UI', 10, QFont.Weight.DemiBold))
        text = p.fontMetrics().elidedText(t.name, Qt.TextElideMode.ElideRight, int(r.width() - 26))
        p.drawText(r.adjusted(13, 37, -10, -20), Qt.AlignmentFlag.AlignTop, text)
        p.setFont(QFont('Segoe UI', 8))
        p.setPen(QColor(foreground if t.selected else marker))
        p.drawText(r.adjusted(13, 0, -10, -8), Qt.AlignmentFlag.AlignBottom, state)
        p.end()


class AppMark(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(48, 43)
        self.setAccessibleName('MPC Stem Exporter')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(RED))
        p.drawEllipse(QRectF(0, 0, 27, 27))
        p.setBrush(QColor(BLUE))
        p.drawRect(QRectF(24, 18, 23, 23))
        p.setBrush(QColor(YELLOW))
        p.drawRect(QRectF(0, 34, 21, 7))
        p.end()


class Worker(QThread):
    event = Signal(dict)
    error = Signal(str)

    def __init__(self, project):
        super().__init__()
        self.exporter = StemExporter(project, self.event.emit)

    def run(self):
        try:
            self.exporter.run()
        except Exception as exc:
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, state_path: Path | None = None):
        super().__init__()
        load_fonts()
        self.state_path = state_path or ROOT / "user-data" / "last-project.json"
        self.ui_path = self.state_path.parent / 'ui-preferences.json'
        try:
            preferences = json.loads(self.ui_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            preferences = {}
        self.dark_mode = isinstance(preferences, dict) and preferences.get('theme') == 'dark'
        apply_theme(self.dark_mode)
        self.setWindowIcon(app_icon())
        self.project_path = None
        self.project = Project()
        self.load_warning = ""
        if self.state_path.exists():
            try:
                self.project = Project.load(self.state_path)
            except Exception as exc:
                self.load_warning = f"Could not restore last project: {exc}"
                # Keep a malformed autosave for inspection rather than replacing it.
                self.state_path = self.state_path.with_name(f"recovered-{int(time.time())}.json")
        if not self.project.settings.output_directory:
            self.project.settings.output_directory = str(ROOT / "recordings")
        self.bank = "A"
        self.focused = 1
        self.worker = None
        self.busy = False
        self.last_directory = None
        self.setWindowTitle(f"MPC Stem Exporter {__version__}")
        self.resize(1240, 860)
        self.setMinimumSize(1040, 760)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.addWidget(AppMark())
        header.addSpacing(8)
        branding = QVBoxLayout()
        branding.setSpacing(2)
        branding.addWidget(label("MPC Stem Exporter", "title"))
        branding.addWidget(label("Record individual tracks from your MPC1000 / JJOS3", "muted"))
        header.addLayout(branding)
        header.addStretch()
        self.theme_button = button('Theme: Dark' if self.dark_mode else 'Theme: Light', self.toggle_theme, 'quiet')
        self.theme_button.setAccessibleName('Switch light or dark theme')
        self.theme_button.setToolTip('Switch between light and dark. Your choice is remembered.')
        header.addWidget(self.theme_button)
        self.new_button = button("New", self.new_project, "quiet")
        self.open_button = button("Open", self.open_project, "quiet")
        self.save_button = button("Save project", self.save_project)
        header.addWidget(self.new_button)
        header.addWidget(self.open_button)
        header.addWidget(self.save_button)
        layout.addLayout(header)

        connection = QFrame()
        connection.setObjectName("connectionStrip")
        row = QHBoxLayout(connection)
        row.setContentsMargins(16, 11, 12, 11)
        self.connection_label = label("", "muted")
        self.connection_label.setWordWrap(True)
        row.addWidget(self.connection_label, 1)
        self.setup_button = button("Connections && setup", self.setup)
        row.addWidget(self.setup_button)
        layout.addWidget(connection)

        body = QHBoxLayout()
        body.setSpacing(20)
        pads_column = QVBoxLayout()
        pads_column.setSpacing(8)
        bankrow = QHBoxLayout()
        bankrow.addWidget(label("Track bank", "trackLabel"))
        bankrow.addSpacing(10)
        self.bank_buttons = {}
        for bank in BANKS:
            b = button(bank, lambda checked=False, bank=bank: self.switch_bank(bank), "bank")
            b.setCheckable(True)
            b.setAccessibleName(f"Bank {bank}")
            bankrow.addWidget(b)
            self.bank_buttons[bank] = b
        bankrow.addStretch()
        self.bank_range = label("Tracks 01–16", "muted")
        bankrow.addWidget(self.bank_range)
        pads_column.addLayout(bankrow)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.pads = []
        for i, track in enumerate(self.project.bank_tracks(self.bank)):
            pad = Pad(track)
            pad.toggle_track.connect(self.toggle_track)
            pad.focus_track.connect(self.focus_track)
            pad.rename_track.connect(self.rename_track)
            pad.navigate.connect(lambda delta, index=i: self.navigate(index, delta))
            self.pads.append(pad)
            grid.addWidget(pad, i // 4, i % 4)
            grid.setColumnStretch(i % 4, 1)
            grid.setRowStretch(i // 4, 1)
        pads_column.addLayout(grid, 1)
        actions = QHBoxLayout()
        self.selection_buttons = []
        for text, enabled, bank_only in (("Select all 64", True, False), ("Clear selection", False, False),
                                          ("Select bank", True, True), ("Clear bank", False, True)):
            b = button(text, lambda checked=False, on=enabled, local=bank_only: self.select_tracks(on, local), "quiet")
            self.selection_buttons.append(b)
            actions.addWidget(b)
        actions.addStretch()
        pads_column.addLayout(actions)

        editor = QFrame()
        editor.setObjectName("trackEditor")
        el = QVBoxLayout(editor)
        el.setContentsMargins(12, 9, 12, 9)
        er = QHBoxLayout()
        self.editor_label = label("Track 01", "trackLabel")
        er.addWidget(self.editor_label)
        self.include = QCheckBox("Export this track")
        self.include.toggled.connect(self.set_included)
        self.name_edit = NameEdit()
        self.name_edit.setPlaceholderText("Name this track…")
        self.name_edit.setAccessibleName("Track name")
        self.name_edit.setToolTip("Enter to confirm · Esc to cancel · F2 to rename a focused pad")
        self.name_edit.returnPressed.connect(self.commit_name)
        self.name_edit.cancelled.connect(self.cancel_name)
        self.name_edit.editingFinished.connect(self.commit_name)
        er.addWidget(self.name_edit, 1)
        er.addWidget(self.include)
        el.addLayout(er)
        self.filename_label = label("", "muted")
        el.addWidget(self.filename_label)
        pads_column.addWidget(editor)
        body.addLayout(pads_column, 7)

        sidebar = QFrame()
        sidebar.setObjectName("sessionPanel")
        sidebar.setMinimumWidth(330)
        sidebar.setMaximumWidth(380)
        panel = QVBoxLayout(sidebar)
        panel.setContentsMargins(0, 0, 0, 12)
        panel.setSpacing(8)
        self.session_scroll = QScrollArea()
        self.session_scroll.setWidgetResizable(True)
        self.session_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.session_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_scroll.setAccessibleName('Recording settings')
        settings_body = QWidget()
        settings_body.setObjectName('recordingSettings')
        side = QVBoxLayout(settings_body)
        side.setContentsMargins(16, 15, 16, 10)
        side.setSpacing(10)
        self.session_scroll.setWidget(settings_body)
        panel.addWidget(self.session_scroll, 1)
        side.addWidget(label('Recording session', 'section'))
        self.title_edit = QLineEdit(self.project.title)
        self.title_edit.setAccessibleName("Session name")
        self.title_edit.editingFinished.connect(self.update_settings)
        side.addWidget(self.title_edit)
        timings = QFormLayout()
        timings.setSpacing(9)
        self.seq = QDoubleSpinBox()
        self.seq.setRange(0.1, 14400)
        self.seq.setDecimals(2)
        self.seq.setSuffix(" s")
        self.seq.setAccessibleName("Sequence duration")
        self.tail = QDoubleSpinBox()
        self.tail.setRange(0, 600)
        self.tail.setDecimals(2)
        self.tail.setSuffix(" s")
        self.tail.setAccessibleName("Tail duration")
        timings.addRow("Sequence length", self.seq)
        timings.addRow("End tail", self.tail)
        side.addLayout(timings)
        self.format_label = label("", "muted")
        self.format_label.setWordWrap(True)
        side.addWidget(self.format_label)
        side.addSpacing(2)
        side.addWidget(label("Save stems to", "trackLabel"))
        folderrow = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setAccessibleName("Output directory")
        self.folder_edit.editingFinished.connect(self.update_settings)
        self.browse_button = button("Browse", self.browse_folder)
        self.browse_button.setAccessibleName("Choose output folder")
        self.browse_button.setMaximumWidth(80)
        folderrow.addWidget(self.folder_edit)
        folderrow.addWidget(self.browse_button)
        side.addLayout(folderrow)
        self.queue_heading = label("Stem queue", "section")
        side.addWidget(self.queue_heading)
        self.queue_list = QListWidget()
        self.queue_list.setAccessibleName("Selected tracks — activate to jump to a pad")
        self.queue_list.itemClicked.connect(self.focus_queued_track)
        self.queue_list.itemActivated.connect(self.focus_queued_track)
        self.queue_list.setMinimumHeight(100)
        side.addWidget(self.queue_list, 1)
        self.estimate = label("", "muted")
        self.estimate.setWordWrap(True)
        self.estimate.setContentsMargins(16, 0, 16, 0)
        panel.addWidget(self.estimate)
        self.readiness = label('', 'readiness')
        self.readiness.setWordWrap(True)
        self.readiness.setContentsMargins(16, 0, 16, 0)
        panel.addWidget(self.readiness)
        self.export_button = button('Select tracks to record', self.start_export, 'primary')
        panel.addWidget(self.export_button)
        self.cancel_button = button("Stop recording", self.cancel_export, "cancel")
        self.cancel_button.hide()
        panel.addWidget(self.cancel_button)
        self.open_output = button("Open saved stems", self.open_recordings, "quiet")
        self.open_output.hide()
        panel.addWidget(self.open_output)
        body.addWidget(sidebar, 3)
        layout.addLayout(body, 1)

        footer = QVBoxLayout()
        footer.setSpacing(7)
        statusrow = QHBoxLayout()
        self.status_label = label("Choose the tracks you want to record.")
        self.status_label.setWordWrap(True)
        statusrow.addWidget(self.status_label, 1)
        self.meter_label = label("Input —", "muted")
        statusrow.addWidget(self.meter_label)
        self.log_button = button("Activity", self.toggle_log, "quiet")
        statusrow.addWidget(self.log_button)
        footer.addLayout(statusrow)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.hide()
        self.progress.setTextVisible(False)
        footer.addWidget(self.progress)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumHeight(115)
        self.activity.setMaximumBlockCount(500)
        self.activity.hide()
        footer.addWidget(self.activity)
        layout.addLayout(footer)

        self.load_settings()
        for control in (self.seq, self.tail):
            control.valueChanged.connect(self.update_settings)
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save_project)
        QShortcut(QKeySequence.StandardKey.Open, self, activated=self.open_project)
        self.animation = QTimer(self)
        self.animation.timeout.connect(self.animate)
        self.animation.start(100)
        self.refresh()
        self.focus_track(1)
        if self.load_warning:
            self.log(self.load_warning)
            self.status_label.setText(self.load_warning)

    def showEvent(self, event):
        super().showEvent(event)
        sync_titlebar(self)

    def toggle_theme(self):
        from .storage import atomic_json
        self.dark_mode = not self.dark_mode
        apply_theme(self.dark_mode)
        self.theme_button.setText('Theme: Dark' if self.dark_mode else 'Theme: Light')
        for pad in self.pads:
            pad.update()
        try:
            atomic_json(self.ui_path, {'theme': 'dark' if self.dark_mode else 'light'})
        except OSError as exc:
            self.log('Theme changed; preference could not be saved: ' + str(exc))

    def log(self, message):
        self.activity.appendPlainText(f"{time.strftime('%H:%M:%S')}  {message}")

    def load_settings(self):
        s = self.project.settings
        # Older sample projects cannot silently produce synthetic stems in the UI.
        if s.mode != "hardware":
            s.mode = "hardware"
            s.setup_verified = False
            for track in self.project.tracks:
                track.status = "idle"
        for control, value in ((self.seq, s.sequence_seconds), (self.tail, s.tail_seconds)):
            control.blockSignals(True)
            control.setValue(value)
            control.blockSignals(False)
        self.folder_edit.setText(s.output_directory)
        self.title_edit.setText(self.project.title)

    def update_settings(self, *_):
        if self.busy:
            return
        s = self.project.settings
        s.mode = "hardware"
        s.sequence_seconds, s.tail_seconds = self.seq.value(), self.tail.value()
        s.output_directory = self.folder_edit.text().strip()
        self.folder_edit.setToolTip(s.output_directory)
        self.project.title = self.title_edit.text().strip() or "Untitled session"
        self.persist()
        self.refresh()

    def persist(self):
        try:
            self.project.save(self.state_path)
        except OSError as exc:
            self.status_label.setText(f"Could not autosave: {exc}")
            self.log(f"Autosave failed: {exc}")

    def refresh(self):
        selected = self.project.selected_tracks()
        n = len(selected)
        for bank, b in self.bank_buttons.items():
            b.setChecked(bank == self.bank)
            count = sum(t.selected for t in self.project.tracks if t.bank == bank)
            b.setToolTip(f"Bank {bank} · {count} selected")
        start = BANKS.index(self.bank) * 16 + 1
        self.bank_range.setText(f"Tracks {start:02}–{start + 15:02}")
        for pad, track in zip(self.pads, self.project.bank_tracks(self.bank)):
            pad.set_track(track)
            pad.editable = not self.busy
        s = self.project.settings
        ready = bool(s.setup_verified and s.midi_port and s.audio_device)
        self.export_button.setText(('Record ' + str(n) + (' stem' if n == 1 else ' stems'))
            if n and ready else 'Set up recording…' if n else 'Select tracks to record')
        self.readiness.setText('Recording in progress. Stop is available below.' if self.busy else
            'Choose pads to add tracks to the queue.' if not n else
            'Connections and MPC setup need checking.' if not ready else 'Ready to record the selected tracks.')
        self.queue_heading.setText(f'Stem queue ({n})' if n else 'Stem queue')
        self.export_button.setEnabled(bool(n) and not self.busy)
        for control in [self.new_button, self.open_button, self.save_button, self.setup_button,
                        self.name_edit, self.include, self.seq, self.tail,
                        self.title_edit, self.folder_edit, self.browse_button, *self.selection_buttons]:
            control.setEnabled(not self.busy)
        self.cancel_button.setVisible(self.busy)
        self.progress.setVisible(self.busy)
        self.meter_label.setVisible(self.busy)
        s = self.project.settings
        self.connection_label.setText(
            f"MIDI to MPC: {s.midi_port or 'Choose output'}\nAudio from MPC: {s.audio_device or 'Choose input'}")
        self.connection_label.setToolTip(self.connection_label.text())
        self.format_label.setText(f"{s.sample_rate / 1000:g} kHz · 24-bit stereo WAV")
        seconds = s.frames / s.sample_rate
        self.estimate.setText(f"{n} {'stem' if n == 1 else 'stems'} · {duration(seconds)} each · about {duration(n * (seconds + s.settle_seconds + 1))} total")
        self.queue_list.clear()
        files = plan_filenames(selected, Path(".__unused_filename_preview__"))
        for t in selected:
            status = {'complete': 'Saved', 'error': 'Error', 'recording': 'Recording', 'queued': 'Waiting'}.get(t.status, 'Selected')
            self.queue_list.addItem(f'{t.bank}{t.pad:02}   {files[t.number].name}   {status}')
            self.queue_list.item(self.queue_list.count() - 1).setData(Qt.ItemDataRole.UserRole, t.number)
        if not selected:
            self.queue_list.addItem("Choose pads to build your stem queue.")

    def focus_queued_track(self, item):
        number = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(number, int) or not 1 <= number <= 64:
            return
        track = self.project.tracks[number - 1]
        self.switch_bank(track.bank)
        self.focus_track(number)
        next(pad for pad in self.pads if pad.track.number == number).setFocus()

    def switch_bank(self, bank):
        if not self.busy:
            self.commit_name()
        self.bank = bank
        self.refresh()

    def navigate(self, index, delta):
        next_index = max(0, min(15, index + delta))
        self.pads[next_index].setFocus()

    def focus_track(self, number):
        if self.focused != number and not self.busy:
            self.commit_name()
        self.focused = number
        track = self.project.tracks[number - 1]
        self.editor_label.setText(f"Track {number:02} / {track.bank}{track.pad:02}")
        self.name_edit.setText(track.custom_name)
        self.name_edit.setPlaceholderText(f"Track_{number}")
        self.include.blockSignals(True)
        self.include.setChecked(track.selected)
        self.include.blockSignals(False)
        self.filename_label.setText(f"Saves as {safe_stem(track.name, number)}.wav")

    def toggle_track(self, number):
        if self.busy:
            return
        track = self.project.tracks[number - 1]
        track.selected = not track.selected
        track.status = "idle"
        self.focus_track(number)
        self.persist()
        self.refresh()

    def set_included(self, enabled):
        if self.busy:
            return
        track = self.project.tracks[self.focused - 1]
        track.selected = enabled
        track.status = "idle"
        self.persist()
        self.refresh()

    def rename_track(self, number):
        if not self.busy:
            self.focus_track(number)
            self.name_edit.setFocus()
            self.name_edit.selectAll()

    def commit_name(self):
        if self.busy or not hasattr(self, "name_edit"):
            return
        track = self.project.tracks[self.focused - 1]
        text = self.name_edit.text().strip()
        if text != track.custom_name:
            track.custom_name = text
            track.status = "idle"
            self.persist()
            self.refresh()
        self.filename_label.setText(f"Saves as {safe_stem(track.name, track.number)}.wav")

    def cancel_name(self):
        self.name_edit.setText(self.project.tracks[self.focused - 1].custom_name)

    def select_tracks(self, enabled, bank_only):
        if not self.busy:
            self.project.select(enabled, self.bank if bank_only else None)
            self.focus_track(self.focused)
            self.persist()
            self.refresh()

    def setup(self):
        if self.busy:
            return
        dialog = SetupDialog(self.project.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.project.settings = dialog.settings
            self.load_settings()
            self.persist()
            self.refresh()

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose output folder", self.project.settings.output_directory)
        if path:
            self.folder_edit.setText(path)
            self.update_settings()

    def save_project(self):
        if self.busy:
            return
        self.commit_name()
        self.update_settings()
        path, _ = QFileDialog.getSaveFileName(self, "Save project", str(self.project_path or ROOT / "session.mpcstems.json"), "Stem projects (*.json)")
        if path:
            try:
                self.project.save(Path(path))
                self.project_path = Path(path)
                self.status_label.setText("Project saved.")
            except Exception as exc:
                QMessageBox.warning(self, "Could not save", str(exc))

    def open_project(self):
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", str(ROOT), "Stem projects (*.json)")
        if path:
            try:
                project = Project.load(Path(path))
            except Exception as exc:
                QMessageBox.warning(self, "Could not open project", str(exc))
                return
            self.project = project
            self.project_path = Path(path)
            self.bank, self.focused = "A", 1
            self.load_settings()
            self.refresh()
            self.focus_track(1)
            self.persist()
            self.status_label.setText("Project opened.")

    def new_project(self):
        if self.busy:
            return
        self.commit_name()
        # Preserve the current project before replacing the autosave.
        archive = self.state_path.parent / f"session-backup-{time.time_ns()}.json"
        try:
            self.project.save(archive)
        except OSError as exc:
            QMessageBox.warning(self, "Could not back up session", str(exc))
            return
        settings = copy.deepcopy(self.project.settings)
        self.project = Project(settings=settings)
        self.project_path = None
        self.bank, self.focused = "A", 1
        self.load_settings()
        self.refresh()
        self.focus_track(1)
        self.persist()
        self.status_label.setText("New session. Previous session backed up automatically.")

    def start_export(self):
        if self.busy:
            return
        # Apply delayed single clicks before freezing the job.
        for pad in self.pads:
            if pad.timer.isActive():
                pad.timer.stop()
                self.toggle_track(pad.pending_number)
        self.commit_name()
        self.update_settings()
        s = self.project.settings
        if s.mode == "hardware" and (not s.setup_verified or not s.midi_port or not s.audio_device):
            self.status_label.setText("Choose your MIDI and audio connections and check the MPC setup before exporting.")
            self.setup()
            return
        if not self.project.selected_tracks():
            return
        self.busy = True
        self.last_directory = None
        self.open_output.hide()
        for track in self.project.selected_tracks():
            track.status = "queued"
        self.status_label.setText("Preparing export…")
        self.progress.setValue(0)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Stop recording")
        self.refresh()
        self.worker = Worker(self.project)
        self.worker.event.connect(self.handle_event)
        self.worker.error.connect(self.handle_error)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()

    def handle_event(self, event):
        kind = event["type"]
        if kind == "log":
            self.log(event["message"])
        elif kind == "directory":
            self.last_directory = event["path"]
        elif kind == "status":
            self.project.tracks[event["number"] - 1].status = event["status"]
            self.persist()
            self.refresh()
        elif kind == "progress":
            track = self.project.tracks[event["number"] - 1]
            self.status_label.setText(f"Recording {track.name}  /  {event['pass']} of {event['total']}  /  {event['ratio']:.0%}")
            self.progress.setValue(round(event["overall"] * 1000))
            db = 20 * math.log10(max(event["peak"], 1e-6))
            self.meter_label.setText(f"INPUT  {db:.1f} dBFS")
        elif kind == "finished":
            self.last_directory = event["directory"]
            if event["status"] == "complete":
                self.status_label.setText(f"Export complete · {event['complete']} stems saved.")
                self.progress.setValue(1000)
            elif event["status"] == "cancelled":
                self.status_label.setText(f"Stopped. {event['complete']} completed stems kept; unfinished pass discarded.")
            self.open_output.setVisible(bool(self.last_directory))

    def handle_error(self, message):
        self.status_label.setText(f"Export stopped: {message}")
        self.log(f"ERROR: {message}")
        self.activity.show()

    def worker_finished(self):
        self.busy = False
        for track in self.project.tracks:
            if track.status in {"queued", "recording"}:
                track.status = "idle"
        self.persist()
        self.refresh()
        self.meter_label.setText("INPUT  —")
        self.worker.deleteLater()
        self.worker = None

    def cancel_export(self):
        if self.worker:
            self.worker.exporter.cancel.set()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Stopping…")
            self.status_label.setText("Stopping playback and closing the current recording…")

    def open_recordings(self):
        if self.last_directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_directory))

    def toggle_log(self):
        self.activity.setVisible(not self.activity.isVisible())

    def animate(self):
        for pad in self.pads:
            if pad.track.status == "recording":
                pad.phase += 0.2
                pad.update()

    def closeEvent(self, event):
        if self.busy:
            self.cancel_export()
            self.status_label.setText("Stopping the export. Close the window again once it has stopped.")
            event.ignore()
        else:
            self.commit_name()
            self.update_settings()
            self.persist()
            event.accept()


def main():
    parser = argparse.ArgumentParser(description="MPC1000 JJOS3 Stem Exporter")
    parser.add_argument("--state", type=Path, help="Override the autosave project path")
    args = parser.parse_args()
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('diamondone.MPCStemExporter')
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MPC Stem Exporter")
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    load_fonts()
    app.setStyleSheet(STYLE)
    window = MainWindow(args.state)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
