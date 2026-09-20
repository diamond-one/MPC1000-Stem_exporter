from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .storage import atomic_json

BANKS = "ABCD"
PAD_ORDER = (13, 14, 15, 16, 9, 10, 11, 12, 5, 6, 7, 8, 1, 2, 3, 4)
STATUSES = {"idle", "queued", "recording", "complete", "error"}


@dataclass
class Track:
    number: int
    selected: bool = False
    custom_name: str = ""
    status: str = "idle"

    @property
    def bank(self) -> str:
        return BANKS[(self.number - 1) // 16]

    @property
    def pad(self) -> int:
        return (self.number - 1) % 16 + 1

    @property
    def name(self) -> str:
        return self.custom_name.strip() or f"Track_{self.number}"


@dataclass
class Settings:
    mode: str = "hardware"
    midi_port: str = ""
    midi_channel: int = 1
    audio_device: str = ""
    audio_hostapi: str = ""
    input_left: int = 1
    input_right: int = 2
    sample_rate: int = 48000
    sequence_seconds: float = 8.0
    tail_seconds: float = 2.0
    preroll_seconds: float = 0.25
    settle_seconds: float = 0.5
    command_gap_ms: int = 100
    output_directory: str = ""
    setup_verified: bool = False

    @property
    def frames(self) -> int:
        return round((self.preroll_seconds + self.sequence_seconds + self.tail_seconds) * self.sample_rate)

    def validate(self) -> None:
        if self.mode not in {"demo", "hardware"}:
            raise ValueError("Choose Demo or Hardware mode.")
        if self.sample_rate not in (44100, 48000, 88200, 96000):
            raise ValueError("Unsupported sample rate.")
        for key, lo, hi in (("sequence_seconds", 0.1, 14400), ("tail_seconds", 0, 600),
                            ("preroll_seconds", 0.1, 5), ("settle_seconds", 0, 60)):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f"Invalid {key.replace('_', ' ')}.")
        for key, lo, hi in (("midi_channel", 1, 16), ("input_left", 1, 64),
                            ("input_right", 1, 64), ("command_gap_ms", 20, 1000)):
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"Invalid {key.replace('_', ' ')}.")
        if self.input_left == self.input_right:
            raise ValueError("Choose two different audio inputs for stereo.")
        if (self.frames + 10 * self.sample_rate) * 8 > 4_000_000_000:
            raise ValueError("This pass exceeds the WAV size limit. Use a shorter sequence or lower sample rate.")
        for key in ("midi_port", "audio_device", "audio_hostapi", "output_directory"):
            if not isinstance(getattr(self, key), str):
                raise ValueError(f"Invalid {key}.")
        if type(self.setup_verified) is not bool:
            raise ValueError("Invalid setup verification flag.")


@dataclass
class Project:
    title: str = "Untitled session"
    tracks: list[Track] = field(default_factory=lambda: [Track(n) for n in range(1, 65)])
    settings: Settings = field(default_factory=Settings)

    def bank_tracks(self, bank: str) -> list[Track]:
        offset = BANKS.index(bank) * 16
        return [self.tracks[offset + n - 1] for n in PAD_ORDER]

    def selected_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.selected]

    def select(self, enabled: bool, bank: str | None = None) -> None:
        for track in self.tracks:
            if bank is None or track.bank == bank:
                track.selected = enabled
                track.status = "idle"

    def to_dict(self) -> dict:
        return {"version": 1, "title": self.title, "tracks": [asdict(t) for t in self.tracks],
                "settings": asdict(self.settings)}

    def save(self, path: Path) -> None:
        atomic_json(path, self.to_dict())

    @classmethod
    def load(cls, path: Path) -> Project:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("This is not a supported Stem Exporter project.")
        rows = data.get("tracks", [])
        if not isinstance(rows, list) or len(rows) != 64:
            raise ValueError("A project must contain exactly 64 tracks.")
        tracks = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid track data.")
            track = Track(**row)
            if type(track.number) is not int or type(track.selected) is not bool or not isinstance(track.custom_name, str) or track.status not in STATUSES:
                raise ValueError("Invalid track data.")
            # An interrupted recording is never presented as completed.
            if track.status in {"recording", "queued"}:
                track.status = "idle"
            tracks.append(track)
        tracks.sort(key=lambda t: t.number)
        if [t.number for t in tracks] != list(range(1, 65)):
            raise ValueError("Track numbers must be unique and cover 1–64.")
        settings = Settings(**data.get("settings", {}))
        settings.validate()
        title = data.get("title", "Untitled session")
        if not isinstance(title, str):
            raise ValueError("Invalid project title.")
        return cls(title, tracks, settings)


def safe_stem(name: str, number: int) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.strip()).rstrip(" .")
    if name.lower().endswith(".wav"):
        name = name[:-4].rstrip(" .")
    name = name[:100].rstrip(" .") or f"Track_{number}"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I):
        name = "_" + name
    return name


def plan_filenames(tracks: list[Track], directory: Path) -> dict[int, Path]:
    taken = {p.name.casefold() for p in directory.iterdir()} if directory.exists() else set()
    planned = {}
    for track in sorted(tracks, key=lambda t: t.number):
        stem = safe_stem(track.name, track.number)
        filename = stem + ".wav"
        suffix = 2
        while filename.casefold() in taken:
            filename = f"{stem}_{suffix}.wav"
            suffix += 1
        taken.add(filename.casefold())
        planned[track.number] = directory / filename
    return planned


def demo_project() -> Project:
    project = Project("Midnight sketches", settings=Settings(mode="demo"))
    for n, name in {1: "Kick", 2: "Snare", 3: "Closed hat", 4: "Open hat", 5: "Percussion",
                    6: "Bass", 9: "Rhodes", 10: "Texture"}.items():
        project.tracks[n - 1].custom_name = name
        project.tracks[n - 1].selected = True
    return project
