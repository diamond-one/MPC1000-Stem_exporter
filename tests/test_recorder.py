"""Exercise the actual timestamped recorder with a deterministic fake audio driver."""
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
import sounddevice as sd
import soundfile as sf

from mpc_stem_exporter.model import Settings
from mpc_stem_exporter.recorder import AudioRecorder, resolve_device


class FakeInput:
    overflow = False
    bad_timestamp = False

    def __init__(self, callback, samplerate, **kwargs):
        self.callback, self.rate = callback, samplerate
        self.active = False
        self.position = 0
        self.stop_event = threading.Event()

    @property
    def time(self):
        return 100 + self.position / self.rate

    def __enter__(self):
        self.active = True
        def produce():
            while not self.stop_event.is_set():
                index = np.arange(1024) + self.position
                ramp = (index % 10000) / 20000
                data = np.column_stack((ramp, np.full(1024, 0.1), np.full(1024, -0.25))).astype("float32")
                timing = SimpleNamespace(inputBufferAdcTime=0 if self.bad_timestamp else self.time)
                status = "input overflow" if self.overflow and self.position >= 32000 else None
                try:
                    self.callback(data, 1024, timing, status)
                except sd.CallbackAbort:
                    self.active = False
                    return
                self.position += 1024
                self.stop_event.wait(0.001)
        self.thread = threading.Thread(target=produce)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=2)
        self.active = False


class Controller:
    played = False
    stopped = False

    def play_from_start(self): self.played = True
    def stop(self): self.stopped = True


def settings():
    return Settings(mode="hardware", input_left=3, input_right=1,
                    sequence_seconds=0.1, tail_seconds=0.1, preroll_seconds=0.25)


def test_timestamp_reference_channel_selection_and_exact_window(monkeypatch, tmp_path):
    monkeypatch.setattr(sd, "InputStream", FakeInput)
    s = settings()
    recorder = AudioRecorder(s)
    recorder.device = 0
    c = Controller()
    output = tmp_path / "test.wav"
    result = recorder.record(output, c, threading.Event(), lambda *args: None, 1)
    data, rate = sf.read(output)
    assert len(data) == s.frames
    assert data.shape[1] == 2 and rate == s.sample_rate
    assert np.allclose(data[:, 0], -0.25, atol=1e-6)
    origin = round((result["midi_reference_audio_clock"] - 100 - s.preroll_seconds) * rate)
    expected = ((np.arange(len(data)) + origin) % 10000) / 20000
    assert np.allclose(data[:, 1], expected, atol=1e-6)
    assert c.played and c.stopped
    assert not list(tmp_path.glob(".capture-*"))


@pytest.mark.parametrize("fault", ["overflow"])
def test_capture_fault_never_produces_a_finished_stem(monkeypatch, tmp_path, fault):
    class BrokenInput(FakeInput): pass
    setattr(BrokenInput, fault, True)
    monkeypatch.setattr(sd, "InputStream", BrokenInput)
    recorder = AudioRecorder(settings())
    recorder.device = 0
    with pytest.raises(RuntimeError):
        recorder.record(tmp_path / "bad.wav", Controller(), threading.Event(), lambda *args: None, 1)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("timestamp", [0.0, float("nan")])
def test_missing_timestamps_use_fallback_and_still_write_complete_wav(monkeypatch, tmp_path, timestamp):
    class UntimedInput(FakeInput):
        bad_timestamp = True
        latency = 0.05
    if np.isnan(timestamp):
        original_enter = UntimedInput.__enter__
        def enter(self):
            cb = self.callback
            self.callback = lambda data, frames, timing, status: cb(data, frames, SimpleNamespace(inputBufferAdcTime=timestamp), status)
            return original_enter(self)
        UntimedInput.__enter__ = enter
    monkeypatch.setattr(sd, "InputStream", UntimedInput)
    messages = []
    recorder = AudioRecorder(settings(), messages.append)
    recorder.device = 0
    path = tmp_path / 'fallback.wav'
    result = recorder.record(path, Controller(), threading.Event(), lambda *args: None, 1)
    assert result['timing_source'] == 'sample_clock_estimate'
    assert result['midi_reference_audio_clock'] is None
    assert sf.info(path).frames == settings().frames
    assert sf.info(path).channels == 2
    assert any('timestamps unavailable' in s for s in messages)


def test_timestamp_epoch_zero_is_valid_when_time_progresses(monkeypatch, tmp_path):
    class ZeroEpoch(FakeInput):
        @property
        def time(self): return self.position / self.rate
    monkeypatch.setattr(sd, "InputStream", ZeroEpoch)
    recorder = AudioRecorder(settings())
    recorder.device = 0
    result = recorder.record(tmp_path / 'zero.wav', Controller(), threading.Event(), lambda *args: None, 1)
    assert result['timing_source'] == 'driver_timestamps'


def test_timestamp_discontinuity_mid_pass_keeps_fixed_window(monkeypatch, tmp_path):
    class JumpingInput(FakeInput):
        @property
        def time(self): return 100 + self.position / self.rate + (100 if self.position > 30000 else 0)
    monkeypatch.setattr(sd, "InputStream", JumpingInput)
    recorder = AudioRecorder(settings())
    recorder.device = 0
    path = tmp_path / 'jump.wav'
    result = recorder.record(path, Controller(), threading.Event(), lambda *args: None, 1)
    assert result['driver_timing_degraded']
    assert sf.info(path).frames == settings().frames


def test_device_identity_uses_name_and_host_api(monkeypatch):
    import mpc_stem_exporter.recorder as module
    monkeypatch.setattr(module, "audio_devices", lambda: [
        {"name": "Interface", "hostapi": "MME", "index": 2, "channels": 2},
        {"name": "Interface", "hostapi": "Windows WASAPI", "index": 8, "channels": 8},
    ])
    s = Settings(audio_device="Interface", audio_hostapi="Windows WASAPI", input_left=3, input_right=4)
    assert resolve_device(s) == 8
    s.audio_hostapi = "MME"
    with pytest.raises(ValueError, match="channels"):
        resolve_device(s)
