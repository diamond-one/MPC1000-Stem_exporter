import ctypes
import errno
import json
import os
from pathlib import Path
import threading

import pytest
import soundfile as sf

from mpc_stem_exporter import storage
from mpc_stem_exporter.exporter import StemExporter
from mpc_stem_exporter.model import Project
from mpc_stem_exporter.recorder import DemoRecorder


def test_transient_replace_denial_retries_without_touching_existing_json(monkeypatch, tmp_path):
    path = tmp_path / "export.json"
    path.write_text('{"old": true}')
    real_replace = os.replace
    attempts = []
    def locked_then_free(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            assert json.loads(path.read_text()) == {"old": True}
            raise PermissionError(errno.EACCES, "File is locked")
        real_replace(source, target)
    monkeypatch.setattr(storage.os, "replace", locked_then_free)
    monkeypatch.setattr(storage, "REPLACE_DELAYS", (0, 0))
    storage.atomic_json(path, {"new": True})
    assert len(attempts) == 3
    assert json.loads(path.read_text()) == {"new": True}
    assert not list(tmp_path.glob(".saving-*"))


def test_persistent_denial_keeps_original_and_valid_recovery(monkeypatch, tmp_path):
    path = tmp_path / "export.json"
    path.write_text('{"old": true}')
    def denied(*args):
        raise PermissionError(errno.EACCES, "File is locked")
    monkeypatch.setattr(storage.os, "replace", denied)
    monkeypatch.setattr(storage, "REPLACE_DELAYS", (0,))
    with pytest.raises(storage.JsonSaveError) as caught:
        storage.atomic_json(path, {"new": "complete data"})
    assert json.loads(path.read_text()) == {"old": True}
    assert json.loads(caught.value.recovery_path.read_text()) == {"new": "complete data"}


def test_invalid_json_never_leaves_recovery_copy(tmp_path):
    with pytest.raises(ValueError):
        storage.atomic_json(tmp_path / "export.json", {"value": float("nan")})
    assert not list(tmp_path.iterdir())


def test_manifest_switches_to_revisioned_snapshots(monkeypatch, tmp_path):
    logs = []
    store = storage.ManifestStore(tmp_path, logs.append)
    store.save({"status": "running"})
    replacements = []
    def denied(*args):
        replacements.append(args)
        raise PermissionError(errno.EACCES, "Locked")
    monkeypatch.setattr(storage.os, "replace", denied)
    monkeypatch.setattr(storage, "REPLACE_DELAYS", ())
    first_recovery = store.save({"status": "recording"})
    final_path = store.save({"status": "complete"})
    assert len(replacements) == 1  # No repeated rename attempts after fallback.
    assert json.loads(first_recovery.read_text())["manifest_revision"] == 2
    assert json.loads(final_path.read_text()) == {"status": "complete", "manifest_revision": 3}
    assert final_path.name.startswith("export-recovery-0003-")
    assert any("Continuing" in line for line in logs)


def windows_read_lock(path):
    """Open an actual Windows handle without FILE_SHARE_DELETE, blocking rename."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return lambda: kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_real_windows_lock_is_retried_until_released(tmp_path):
    path = tmp_path / "export.json"
    path.write_text('{"old": true}')
    release = windows_read_lock(path)
    timer = threading.Timer(0.2, release)
    timer.start()
    try:
        storage.atomic_json(path, {"new": True})
    finally:
        timer.join()
    assert json.loads(path.read_text()) == {"new": True}


class Controller:
    def __init__(self): self.calls = []
    def connect(self): self.calls.append("connect")
    def isolate_track(self, n, cancel): self.calls.append(n)
    def play_from_start(self): self.calls.append("play")
    def stop(self): self.calls.append("stop")
    def close(self): self.calls.append("close")


def project(tmp_path):
    p = Project()
    p.settings.mode = "demo"
    p.settings.output_directory = str(tmp_path)
    p.settings.sequence_seconds = 0.1
    p.settings.tail_seconds = 0
    p.tracks[0].selected = p.tracks[16].selected = p.tracks[63].selected = True
    return p


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_entire_export_completes_with_export_json_locked_for_the_whole_job(tmp_path):
    p = project(tmp_path)
    controller = Controller()
    releases = []
    events = []
    def emit(event):
        events.append(event)
        if event["type"] == "directory":
            releases.append(windows_read_lock(Path(event["path"]) / "export.json"))
    job = StemExporter(p, emit, controller, DemoRecorder(p.settings, speed=10000))
    try:
        manifest = job.run()
    finally:
        for release in releases:
            release()
    assert manifest["status"] == "complete"
    assert [n for n in controller.calls if type(n) is int] == [1, 17, 64]
    wavs = list(job.directory.glob("*.wav"))
    assert len(wavs) == 3
    assert all(sf.info(path).frames == p.settings.frames for path in wavs)
    finished = next(e for e in events if e["type"] == "finished")
    assert finished["complete"] == 3
    recovered = json.loads(Path(finished["manifest_path"]).read_text())
    assert recovered["status"] == "complete"
    assert all(t["status"] == "complete" for t in recovered["tracks"])
    assert controller.calls[-2:] == ["stop", "close"]


def test_completed_wav_not_marked_failed_when_all_metadata_writes_fail(monkeypatch, tmp_path):
    p = project(tmp_path)
    controller = Controller()
    events = []
    save = storage.ManifestStore.save
    calls = []
    def out_of_space(self, manifest):
        calls.append(manifest["status"])
        if len(calls) >= 3:
            raise OSError(errno.ENOSPC, "Disk full")
        return save(self, manifest)
    monkeypatch.setattr(storage.ManifestStore, "save", out_of_space)
    job = StemExporter(p, events.append, controller, DemoRecorder(p.settings, speed=10000))
    with pytest.raises(OSError, match="Disk full"):
        job.run()
    assert (job.directory / "Track_1.wav").is_file()
    statuses = [e["status"] for e in events if e["type"] == "status" and e["number"] == 1]
    assert statuses == ["recording", "complete"]
    assert next(e for e in events if e["type"] == "finished")["complete"] == 1
    assert controller.calls[-2:] == ["stop", "close"]


def test_final_metadata_error_does_not_hide_original_audio_error(monkeypatch, tmp_path):
    p = project(tmp_path)
    events = []
    controller = Controller()
    class BrokenRecorder:
        def preflight(self): pass
        def record(self, *args): raise RuntimeError("Audio device disconnected")
    save = storage.ManifestStore.save
    def fail_final(self, manifest):
        if manifest["status"] == "error":
            raise OSError(errno.ENOSPC, "Metadata disk full")
        return save(self, manifest)
    monkeypatch.setattr(storage.ManifestStore, "save", fail_final)
    job = StemExporter(p, events.append, controller, BrokenRecorder())
    with pytest.raises(RuntimeError, match="Audio device disconnected"):
        job.run()
    assert any(e["type"] == "finished" for e in events)
    assert controller.calls[-2:] == ["stop", "close"]
