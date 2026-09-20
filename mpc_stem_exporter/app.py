from __future__ import annotations

import argparse
import copy
import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QRectF, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QKeySequence, QLinearGradient, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSizePolicy, QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

from .exporter import StemExporter
from . import __version__
from .model import BANKS, PAD_ORDER, Project, Track, plan_filenames, safe_stem
from .setup_dialog import SetupDialog

ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
ACCENT = "#efa85d"

STYLE = """
QWidget { background: #191b1e; color: #e6e3dd; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow { background: #191b1e; }
QLabel { background: transparent; }
QLabel#eyebrow { color: #999b9e; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QLabel#muted { color: #93979d; font-size: 12px; }
QLabel#title { font-size: 27px; font-weight: 800; letter-spacing: 1px; }
QLabel#badge { color: #efa85d; background: #332b23; border: 1px solid #5b4631; border-radius: 4px; padding: 6px 10px; font-size: 10px; font-weight: 700; }
QFrame#card { background: #222529; border: 1px solid #34373b; border-radius: 9px; }
QPushButton { background: #2b2e33; border: 1px solid #41444a; border-radius: 5px; padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #373b40; border-color: #64676c; }
QPushButton:focus { border: 1px solid #efa85d; }
QPushButton:disabled { color: #676b70; background: #24262a; border-color: #35383c; }
QPushButton#primary { background: #efa85d; color: #211a14; border: 1px solid #ffc17d; font-size: 14px; font-weight: 800; padding: 17px; }
QPushButton#primary:hover { background: #ffc17d; }
QPushButton#primary:disabled { background: #514332; color: #99846a; border-color: #514332; }
QPushButton#bank { padding: 10px 18px; font-size: 14px; font-weight: 800; }
QPushButton#bank:checked { background: #efa85d; color: #211a14; border-color: #ffc17d; }
QPushButton#quiet { border-color: transparent; background: transparent; color: #b3b5b8; padding: 7px 9px; }
QPushButton#quiet:hover { color: #efa85d; background: #2a2c30; }
QPushButton#cancel { color: #f19987; border-color: #81574d; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #17191c; border: 1px solid #44474c; border-radius: 4px; padding: 7px; selection-background-color: #87643d; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #efa85d; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #74777c; }
QComboBox QAbstractItemView { background: #25282c; color: #e6e3dd; selection-background-color: #554331; }
QListWidget, QPlainTextEdit, QTextBrowser { background: #191c1f; border: 1px solid #34373b; border-radius: 5px; padding: 7px; }
QListWidget::item { padding: 5px 3px; color: #c2c5c8; }
QProgressBar { background: #131518; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: #efa85d; border-radius: 3px; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #65676b; border-radius: 3px; background: #1a1c1f; }
QCheckBox::indicator:checked { background: #efa85d; border-color: #ffc17d; image: none; }
QToolTip { color: #eee; background: #31353a; border: 1px solid #6a6d70; padding: 5px; }
QScrollBar:vertical { width: 8px; background: #1b1d20; }
QScrollBar::handle:vertical { background: #53575c; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


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
        self.setMinimumSize(112, 100)
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
        r = QRectF(self.rect()).adjusted(2, 2, -2, -5)
        t = self.track
        top, bottom, edge = "#383b40", "#2c2f34", "#494c52"
        marker = "#62666c"
        if t.selected:
            top, bottom, edge, marker = "#514536", "#3d352c", "#987345", ACCENT
        if t.status == "complete":
            top, bottom, edge, marker = "#303f39", "#29352f", "#608770", "#9ec7ad"
        elif t.status == "error":
            top, bottom, edge, marker = "#4b3431", "#3a2a28", "#a76c62", "#f19987"
        elif t.status == "recording":
            top, bottom, edge, marker = "#74502d", "#51402b", "#efb76f", "#ffcc80"
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#101214"))
        p.drawRoundedRect(r.translated(0, 4), 7, 7)
        gradient = QLinearGradient(r.topLeft(), r.bottomLeft())
        gradient.setColorAt(0, QColor(top))
        gradient.setColorAt(1, QColor(bottom))
        p.setBrush(gradient)
        p.setPen(QPen(QColor(edge), 1))
        p.drawRoundedRect(r, 7, 7)
        if self.hasFocus():
            p.setPen(QPen(QColor("#f9d8a9"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(3, 3, -3, -3), 5, 5)
        p.setPen(QColor("#ede5d9") if t.selected else QColor("#b5b7ba"))
        p.setFont(QFont("Segoe UI", 19, QFont.Weight.DemiBold))
        p.drawText(r.adjusted(14, 9, -12, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, f"{t.number:02}")
        p.setFont(QFont("Consolas", 9))
        p.setPen(QColor(marker))
        p.drawText(r.adjusted(0, 13, -13, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, f"{t.bank}{t.pad:02}")
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        p.setPen(QColor("#e6ded1") if t.selected else QColor("#969a9f"))
        text = p.fontMetrics().elidedText(t.name, Qt.TextElideMode.ElideRight, int(r.width() - 28))
        p.drawText(r.adjusted(14, 48, -10, 0), Qt.AlignmentFlag.AlignTop, text)
        status = {"recording": "●  RECORDING", "complete": "✓  EXPORTED", "error": "!  ERROR", "queued": "·  WAITING"}.get(t.status, "●  SELECTED" if t.selected else "—")
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        color = QColor(marker)
        if t.status == "recording":
            color.setAlphaF(0.6 + 0.4 * abs(math.sin(self.phase)))
        p.setPen(color)
        p.drawText(r.adjusted(14, 0, -10, -11), Qt.AlignmentFlag.AlignBottom, status)
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
        self.state_path = state_path or ROOT / "user-data" / "last-project.json"
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
        self.resize(1130, 900)
        self.setMinimumSize(1000, 880)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 24, 28, 18)
        layout.setSpacing(19)

        header = QHBoxLayout()
        branding = QVBoxLayout()
        branding.setSpacing(2)
        branding.addWidget(label("MPC  /  STEM EXPORTER", "title"))
        branding.addWidget(label("MPC1000 · JJOS3", "muted"))
        header.addLayout(branding)
        header.addStretch()
        self.new_button = button("New", self.new_project, "quiet")
        self.open_button = button("Open", self.open_project, "quiet")
        self.save_button = button("Save project", self.save_project)
        header.addWidget(self.new_button)
        header.addWidget(self.open_button)
        header.addWidget(self.save_button)
        layout.addLayout(header)

        connection = QFrame()
        connection.setObjectName("card")
        row = QHBoxLayout(connection)
        row.setContentsMargins(16, 11, 12, 11)
        self.connection_label = label("", "muted")
        row.addWidget(self.connection_label, 1)
        self.setup_button = button("MPC && audio setup", self.setup)
        row.addWidget(self.setup_button)
        layout.addWidget(connection)

        body = QHBoxLayout()
        body.setSpacing(25)
        pads_column = QVBoxLayout()
        pads_column.setSpacing(12)
        bankrow = QHBoxLayout()
        bankrow.addWidget(label("PAD BANK", "eyebrow"))
        bankrow.addSpacing(10)
        self.bank_buttons = {}
        for bank in BANKS:
            b = button(bank, lambda checked=False, bank=bank: self.switch_bank(bank), "bank")
            b.setCheckable(True)
            b.setAccessibleName(f"Bank {bank}")
            bankrow.addWidget(b)
            self.bank_buttons[bank] = b
        bankrow.addStretch()
        self.bank_range = label("TRACKS 01—16", "eyebrow")
        bankrow.addWidget(self.bank_range)
        pads_column.addLayout(bankrow)
        grid = QGridLayout()
        grid.setSpacing(11)
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
        for text, enabled, bank_only in (("Select all", True, False), ("Clear all", False, False),
                                          ("Select bank", True, True), ("Clear bank", False, True)):
            b = button(text, lambda checked=False, on=enabled, local=bank_only: self.select_tracks(on, local), "quiet")
            self.selection_buttons.append(b)
            actions.addWidget(b)
        actions.addStretch()
        pads_column.addLayout(actions)

        editor = QFrame()
        editor.setObjectName("card")
        el = QVBoxLayout(editor)
        el.setContentsMargins(15, 12, 15, 12)
        er = QHBoxLayout()
        self.editor_label = label("TRACK 01", "eyebrow")
        er.addWidget(self.editor_label)
        er.addStretch()
        self.include = QCheckBox("Export this track")
        self.include.toggled.connect(self.set_included)
        er.addWidget(self.include)
        el.addLayout(er)
        self.name_edit = NameEdit()
        self.name_edit.setPlaceholderText("Name this track…")
        self.name_edit.setAccessibleName("Track name")
        self.name_edit.setToolTip("Enter to confirm · Esc to cancel · F2 to rename a focused pad")
        self.name_edit.returnPressed.connect(self.commit_name)
        self.name_edit.cancelled.connect(self.cancel_name)
        self.name_edit.editingFinished.connect(self.commit_name)
        el.addWidget(self.name_edit)
        self.filename_label = label("", "muted")
        el.addWidget(self.filename_label)
        pads_column.addWidget(editor)
        body.addLayout(pads_column, 7)

        sidebar = QFrame()
        sidebar.setObjectName("card")
        sidebar.setMinimumWidth(300)
        sidebar.setMaximumWidth(340)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(19, 20, 19, 18)
        side.setSpacing(12)
        side.addWidget(label("CAPTURE SESSION", "eyebrow"))
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
        side.addWidget(label("OUTPUT FOLDER", "eyebrow"))
        folderrow = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setAccessibleName("Output directory")
        self.folder_edit.editingFinished.connect(self.update_settings)
        self.browse_button = button("…", self.browse_folder)
        self.browse_button.setAccessibleName("Choose output folder")
        self.browse_button.setMaximumWidth(38)
        folderrow.addWidget(self.folder_edit)
        folderrow.addWidget(self.browse_button)
        side.addLayout(folderrow)
        side.addWidget(label("STEM QUEUE", "eyebrow"))
        self.queue_list = QListWidget()
        self.queue_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.queue_list.setMinimumHeight(110)
        side.addWidget(self.queue_list, 1)
        self.estimate = label("", "muted")
        side.addWidget(self.estimate)
        self.export_button = button("EXPORT STEMS", self.start_export, "primary")
        side.addWidget(self.export_button)
        self.cancel_button = button("Stop export", self.cancel_export, "cancel")
        self.cancel_button.hide()
        side.addWidget(self.cancel_button)
        self.open_output = button("Open exported stems  ↗", self.open_recordings, "quiet")
        self.open_output.hide()
        side.addWidget(self.open_output)
        body.addWidget(sidebar, 3)
        layout.addLayout(body, 1)

        footer = QVBoxLayout()
        footer.setSpacing(7)
        statusrow = QHBoxLayout()
        self.status_label = label("Select tracks to export.")
        self.status_label.setWordWrap(True)
        statusrow.addWidget(self.status_label, 1)
        self.meter_label = label("INPUT  —", "eyebrow")
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
        self.bank_range.setText(f"TRACKS {start:02}—{start + 15:02}")
        for pad, track in zip(self.pads, self.project.bank_tracks(self.bank)):
            pad.set_track(track)
            pad.editable = not self.busy
        self.export_button.setText(f"EXPORT {n} {'STEM' if n == 1 else 'STEMS'}" if n else "EXPORT STEMS")
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
            f"MIDI  {s.midi_port or 'Choose output'}   /   AUDIO  {s.audio_device or 'Choose input'}")
        self.connection_label.setToolTip(self.connection_label.text())
        self.format_label.setText(f"{s.sample_rate / 1000:g} kHz · 24-bit stereo WAV")
        seconds = s.frames / s.sample_rate
        self.estimate.setText(f"{n:02} stems  ·  {duration(seconds)} each  ·  ~{duration(n * (seconds + s.settle_seconds + 1))} total")
        self.queue_list.clear()
        files = plan_filenames(selected, Path(".__unused_filename_preview__"))
        for t in selected:
            mark = {"complete": "✓", "error": "!", "recording": "●"}.get(t.status, "·")
            self.queue_list.addItem(f"{t.number:02}   {files[t.number].name}   {mark}")
        if not selected:
            self.queue_list.addItem("Choose pads to build your stem queue.")

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
        self.editor_label.setText(f"TRACK {number:02}  /  {track.bank}{track.pad:02}")
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
        self.cancel_button.setText("Stop export")
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
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MPC Stem Exporter")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow(args.state)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
