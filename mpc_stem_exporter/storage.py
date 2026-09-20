"""Durable JSON saves that tolerate Windows readers holding a file open."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import tempfile
import time

REPLACE_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


class JsonSaveError(OSError):
    def __init__(self, target: Path, recovery_path: Path, cause: OSError):
        self.target = target
        self.recovery_path = recovery_path
        super().__init__(f"Could not replace {target.name}. A complete recovery copy was saved as "
                         f"{recovery_path}. ({cause})")


def json_snapshot(directory: Path, prefix: str, data: dict) -> Path:
    # Validate/serialize before opening anything; an incomplete write is never a recovery copy.
    payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".json", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # Cleanup must not replace the original I/O error.
        raise
    return path


def atomic_json(path: Path, data: dict) -> None:
    temporary = json_snapshot(path.parent, f".saving-{path.stem}-", data)
    for attempt in range(len(REPLACE_DELAYS) + 1):
        try:
            os.replace(temporary, path)
            return
        except OSError as exc:
            retryable = (isinstance(exc, PermissionError) or
                         getattr(exc, "winerror", None) in (5, 32, 33) or
                         exc.errno in (errno.EACCES, errno.EBUSY))
            if retryable and attempt < len(REPLACE_DELAYS):
                time.sleep(REPLACE_DELAYS[attempt])
                continue
            # Keep the closed, complete JSON. Never truncate the existing destination.
            raise JsonSaveError(path, temporary, exc) from exc


class ManifestStore:
    """Switch to numbered snapshots if export.json cannot be replaced.

    The revision inside each file identifies the latest state, independent of
    filesystem timestamps. Snapshot writes do not replace any open file.
    """
    def __init__(self, directory: Path, log=lambda _: None):
        self.directory = directory
        self.log = log
        self.snapshot_mode = False
        self.revision = 0
        self.last_path: Path | None = None

    def save(self, manifest: dict) -> Path:
        self.revision += 1
        payload = {**manifest, "manifest_revision": self.revision}
        if self.snapshot_mode:
            self.last_path = json_snapshot(self.directory, f"export-recovery-{self.revision:04}-", payload)
        else:
            target = self.directory / "export.json"
            try:
                atomic_json(target, payload)
                self.last_path = target
            except JsonSaveError as exc:
                self.snapshot_mode = True
                self.last_path = exc.recovery_path
                self.log("Windows prevented replacement of export.json. Continuing with separate "
                         "progress snapshots; WAV recording is unaffected.")
                self.log(f"Progress recovery file: {self.last_path}")
        return self.last_path
