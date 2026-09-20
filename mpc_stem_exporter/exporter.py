from __future__ import annotations

import copy
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from .controller import Cancelled, DemoController, MPCController, pause
from .model import Project, plan_filenames
from .recorder import AudioRecorder, DemoRecorder
from .storage import ManifestStore


class StemExporter:
    def __init__(self, project: Project, emit=lambda _: None, controller=None, recorder=None):
        # Freeze the job. UI bank browsing cannot change a running recording.
        self.project = copy.deepcopy(project)
        self.emit = emit
        self.cancel = threading.Event()
        settings = self.project.settings
        self.controller = controller or (DemoController if settings.mode == "demo" else MPCController)(settings, self.log)
        self.recorder = recorder or (DemoRecorder(settings) if settings.mode == "demo" else AudioRecorder(settings, self.log))
        self.directory = None

    def log(self, message):
        self.emit({"type": "log", "message": message})

    def run(self):
        settings = self.project.settings
        tracks = self.project.selected_tracks()
        settings.validate()
        if not tracks:
            raise ValueError("Select at least one track.")
        if not settings.output_directory:
            raise ValueError("Choose an output folder.")
        if settings.mode == "hardware" and (not settings.setup_verified or not settings.midi_port or not settings.audio_device):
            raise ValueError("Complete the MPC hardware setup before exporting.")
        base = Path(settings.output_directory).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        # PCM24 output plus one temporary stereo FLOAT capture and margin.
        required = settings.frames * (len(tracks) * 6 + 8) + 50_000_000
        if shutil.disk_usage(base).free < required:
            raise ValueError("The output drive does not have enough free space for this export.")
        self.recorder.preflight()
        if self.cancel.is_set():
            self.emit({"type": "finished", "status": "cancelled", "directory": "", "complete": 0, "total": len(tracks)})
            return
        prefix = "DEMO_" if settings.mode == "demo" else "Stems_"
        stem = prefix + datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        for n in range(1, 10000):
            directory = base / (stem if n == 1 else f"{stem}_{n}")
            try:
                directory.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("Could not create a unique session folder.")
        self.directory = directory
        files = plan_filenames(tracks, directory)
        manifest = {"version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                    "project": self.project.to_dict(), "mode": settings.mode,
                    "status": "running", "alignment_verified_on_hardware": False,
                    "tracks": [{"number": t.number, "name": t.name,
                                "file": files[t.number].name, "status": "queued"} for t in tracks]}
        current = None
        error = None
        connected = False
        store = ManifestStore(directory, self.log)
        try:
            store.save(manifest)
            self.emit({"type": "directory", "path": str(directory)})
            self.controller.connect()
            connected = True
            for i, track in enumerate(tracks):
                if self.cancel.is_set():
                    raise Cancelled()
                current = manifest["tracks"][i]
                current["status"] = "recording"
                self.emit({"type": "status", "number": track.number, "status": "recording"})
                store.save(manifest)
                self.controller.isolate_track(track.number, self.cancel)
                pause(settings.settle_seconds if settings.mode == "hardware" else 0.05, self.cancel)
                def progress(ratio, peak):
                    self.emit({"type": "progress", "number": track.number, "pass": i + 1,
                               "total": len(tracks), "ratio": ratio, "overall": (i + ratio) / len(tracks), "peak": peak})
                metadata = self.recorder.record(files[track.number], self.controller, self.cancel, progress, track.number)
                current.update(metadata)
                current["status"] = "complete"
                self.emit({"type": "status", "number": track.number, "status": "complete"})
                if metadata.get("clipped"):
                    self.log(f"Track {track.number} reached digital clipping; reduce input gain and record again.")
                current = None
                # A metadata failure must never relabel a finished WAV as failed.
                store.save(manifest)
            manifest["status"] = "complete"
        except Cancelled:
            manifest["status"] = "cancelled"
            if current:
                current["status"] = "cancelled"
                self.emit({"type": "status", "number": current["number"], "status": "idle"})
        except Exception as exc:
            error = exc
            manifest["status"] = "error"
            manifest["error"] = str(exc)
            if current:
                current["status"] = "error"
                self.emit({"type": "status", "number": current["number"], "status": "error"})
        finally:
            # STOP is attempted even after device/capture errors. No guessed mute restoration.
            if connected:
                try:
                    self.controller.stop()
                except Exception as exc:
                    manifest["stop_error"] = str(exc)
                    manifest["status"] = "error"
                    error = error or RuntimeError(f"Could not stop the MPC: {exc}. Press STOP on the MPC.")
            try:
                self.controller.close()
            except Exception as exc:
                self.log(f"Could not close the MIDI port: {exc}")
            for row in manifest["tracks"]:
                if row["status"] == "queued":
                    row["status"] = "not_recorded"
            try:
                store.save(manifest)
            except OSError as exc:
                manifest["status"] = "error"
                manifest["manifest_save_error"] = str(exc)
                self.log(f"Could not save final export progress: {exc}. Completed WAVs are still in {directory}.")
                error = error or exc
            if store.snapshot_mode and store.last_path:
                self.log(f"Latest export progress: {store.last_path}")
        self.emit({"type": "finished", "status": manifest["status"], "directory": str(directory),
                   "complete": sum(t["status"] == "complete" for t in manifest["tracks"]), "total": len(tracks),
                   "manifest_path": str(store.last_path) if store.last_path else ""})
        if error:
            raise error
        return manifest
