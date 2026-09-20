import json
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from mpc_stem_exporter.controller import Cancelled, MPCController
from mpc_stem_exporter.exporter import StemExporter
from mpc_stem_exporter.model import PAD_ORDER, Project, Settings, plan_filenames, safe_stem
from mpc_stem_exporter.recorder import DemoRecorder, write_exclusive


def test_bank_views_keep_all_64_object_identities_and_state(tmp_path):
    p = Project()
    identities = [id(t) for t in p.tracks]
    for n in (1, 17, 36, 64):
        t = p.tracks[n - 1]
        t.selected = True
        t.custom_name = f"Stem {n}"
        t.status = "complete"
    for bank in "ABCDACBD":
        view = p.bank_tracks(bank)
        assert [t.pad for t in view] == list(PAD_ORDER)
        assert all(t.bank == bank for t in view)
    assert identities == [id(t) for t in p.tracks]
    assert [t.number for t in p.selected_tracks()] == [1, 17, 36, 64]
    path = tmp_path / "session.json"
    p.save(path)
    loaded = Project.load(path)
    assert loaded.to_dict() == p.to_dict()
    p.select(True)
    assert len(p.selected_tracks()) == 64
    p.select(False, "C")
    assert len(p.selected_tracks()) == 48
    p.select(False)
    assert not p.selected_tracks()


def test_filename_rules_and_collisions(tmp_path):
    p = Project()
    assert p.tracks[35].name == "Track_36"
    tracks = [p.tracks[i] for i in (0, 1, 2, 35, 63)]
    tracks[0].custom_name = "Kick"
    tracks[1].custom_name = "kick"
    tracks[2].custom_name = "CON.wav"
    tracks[4].custom_name = "Snare / top?"
    (tmp_path / "Kick.wav").write_text("Existing audio")
    files = plan_filenames(tracks, tmp_path)
    assert [files[t.number].name for t in tracks] == ["Kick_2.wav", "kick_3.wav", "_CON.wav", "Track_36.wav", "Snare _ top_.wav"]
    assert safe_stem("...", 17) == "Track_17"
    assert safe_stem("LPT1.txt", 1) == "_LPT1.txt"
    assert tracks[4].custom_name == "Snare / top?"


@pytest.mark.parametrize("change", [
    lambda p: p["tracks"].pop(),
    lambda p: p["tracks"][1].update(number=1),
    lambda p: p["tracks"][0].update(number=0),
    lambda p: p["tracks"][0].update(selected="false"),
    lambda p: p["settings"].update(sequence_seconds=float("nan")),
    lambda p: p["settings"].update(sample_rate=3),
    lambda p: p["settings"].update(input_left=2, input_right=2),
])
def test_reject_corrupt_projects(tmp_path, change):
    data = Project().to_dict()
    change(data)
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        Project.load(file)


def test_interrupted_statuses_recover(tmp_path):
    project = Project()
    for t, status in zip(project.tracks, ("queued", "recording", "complete", "error")):
        t.status = status
    path = tmp_path / "project.json"
    project.save(path)
    assert [t.status for t in Project.load(path).tracks[:4]] == ["idle", "idle", "complete", "error"]


class Port:
    def __init__(self):
        self.messages = []
        self.closed = False

    def send(self, message):
        self.messages.append(message)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("number", range(1, 65))
