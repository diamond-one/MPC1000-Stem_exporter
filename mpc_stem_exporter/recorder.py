from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from .controller import Cancelled, pause
from .model import Settings


def audio_devices() -> list[dict]:
    import sounddevice as sd
    apis = sd.query_hostapis()
    return [{"index": i, "name": d["name"], "hostapi": apis[d["hostapi"]]["name"],
             "channels": d["max_input_channels"]}
            for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] >= 2]


def resolve_device(settings: Settings) -> int:
    matches = [d for d in audio_devices() if d["name"] == settings.audio_device and d["hostapi"] == settings.audio_hostapi]
    if len(matches) != 1:
        raise ValueError("The saved audio input is unavailable or ambiguous. Choose it again in Setup.")
    if max(settings.input_left, settings.input_right) > matches[0]["channels"]:
        raise ValueError("The chosen stereo channels are not available on this audio input.")
    return matches[0]["index"]


class CaptureClock:
    """Assess timestamp progression, not its absolute epoch (zero is valid)."""
    def __init__(self, rate):
        self.rate = rate
        self.first = None
        self.frames = 0
        self.blocks = 0
        self.usable = True

    def observe(self, adc, frames):
        if not np.isfinite(adc):
            self.usable = False
        elif self.first is None:
            self.first = adc
        elif abs(adc - (self.first + self.frames / self.rate)) > max(0.005, frames / self.rate / 4):
            self.usable = False
        self.frames += frames
        self.blocks += 1


