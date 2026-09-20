import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import sounddevice as sd
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from mpc_stem_exporter.controller import MPCController
from mpc_stem_exporter.model import Settings
from mpc_stem_exporter.recorder import InputMonitor
from mpc_stem_exporter.setup_dialog import MidiTestWorker, SetupDialog


class Port:
    def __init__(self): self.messages = []
    def send(self, message): self.messages.append(message)


def test_midi_routing_test_uses_only_main_and_bank_notes(monkeypatch):
    import mpc_stem_exporter.controller as module
    monkeypatch.setattr(module, 'pause', lambda seconds, cancel: None)
    port = Port()
    controller = MPCController(Settings(midi_channel=12), port=port, sleeper=lambda _: None)
    controller.test_routing(threading.Event())
    assert [m.note for m in port.messages if m.type == 'note_on'] == [77, 89, 90, 91, 92, 89]
    assert all(m.channel == 11 for m in port.messages)
    assert not any(m.type == 'control_change' for m in port.messages)


class MidiController:
    def __init__(self): self.calls = []
    def connect(self): self.calls.append('connect')
    def play_from_start(self): self.calls.append('play')
    def stop(self): self.calls.append('stop')
    def close(self): self.calls.append('close')
    def test_routing(self, cancel): self.calls.append('banks')


def test_midi_play_test_always_stops_and_closes(monkeypatch):
    import mpc_stem_exporter.setup_dialog as module
    monkeypatch.setattr(module, 'pause', lambda seconds, cancel: None)
    c = MidiController()
    worker = MidiTestWorker(Settings(), 'play', lambda *args: c)
    worker.run()
    assert c.calls == ['connect', 'play', 'stop', 'close']


def test_midi_play_failure_attempts_stop():
    c = MidiController()
    def fail(): raise RuntimeError('Cable unplugged')
    c.play_from_start = fail
    errors = []
    worker = MidiTestWorker(Settings(), 'play', lambda *args: c)
    worker.failed.connect(errors.append)
    worker.run()
    assert c.calls[-2:] == ['stop', 'close']
    assert 'Cable unplugged' in errors[0]


def test_cancelled_midi_worker_never_starts_playback():
    c = MidiController()
    worker = MidiTestWorker(Settings(), 'play', lambda *args: c)
    worker.request_stop()
    worker.run()
    assert c.calls == ['connect', 'stop', 'close']


class MeterStream:
    instance = None
    def __init__(self, callback, **kwargs):
        self.callback = callback
        self.active = False
        self.closed = False
        self.kwargs = kwargs
        MeterStream.instance = self
    def start(self): self.active = True
    def abort(self): self.active = False
    def close(self): self.closed = True
    def feed(self, data, status=None):
        self.callback(np.array(data, dtype='float32'), len(data), SimpleNamespace(inputBufferAdcTime=0), status)


@pytest.fixture
def meter_driver(monkeypatch):
    import mpc_stem_exporter.recorder as module
    monkeypatch.setattr(module, 'resolve_device', lambda settings: 9)
    monkeypatch.setattr(sd, 'check_input_settings', lambda **kwargs: None)
    monkeypatch.setattr(sd, 'InputStream', MeterStream)


def test_meter_selected_channels_short_taps_clip_latch_and_cleanup(meter_driver):
    monitor = InputMonitor(Settings(mode='hardware', input_left=3, input_right=1))
    monitor.start()
    stream = MeterStream.instance
    assert stream.kwargs['channels'] == 3 and stream.kwargs['device'] == 9
    stream.feed([[0.2, 0.7, 1.0], [-0.5, 0, 0.1]])
    stream.feed([[0, 0, 0]])  # A short tap must survive until the next UI poll.
    sample = monitor.poll()
    assert np.allclose(sample['peaks'], [1.0, 0.5])
    assert sample['clipped'] == (True, False)
    assert np.allclose(monitor.poll()['peaks'], [0, 0])
    assert monitor.poll()['clipped'][0]
    monitor.reset_peaks()
    assert monitor.poll()['clipped'] == (False, False)
    monitor.stop()
    assert stream.closed and not stream.active


