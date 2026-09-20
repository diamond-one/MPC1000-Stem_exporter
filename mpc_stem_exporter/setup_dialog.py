from __future__ import annotations

import copy
import math
import threading
import time

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from .controller import Cancelled, MPCController, pause
from .recorder import InputMonitor, audio_devices


def text_label(text, role=None):
    widget = QLabel(text)
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(True)
    return widget


class MidiTestWorker(QThread):
    message = Signal(str)
    failed = Signal(str)

    def __init__(self, settings, action, controller_factory=MPCController):
        super().__init__()
        self.settings, self.action = copy.deepcopy(settings), action
        self.controller_factory = controller_factory
        self.cancel = threading.Event()
        self.stop_requested = action == "stop"

    def request_stop(self):
        self.stop_requested = True
        self.cancel.set()

    def run(self):
        controller = self.controller_factory(self.settings, self.message.emit)
        connected = False
        error = None
        try:
            controller.connect()
            connected = True
            if self.cancel.is_set():
                raise Cancelled()
            if self.action == "banks":
                controller.test_routing(self.cancel)
            elif self.action == "play":
                controller.play_from_start()
                pause(2, self.cancel)
        except Cancelled:
            pass
        except Exception as exc:
            error = exc
        finally:
            if connected and (self.action == "play" or self.stop_requested):
                try:
                    controller.stop()
                except Exception as exc:
                    error = error or exc
            try:
                controller.close()
            except Exception as exc:
                error = error or exc
        if error:
            self.failed.emit(f"MIDI test failed: {error}")
        elif self.cancel.is_set():
            self.message.emit("Test stopped. STOP sent to the selected MIDI output.")
        elif self.action == "banks":
            self.message.emit("Messages sent. Check that the MPC showed MAIN and cycled A → B → C → D → A. "
                              "A successful send alone does not confirm the MPC received it.")
        elif self.action == "play":
            self.message.emit("PLAY START and STOP sent. Check that the MPC played from the beginning for about 2 seconds.")
        else:
            self.message.emit("STOP sent. Check that playback stopped on the MPC.")


class SetupDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = copy.deepcopy(settings)
        self.monitor = None
        self.midi_worker = None
        self.pending_result = None
        self.levels = [0.0, 0.0]
        self.last_signal = [0.0, 0.0]
        self.setWindowTitle("MPC & audio setup — routing tests")
        self.setMinimumWidth(710)
        self.resize(760, 825)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = text_label("MPC & AUDIO SETUP", "title")
        layout.addWidget(title)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        connections = QWidget()
        self.tabs.addTab(connections, "Connections && tests")
        connect_layout = QVBoxLayout(connections)
        connect_layout.setSpacing(12)
        source_row = QHBoxLayout()
        source_row.addWidget(text_label("Source"))
        self.mode = QComboBox()
        self.mode.addItems(["Demo — synthetic audio, no hardware", "Hardware — MPC1000 / JJOS3"])
        self.mode.setCurrentIndex(0 if settings.mode == "demo" else 1)
        source_row.addWidget(self.mode, 1)
        self.refresh_button = QPushButton("Refresh devices")
        self.refresh_button.clicked.connect(self.refresh_devices)
        source_row.addWidget(self.refresh_button)
        connect_layout.addLayout(source_row)

        midi_box = QGroupBox("1  ·  MIDI to the MPC")
        ml = QVBoxLayout(midi_box)
        mf = QFormLayout()
        self.midi = QComboBox()
        self.midi.setAccessibleName("MIDI output")
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        self.channel.setValue(settings.midi_channel)
        mf.addRow("MIDI output", self.midi)
        mf.addRow("Receive channel on MPC", self.channel)
        ml.addLayout(mf)
        mr = QHBoxLayout()
        self.bank_test = QPushButton("Test MIDI · bank LEDs")
        self.play_test = QPushButton("Play test · 2 seconds")
        self.stop_test = QPushButton("STOP MPC")
        self.stop_test.setObjectName("cancel")
        for b, action in ((self.bank_test, "banks"), (self.play_test, "play"), (self.stop_test, "stop")):
            b.clicked.connect(lambda checked=False, action=action: self.test_midi(action))
            mr.addWidget(b)
        ml.addLayout(mr)
        self.midi_status = text_label("Watch the MPC bank LEDs during Test MIDI. Play test starts the current sequence, then stops it.", "muted")
        self.midi_status.setMinimumHeight(45)
        ml.addWidget(self.midi_status)
        connect_layout.addWidget(midi_box)

        audio_box = QGroupBox("2  ·  Audio from the MPC")
        al = QVBoxLayout(audio_box)
        af = QFormLayout()
        self.audio = QComboBox()
        self.audio.setAccessibleName("Audio input")
        af.addRow("Audio input / driver", self.audio)
        pair = QHBoxLayout()
        self.left, self.right = QSpinBox(), QSpinBox()
        for widget, value in ((self.left, settings.input_left), (self.right, settings.input_right)):
            widget.setRange(1, 64)
            widget.setValue(value)
        self.left.setAccessibleName("Left audio input channel")
        self.right.setAccessibleName("Right audio input channel")
        pair.addWidget(text_label("L"))
        pair.addWidget(self.left)
        pair.addWidget(text_label("R"))
        pair.addWidget(self.right)
        self.rate = QComboBox()
        for rate in (44100, 48000, 88200, 96000):
            self.rate.addItem(f"{rate / 1000:g} kHz", rate)
        self.rate.setCurrentIndex(self.rate.findData(settings.sample_rate))
        pair.addWidget(self.rate)
        af.addRow("Interface channels / rate", pair)
        al.addLayout(af)
        self.meters, self.level_labels = [], []
        for side in ("L", "R"):
            row = QHBoxLayout()
            row.addWidget(text_label(side))
            meter = QProgressBar()
            meter.setObjectName("inputMeter")
            meter.setAccessibleName(f"{side} audio input level")
            meter.setRange(0, 600)
            meter.setValue(0)
            meter.setTextVisible(False)
            meter.setStyleSheet("QProgressBar { min-height: 16px; max-height: 16px; } QProgressBar::chunk { background: #91bb9c; }")
            row.addWidget(meter, 1)
            value = text_label("−∞ dBFS")
            value.setMinimumWidth(104)
            row.addWidget(value)
            al.addLayout(row)
            self.meters.append(meter)
            self.level_labels.append(value)
        self.audio_status = text_label("Start the input meter, then tap a pad or play the MPC. L and R show the selected inputs.", "muted")
        self.audio_status.setMinimumHeight(40)
        al.addWidget(self.audio_status)
        self.timing_status = text_label("The meter listens only; it does not save audio or send sound to your speakers.", "muted")
        al.addWidget(self.timing_status)
        ar = QHBoxLayout()
        self.meter_button = QPushButton("Start input meter")
        self.meter_button.clicked.connect(self.toggle_monitor)
        self.reset_button = QPushButton("Reset clip indicators")
        self.reset_button.clicked.connect(self.reset_meter)
        ar.addWidget(self.meter_button)
        ar.addWidget(self.reset_button)
        al.addLayout(ar)
        connect_layout.addWidget(audio_box)
        self.device_note = text_label("", "muted")
        connect_layout.addWidget(self.device_note)
        connect_layout.addStretch()

        advanced = QWidget()
        self.tabs.addTab(advanced, "Timing && MPC setup")
        adv = QVBoxLayout(advanced)
        form = QFormLayout()
        self.preroll, self.settle = QDoubleSpinBox(), QDoubleSpinBox()
        for widget, lo, hi, value in ((self.preroll, 0.1, 5, settings.preroll_seconds), (self.settle, 0, 60, settings.settle_seconds)):
            widget.setRange(lo, hi)
            widget.setDecimals(2)
            widget.setSuffix(" s")
            widget.setValue(value)
        form.addRow("Shared pre-roll", self.preroll)
        form.addRow("Settle between passes", self.settle)
        self.gap = QSpinBox()
        self.gap.setRange(20, 1000)
        self.gap.setSuffix(" ms")
        self.gap.setValue(settings.command_gap_ms)
        form.addRow("MIDI button spacing", self.gap)
        adv.addLayout(form)
        guide = QTextBrowser()
        guide.setOpenExternalLinks(True)
        guide.setHtml("""<h3>JJ's official OS3 controller preset</h3>
        <p>Back up your MPC system setup. Download JJ's controller package and load
        <b>MPC1000/OS3/MPC1K_SETUPS.SYS</b> unchanged on the MPC. Enable MIDI/SYNC → BUTN input
        on the connected port and match the receive channel in Connections.</p>
        <p><b>Test MIDI:</b> MAIN, then bank A → B → C → D → A. Watch the screen and bank LEDs.
        If nothing changes, check MIDI OUT → MPC MIDI IN, the port, channel and BUTN assignments.</p>
        <p><b>Test audio:</b> connect MAIN OUT L/R to interface line inputs. Start the meter,
        tap pads or play the MPC, and watch both channels. Red CLIP indicates the input hit
        full scale. Adjust gain on the interface. The meter creates no recording.</p>
        <p>Before export: <b>Loop OFF · Solo OFF · Mute groups OFF · Use events OFF</b>.
        Disable the playback metronome and exit record standby. Verify PLAY START and enter
        the exact sequence duration and enough effect tail.</p>
        <p><b>Full songs:</b> convert the song to a sequence on the MPC first
        (<b>MODE → SONG → F4</b>), then export that sequence.</p>
        <p><b>Previously muted tracks:</b> this app actively mutes and unmutes tracks.
        A track muted on the MPC can still be exported if it is selected here.
        Deselect tracks you do not want to export. If their MIDI data must be removed
        from the sequence, delete it from a backed-up working copy before exporting.</p>
        <h3>Audio timing</h3><p>The recorder uses driver timestamps when available. If they are
        missing or do not progress, it estimates timing from the input sample count and
        reported latency. Every WAV keeps the requested frame count and pre-roll, but musical
        alignment remains approximate. The capture mode is logged and saved in the manifest.</p>
        <p>The meter stops when you change the audio route or close Setup, releasing the input
        for export. Previous MPC mute states cannot be read or restored.</p>
        <p><a style='color:#efa85d' href='https://www7a.biglobe.ne.jp/~mpc1000/mpc-ctl/index.htm'>Official JJ setup & mappings</a></p>""")
        adv.addWidget(guide, 1)
        self.verified = QCheckBox("I checked the MPC setup, routing, and sequence behavior.")
        self.verified.setChecked(settings.setup_verified)
        layout.addWidget(self.verified)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(50)
        self.meter_timer.timeout.connect(self.poll_monitor)
        self.refresh_devices()
        self.mode.currentIndexChanged.connect(self.mode_changed)
        for control in (self.audio, self.rate):
            control.currentIndexChanged.connect(self.audio_changed)
        for control in (self.left, self.right):
            control.valueChanged.connect(self.audio_changed)
        self.update_enabled()

    def refresh_devices(self):
        self.stop_monitor()
        old_midi = self.midi.currentData() or self.settings.midi_port
        old_audio = self.audio.currentData() or {"name": self.settings.audio_device, "hostapi": self.settings.audio_hostapi}
        errors = []
        self.midi.blockSignals(True)
        self.audio.blockSignals(True)
        self.midi.clear()
        self.midi.addItem("Choose a MIDI output", "")
        self.audio.clear()
        self.audio.addItem("Choose a stereo audio input", None)
        try:
            import mido
            mido.set_backend("mido.backends.rtmidi")
            for name in mido.get_output_names():
                self.midi.addItem(name, name)
        except Exception as exc:
            errors.append(f"MIDI scan: {exc}")
        try:
            for device in audio_devices():
                self.audio.addItem(f"{device['name']} · {device['hostapi']} ({device['channels']} inputs)", device)
        except Exception as exc:
            errors.append(f"Audio scan: {exc}")
        self.midi.setCurrentIndex(max(0, self.midi.findData(old_midi)))
        for i in range(1, self.audio.count()):
            d = self.audio.itemData(i)
            if d['name'] == old_audio['name'] and d['hostapi'] == old_audio['hostapi']:
                self.audio.setCurrentIndex(i)
        self.midi.blockSignals(False)
        self.audio.blockSignals(False)
        self.device_note.setText("\n".join(errors))

    def current_settings(self, validate=True):
        s = copy.deepcopy(self.settings)
        s.mode = "demo" if self.mode.currentIndex() == 0 else "hardware"
        s.midi_port = self.midi.currentData() or ""
        d = self.audio.currentData() or {}
        s.audio_device, s.audio_hostapi = d.get("name", ""), d.get("hostapi", "")
        s.midi_channel = self.channel.value()
        s.input_left, s.input_right = self.left.value(), self.right.value()
        s.sample_rate = self.rate.currentData()
        s.preroll_seconds, s.settle_seconds = self.preroll.value(), self.settle.value()
        s.command_gap_ms, s.setup_verified = self.gap.value(), self.verified.isChecked()
        if validate:
            s.validate()
        return s

    def update_enabled(self):
        hardware = self.mode.currentIndex() == 1
        testing = self.midi_worker is not None
        for widget in (self.midi, self.channel, self.bank_test, self.play_test, self.gap):
            widget.setEnabled(hardware and not testing)
        for widget in (self.audio, self.rate, self.left, self.right, self.verified, self.settle, self.meter_button):
            widget.setEnabled(hardware)
        self.stop_test.setEnabled(hardware)
        self.refresh_button.setEnabled(not testing)
        self.mode.setEnabled(not testing)
        self.reset_button.setEnabled(self.monitor is not None)

    def mode_changed(self):
        self.stop_monitor()
        self.update_enabled()

    def audio_changed(self):
        if self.monitor:
            self.stop_monitor()
            self.audio_status.setText("Input changed. Start the meter to test the new route.")

    def test_midi(self, action):
        if self.midi_worker:
            if action == "stop":
                self.midi_worker.request_stop()
            return
        try:
            s = self.current_settings(validate=False)
            if s.mode != "hardware" or not s.midi_port:
                raise ValueError("Choose Hardware mode and a MIDI output first.")
        except ValueError as exc:
            self.midi_status.setText(str(exc))
            return
        self.midi_status.setText("Sending MIDI test… Watch the MPC.")
        self.midi_worker = MidiTestWorker(s, action)
        self.midi_worker.message.connect(self.midi_status.setText)
        self.midi_worker.failed.connect(self.midi_status.setText)
        self.midi_worker.finished.connect(self.midi_finished)
        self.update_enabled()
        self.midi_worker.start()

    def midi_finished(self):
        self.midi_worker.deleteLater()
        self.midi_worker = None
        self.update_enabled()
        if self.pending_result is not None:
            result, self.pending_result = self.pending_result, None
            super().done(result)

    def toggle_monitor(self):
        if self.monitor:
            self.stop_monitor()
            return
        try:
            s = self.current_settings()
            self.monitor = InputMonitor(s)
            self.reset_meter()
            self.monitor.start()
            self.meter_button.setText("Stop input meter")
            self.audio_status.setText("Listening… Tap a pad or play the MPC.")
            self.meter_timer.start()
            self.update_enabled()
        except Exception as exc:
            self.stop_monitor()
            self.audio_status.setText(f"Could not start input meter: {exc}")

    def reset_meter(self):
        if self.monitor:
            self.monitor.reset_peaks()
        self.levels = [0.0, 0.0]
        self.last_signal = [0.0, 0.0]
        for meter, value in zip(self.meters, self.level_labels):
            meter.setValue(0)
            value.setText("−∞ dBFS")
            value.setStyleSheet("")

    def poll_monitor(self):
        if not self.monitor:
            return
        try:
            data = self.monitor.poll()
        except Exception as exc:
            self.stop_monitor()
            self.audio_status.setText(f"Input meter stopped: {exc}")
            return
        now = time.monotonic()
        for i, peak in enumerate(data['peaks']):
            self.levels[i] = max(float(peak), self.levels[i] * 0.75)
            db = 20 * math.log10(max(self.levels[i], 1e-6))
            self.meters[i].setValue(round(max(0, min(600, (db + 60) * 10))))
            if peak >= 0.001:
                self.last_signal[i] = now
            self.level_labels[i].setText("CLIP" if data['clipped'][i] else (f"{db:.1f} dBFS" if db > -90 else "−∞ dBFS"))
            self.level_labels[i].setStyleSheet("color: #ff8f7b; font-weight: bold;" if data['clipped'][i] else "")
        sides = [side for i, side in enumerate(('L', 'R')) if now - self.last_signal[i] < 0.8]
        if any(data['clipped']):
            self.audio_status.setText("Input clipped. Lower the audio-interface gain, then reset the clip indicators.")
        elif sides:
            self.audio_status.setText(f"Input signal detected: {' + '.join(sides)}. Both bars should respond to stereo audio.")
        else:
            self.audio_status.setText("Input open; no signal above −60 dBFS. Tap a pad or play the MPC, then check cables and input gain.")
        self.timing_status.setText("Driver timing available. No audio is being saved." if data['driver_timing'] else
                                  "Driver timing unavailable/checking. Export can use approximate sample-clock timing.")

    def stop_monitor(self):
        self.meter_timer.stop() if hasattr(self, 'meter_timer') else None
        monitor, self.monitor = self.monitor, None
        if monitor:
            try:
                monitor.stop()
            except Exception as exc:
                self.audio_status.setText(f"Input close: {exc}")
            else:
                self.audio_status.setText("Input meter stopped.")
        if hasattr(self, 'meter_button'):
            self.meter_button.setText("Start input meter")
            self.reset_button.setEnabled(False)

    def accept(self):
        try:
            self.settings = self.current_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Check setup", str(exc))
            return
        self.done(QDialog.DialogCode.Accepted)

    def done(self, result):
        self.stop_monitor()
        if self.midi_worker:
            self.pending_result = result
            self.midi_worker.request_stop()
            self.midi_status.setText("Stopping the MIDI test before closing…")
            return
        super().done(result)