class InputMonitor:
    """Input-only level meter. No audio file or speaker output is created."""
    def __init__(self, settings):
        self.settings = settings
        self.stream = None
        self.lock = threading.Lock()
        self.peaks = np.zeros(2)
        self.clipped = [False, False]
        self.error = None
        self.last_received = None
        self.started = None
        self.clock = CaptureClock(settings.sample_rate)

    def _callback(self, data, frames, timing, status):
        import sounddevice as sd
        if status:
            self.error = f"Audio input: {status}. Check the input device or try another host API."
            raise sd.CallbackAbort
        stereo = data[:, [self.settings.input_left - 1, self.settings.input_right - 1]]
        if not np.isfinite(stereo).all():
            self.error = "The input driver returned invalid audio samples."
            raise sd.CallbackAbort
        peaks = np.max(np.abs(stereo), axis=0)
        self.clock.observe(float(timing.inputBufferAdcTime), frames)
        with self.lock:
            self.peaks = np.maximum(self.peaks, peaks)
            self.clipped = [a or b >= 1.0 for a, b in zip(self.clipped, peaks)]
            self.last_received = time.monotonic()

    def start(self):
        import sounddevice as sd
        if self.settings.mode != "hardware":
            raise ValueError("Choose Hardware mode to test an audio input.")
        self.settings.validate()
        device = resolve_device(self.settings)
        channels = max(self.settings.input_left, self.settings.input_right)
        sd.check_input_settings(device=device, samplerate=self.settings.sample_rate,
                                channels=channels, dtype="float32")
        self.started = time.monotonic()
        try:
            self.stream = sd.InputStream(device=device, channels=channels,
                                         samplerate=self.settings.sample_rate, dtype="float32",
                                         latency="high", callback=self._callback)
            self.stream.start()
        except Exception:
            self.stop()
            raise

    def poll(self):
        if self.error:
            raise RuntimeError(self.error)
        if self.stream is None:
            raise RuntimeError("Input meter is stopped.")
        if not self.stream.active:
            raise RuntimeError("Audio input stopped. Check the interface connection.")
        with self.lock:
            peaks = self.peaks.copy()
            self.peaks.fill(0)
            last = self.last_received
            clipped = tuple(self.clipped)
        if time.monotonic() - (last or self.started) > 3:
            raise RuntimeError("No audio buffers received. Check the device or choose another host API.")
        return {"peaks": peaks, "clipped": clipped, "receiving": last is not None,
                "driver_timing": self.clock.usable if self.clock.blocks >= 3 else None}

    def reset_peaks(self):
        with self.lock:
            self.peaks.fill(0)
            self.clipped = [False, False]

    def stop(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()


def write_exclusive(path: Path, blocks, rate: int) -> None:
    created = False
    try:
        with path.open("xb") as handle:
            created = True
            with sf.SoundFile(handle, mode="w", samplerate=rate, channels=2,
                              format="WAV", subtype="PCM_24") as output:
                for block in blocks:
                    output.write(block)
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise


class AudioRecorder:
    """Timestamped input captured to disk, then cropped to a fixed MIDI reference.

    This compensates input buffering without onset detection or silence trimming.
    A MIDI send timestamp is only an estimate of the MPC's actual musical start.
    """
    def __init__(self, settings: Settings, log=lambda _: None):
        self.settings = settings
        self.device = None
        self.log = log

    def preflight(self):
        import sounddevice as sd
        self.device = resolve_device(self.settings)
        sd.check_input_settings(device=self.device, samplerate=self.settings.sample_rate,
                                channels=max(self.settings.input_left, self.settings.input_right), dtype="float32")

    def record(self, path, controller, cancel, progress, number):
        import sounddevice as sd
        settings = self.settings
        rate = settings.sample_rate
        buffers = queue.Queue(maxsize=128)
        failure = []
        delivered = 0
        latest = None

        def callback(indata, frames, timing, status):
            nonlocal delivered, latest
            if status:
                failure.append(f"Audio input reported {status}. This pass was discarded.")
                raise sd.CallbackAbort
            try:
                latest = (delivered, frames, time.monotonic())
                delivered += frames
                buffers.put_nowait((indata[:, [settings.input_left - 1, settings.input_right - 1]].copy(),
                                    float(timing.inputBufferAdcTime)))
            except queue.Full:
                failure.append("Audio capture could not keep up with disk. This pass was discarded.")
                raise sd.CallbackAbort

        fd, raw_name = tempfile.mkstemp(prefix=".capture-", suffix=".wav", dir=path.parent)
        os.close(fd)
        origin = None
        written = 0
        peak = 0.0
        trigger_time = None
        clock = CaptureClock(rate)
        timing_source = "driver_timestamps"
        timing_degraded = False
        warmup = round(max(0.5, settings.preroll_seconds + 0.25) * rate)
        try:
            with sf.SoundFile(raw_name, "w", samplerate=rate, channels=2, subtype="FLOAT") as raw:
                with sd.InputStream(device=self.device, samplerate=rate,
                                    channels=max(settings.input_left, settings.input_right), dtype="float32",
                                    latency="high", callback=callback) as stream:
                    deadline = time.monotonic() + 5
                    while origin is None or written < origin + settings.frames:
                        if cancel.is_set():
                            raise Cancelled()
                        if failure:
                            raise RuntimeError(failure[0])
                        try:
                            block, adc = buffers.get(timeout=0.1)
                        except queue.Empty:
                            if time.monotonic() > deadline or not stream.active:
                                raise RuntimeError("Audio input stopped delivering samples. Check the interface connection.")
                            continue
                        deadline = time.monotonic() + 5
                        if not np.isfinite(block).all():
                            raise RuntimeError("Audio input returned invalid samples. This pass was discarded.")
                        clock.observe(adc, len(block))
                        if origin is not None and timing_source == "driver_timestamps" and not clock.usable and not timing_degraded:
                            timing_degraded = True
                            self.log("Driver timestamps stopped progressing. Continuing this pass using its fixed sample count.")
                        raw.write(block)
                        written += len(block)
                        peak = max(peak, float(np.max(np.abs(block))))
                        if origin is None and written >= warmup:
                            try:
                                trigger_time = float(stream.time)
                            except Exception:
                                trigger_time = None
                            candidate = None
                            if clock.usable and clock.blocks >= 3 and trigger_time is not None and np.isfinite(trigger_time):
                                candidate = round((trigger_time - clock.first - settings.preroll_seconds) * rate)
                            if candidate is not None and 0 <= candidate <= delivered + rate * 5:
                                origin = candidate
                            else:
                                timing_source = "sample_clock_estimate"
                                trigger_time = None
                                start_frame, block_frames, received_at = latest
                                latency = float(getattr(stream, "latency", 0) or 0)
                                if not np.isfinite(latency) or latency < 0:
                                    latency = 0
                                # Approximate the live input position from delivered samples,
                                # callback arrival and driver-reported input latency. No onset trim.
                                elapsed = max(0, time.monotonic() - received_at)
                                advance = max(block_frames / rate, latency) + elapsed
                                origin = max(0, round(start_frame + (advance - settings.preroll_seconds) * rate))
                                self.log("Audio driver timestamps unavailable; using sample-clock timing. "
                                         "WAV lengths and pre-roll are preserved; musical alignment is approximate.")
                            controller.play_from_start()
                        ratio = max(0, min(1, (written - origin) / settings.frames)) if origin is not None else 0
                        progress(ratio, float(np.max(np.abs(block))))
                    if failure:
                        raise RuntimeError(failure[0])
                controller.stop()
            def blocks():
                with sf.SoundFile(raw_name) as source:
                    source.seek(origin)
                    remaining = settings.frames
                    while remaining:
                        if cancel.is_set():
                            raise Cancelled()
                        block = source.read(min(65536, remaining), dtype="float32", always_2d=True)
                        if not len(block):
                            raise RuntimeError("Audio recording ended before the expected frame count.")
                        remaining -= len(block)
                        yield block
            write_exclusive(path, blocks(), rate)
            return {"frames": settings.frames, "sample_rate": rate, "peak": peak,
                    "clipped": peak >= 1.0, "midi_reference_audio_clock": trigger_time,
                    "timing_source": timing_source, "driver_timing_degraded": timing_degraded,
                    "alignment": ("Estimated sample clock; input latency and MIDI jitter unverified" if timing_source == "sample_clock_estimate"
                                  else "MIDI send timestamp; hardware latency and jitter unverified")}
        finally:
            Path(raw_name).unlink(missing_ok=True)


class DemoRecorder:
    """Synthetic audio only. Never opens an input device; runs at 6x speed."""
    def __init__(self, settings: Settings, speed=6.0):
        self.settings = settings
        self.speed = speed

    def preflight(self):
        pass

    def record(self, path, controller, cancel, progress, number):
        rate = self.settings.sample_rate
        total = self.settings.frames
        controller.play_from_start()
        peak = 0.0
        def blocks():
            nonlocal peak
            for start in range(0, total, 2048):
                if cancel.is_set():
                    raise Cancelled()
                count = min(2048, total - start)
                t = (np.arange(count) + start) / rate - self.settings.preroll_seconds
                pulse = np.remainder(np.maximum(t, 0), 0.5)
                freq = 45 + number * 37
                active = (t >= 0) & (t < self.settings.sequence_seconds)
                envelope = np.where(active, np.exp(-pulse * (6 + number % 5)), 0)
                tail = np.where(t >= self.settings.sequence_seconds,
                                np.exp(-np.maximum(0, t - self.settings.sequence_seconds) * 4) * 0.05, 0)
                signal = (np.sin(2 * np.pi * freq * t) * (envelope * 0.22 + tail)).astype("float32")
                block = np.column_stack((signal, signal * 0.94))
                current = float(np.max(np.abs(block)))
                peak = max(peak, current)
                yield block
                progress((start + count) / total, current)
                pause(count / rate / self.speed, cancel)
        write_exclusive(path, blocks(), rate)
        controller.stop()
        return {"frames": total, "sample_rate": rate, "peak": peak, "clipped": False,
                "alignment": "Synthetic demonstration audio; not recorded from an MPC"}