def test_meter_propagates_driver_error(meter_driver):
    monitor = InputMonitor(Settings(mode='hardware'))
    monitor.start()
    with pytest.raises(sd.CallbackAbort):
        MeterStream.instance.feed([[0, 0]], 'input overflow')
    with pytest.raises(RuntimeError, match='overflow'):
        monitor.poll()
    monitor.stop()


def test_monitor_cannot_open_hardware_from_demo(meter_driver):
    monitor = InputMonitor(Settings(mode='demo'))
    with pytest.raises(ValueError, match='Hardware'):
        monitor.start()


@pytest.fixture
def dialog(monkeypatch, meter_driver):
    import mido
    import mpc_stem_exporter.setup_dialog as module
    monkeypatch.setattr(mido, 'set_backend', lambda *args: None)
    monkeypatch.setattr(mido, 'get_output_names', lambda: ['Test MIDI'])
    monkeypatch.setattr(module, 'audio_devices', lambda: [
        {'name':'Test interface','hostapi':'Test driver','channels':4,'index':9}])
    app = QApplication.instance() or QApplication([])
    s = Settings(mode='hardware', midi_port='Test MIDI', audio_device='Test interface', audio_hostapi='Test driver')
    d = SetupDialog(s)
    d.show()
    app.processEvents()
    yield d
    d.reject()
    app.processEvents()


def test_setup_meter_updates_left_and_right_and_stops_on_route_change(dialog):
    dialog.toggle_monitor()
    stream = MeterStream.instance
    stream.feed([[0.5, 0.1]])
    dialog.poll_monitor()
    assert dialog.meters[0].value() > dialog.meters[1].value() > 0
    assert 'L + R' in dialog.audio_status.text()
    dialog.left.setValue(3)
    assert dialog.monitor is None and stream.closed


def test_setup_cancel_and_save_release_audio_input(dialog):
    dialog.toggle_monitor()
    stream = MeterStream.instance
    dialog.reject()
    assert stream.closed and dialog.monitor is None
    dialog.show()
    dialog.toggle_monitor()
    stream = MeterStream.instance
    dialog.rate.setCurrentIndex(0)  # Route change releases stream too.
    assert stream.closed
    dialog.toggle_monitor()
    stream = MeterStream.instance
    dialog.accept()
    assert stream.closed and dialog.settings.sample_rate == 44100


def test_closing_during_midi_test_waits_for_worker_and_stops_playback(dialog, monkeypatch):
    import mpc_stem_exporter.setup_dialog as module
    c = MidiController()
    original = MidiTestWorker
    monkeypatch.setattr(module, 'MidiTestWorker', lambda s, action: original(s, action, lambda *args: c))
    dialog.test_midi('play')
    QTest.qWait(50)
    dialog.reject()
    for _ in range(100):
        if dialog.midi_worker is None:
            break
        QTest.qWait(20)
    assert dialog.midi_worker is None
    assert c.calls[-2:] == ['stop', 'close']
    assert not dialog.isVisible()


def test_midi_can_be_tested_before_audio_is_configured(dialog, monkeypatch):
    import mpc_stem_exporter.setup_dialog as module
    c = MidiController()
    original = MidiTestWorker
    monkeypatch.setattr(module, 'MidiTestWorker', lambda s, action: original(s, action, lambda *args: c))
    dialog.audio.setCurrentIndex(0)
    dialog.left.setValue(2)  # An invalid stereo pair must not prevent the MIDI test.
    dialog.test_midi('banks')
    for _ in range(100):
        if dialog.midi_worker is None:
            break
        QTest.qWait(20)
    assert dialog.midi_worker is None
    assert c.calls == ['connect', 'banks', 'close']
