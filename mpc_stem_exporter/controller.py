"""Direct MIDI remote control. No UI dependency or third-party controller app.

Mappings transcribed from JJ's official MPC Controller diagrams (see docs).
Mute state is NOT observable over this protocol. Only isolation from ALLMUTE
is supported; arbitrary idempotent setTrackEnabled would misrepresent it.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .model import Settings

NOTE = {"play_start": 1, "play": 2, "stop": 3, "main": 77,
        "track_mute": 88, "A": 89, "B": 90, "C": 91, "D": 92,
        "all_mute": 93, "clear_mutes": 94}


class Cancelled(Exception):
    pass


def pause(seconds: float, cancel: threading.Event) -> None:
    if cancel.wait(max(0, seconds)):
        raise Cancelled("Export cancelled.")


class MPCController:
    def __init__(self, settings: Settings, log: Callable[[str], None] = lambda _: None,
                 port=None, sleeper: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.log = log
        self.port = port
        self.sleep = sleeper

    def connect(self) -> None:
        if self.port is None:
            import mido
            mido.set_backend("mido.backends.rtmidi")
            self.port = mido.open_output(self.settings.midi_port)

    def _note(self, action: str, settle: bool = True) -> None:
        import mido
        number = NOTE[action]
        self.log(f"MIDI {action}: note {number}, channel {self.settings.midi_channel}")
        self.port.send(mido.Message("note_on", channel=self.settings.midi_channel - 1, note=number, velocity=127))
        try:
            self.sleep(0.02)
        finally:
            self.port.send(mido.Message("note_off", channel=self.settings.midi_channel - 1, note=number, velocity=0))
        if settle:
            self.sleep(self.settings.command_gap_ms / 1000)

    def _pad(self, pad: int) -> None:
        import mido
        self.log(f"MIDI pad {pad}: CC {pad + 15}")
        self.port.send(mido.Message("control_change", channel=self.settings.midi_channel - 1, control=pad + 15, value=127))
        try:
            self.sleep(0.02)
        finally:
            self.port.send(mido.Message("control_change", channel=self.settings.midi_channel - 1, control=pad + 15, value=0))
        self.sleep(self.settings.command_gap_ms / 1000)

    def isolate_track(self, number: int, cancel: threading.Event) -> None:
        if not 1 <= number <= 64:
            raise ValueError("MPC track must be between 1 and 64.")
        # MAIN establishes a known page before TRACK MUTE, which may toggle.
        for action in ("stop", "main", "track_mute", "ABCD"[(number - 1) // 16], "all_mute"):
            if cancel.is_set():
                raise Cancelled()
            self._note(action)
        if cancel.is_set():
            raise Cancelled()
        self._pad((number - 1) % 16 + 1)

    def play_from_start(self) -> None:
        # PLAY START performs locate + play together. No assumed MIDI SPP.
        self._note("play_start", settle=False)

    def test_routing(self, cancel: threading.Event) -> None:
        """Visible MAIN/bank-LED test; no pad, mute or playback messages."""
        for action in ("main", "A", "B", "C", "D", "A"):
            if cancel.is_set():
                raise Cancelled()
            self._note(action)
            pause(0.25, cancel)

    def stop(self) -> None:
        if self.port is not None:
            self._note("stop", settle=False)

    def close(self) -> None:
        if self.port is not None:
            self.port.close()
            self.port = None


class DemoController:
    def __init__(self, settings: Settings, log=lambda _: None):
        self.log = log

    def connect(self):
        self.log("DEMO: simulated MIDI; no hardware messages are sent.")

    def isolate_track(self, number, cancel):
        pause(0.08, cancel)
        self.log(f"DEMO: isolate track {number}")

    def play_from_start(self):
        self.log("DEMO: playback from start")

    def stop(self):
        self.log("DEMO: stop")

    def close(self):
        pass