def test_midi_mapping_every_track(number):
    port = Port()
    c = MPCController(Settings(midi_channel=7), port=port, sleeper=lambda _: None)
    c.isolate_track(number, threading.Event())
    c.play_from_start()
    c.stop()
    notes = [m.note for m in port.messages if m.type == "note_on"]
    assert notes == [3, 77, 88, 89 + (number - 1) // 16, 93, 1, 3]
    pads = [m for m in port.messages if m.type == "control_change"]
    assert [(m.control, m.value) for m in pads] == [(16 + (number - 1) % 16, 127), (16 + (number - 1) % 16, 0)]
    assert all(m.channel == 6 for m in port.messages)
    assert len([m for m in port.messages if m.type == "note_off"]) == len(notes)


def test_cancel_before_isolation_sends_no_pad():
    cancel = threading.Event()
    cancel.set()
    port = Port()
    c = MPCController(Settings(), port=port, sleeper=lambda _: None)
    with pytest.raises(Cancelled):
        c.isolate_track(1, cancel)
    assert not port.messages


class Controller:
    def __init__(self):
        self.calls = []

    def connect(self): self.calls.append("connect")
    def isolate_track(self, n, cancel): self.calls.append(n)
    def play_from_start(self): self.calls.append("play")
    def stop(self): self.calls.append("stop")
    def close(self): self.calls.append("close")


def project_for_export(tmp_path):
    p = Project()
    p.settings.output_directory = str(tmp_path)
    p.settings.sequence_seconds = 0.1
    p.settings.tail_seconds = 0.1
    for n in (64, 36, 2):
        p.tracks[n - 1].selected = True
    return p


def test_selected_order_stereo_wavs_and_pre_roll(tmp_path):
    p = project_for_export(tmp_path)
    c = Controller()
    exporter = StemExporter(p, controller=c, recorder=DemoRecorder(p.settings, speed=10000))
    result = exporter.run()
    assert result["status"] == "complete"
    assert [n for n in c.calls if isinstance(n, int)] == [2, 36, 64]
    assert c.calls[-2:] == ["stop", "close"]
    assert len(list(exporter.directory.glob("*.wav"))) == 3
    for path in exporter.directory.glob("*.wav"):
        info = sf.info(path)
        assert (info.frames, info.channels, info.samplerate, info.subtype) == (p.settings.frames, 2, 48000, "PCM_24")
        data, _ = sf.read(path)
        assert np.max(np.abs(data[:round(p.settings.preroll_seconds * 48000)])) == 0
        assert np.max(np.abs(data)) > 0
    assert p.tracks[1].status == "idle"  # Immutable export snapshot


def test_cancel_discards_incomplete_keeps_completed(tmp_path):
    p = project_for_export(tmp_path)
    c = Controller()
    events = []
    exporter = None
    def emit(event):
        events.append(event)
        if event["type"] == "progress" and event["number"] == 36:
            exporter.cancel.set()
    exporter = StemExporter(p, emit=emit, controller=c, recorder=DemoRecorder(p.settings, speed=10000))
    result = exporter.run()
    assert result["status"] == "cancelled"
    assert [p.name for p in exporter.directory.glob("*.wav")] == ["Track_2.wav"]
    assert c.calls[-2:] == ["stop", "close"]
    assert not list(exporter.directory.glob(".capture-*"))
    assert [t["status"] for t in result["tracks"]] == ["complete", "cancelled", "not_recorded"]


def test_recording_failure_stops_and_marks_error(tmp_path):
    p = project_for_export(tmp_path)
    c = Controller()
    class BrokenRecorder:
        def preflight(self): pass
        def record(self, *args): raise RuntimeError("Interface disconnected")
    exporter = StemExporter(p, controller=c, recorder=BrokenRecorder())
    with pytest.raises(RuntimeError, match="disconnected"):
        exporter.run()
    manifest = json.loads((exporter.directory / "export.json").read_text())
    assert manifest["status"] == "error"
    assert manifest["tracks"][0]["status"] == "error"
    assert c.calls[-2:] == ["stop", "close"]


def test_exclusive_wav_writer_never_overwrites(tmp_path):
    path = tmp_path / "Kick.wav"
    path.write_bytes(b"previous audio")
    with pytest.raises(FileExistsError):
        write_exclusive(path, [np.zeros((100, 2))], 48000)
    assert path.read_bytes() == b"previous audio"


def test_repeated_exports_use_separate_folders(tmp_path):
    p = project_for_export(tmp_path)
    p.select(False)
    p.tracks[0].selected = True
    runs = [StemExporter(p, controller=Controller(), recorder=DemoRecorder(p.settings, speed=10000)) for _ in range(2)]
    for job in runs:
        job.run()
    assert runs[0].directory != runs[1].directory


def test_project_frozen_against_edits_after_job_creation(tmp_path):
    p = project_for_export(tmp_path)
    job = StemExporter(p)
    p.select(False)
    p.settings.sequence_seconds = 50
    assert [t.number for t in job.project.selected_tracks()] == [2, 36, 64]
    assert job.project.settings.sequence_seconds == 0.1
