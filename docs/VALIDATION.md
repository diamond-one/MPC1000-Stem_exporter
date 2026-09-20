# Validation report

19 September 2026 · Windows 11 · Python 3.12

The initial automated suite passed **88 tests**. It exercises:

- All 64 tracks' bank/pad MIDI messages, including release messages and channel selection.
- Persistent 64-track identity across bank browsing, project save/open, and interrupted status recovery.
- Global default names, reserved filenames, case-insensitive duplicate handling, and exclusive file creation.
- Selected-only ascending exports and immutable job snapshots.
- Stereo PCM24 WAV sample rate, exact frame count and retained demo pre-roll.
- Timestamp-based crop positioning and arbitrary stereo input channel selection in the real recorder using a deterministic fake driver.
- Simulated driver overflow and unusable timestamps: initial behavior discarded the failed pass and cleaned up temporary files. Version 0.1.2 changes the timestamp behavior as described below.
- Cancellation preserving finished stems, failure propagation, and final transport STOP attempts.
- Qt keyboard navigation, rename commit/cancel, double-click without changing selection, and a complete demo export while browsing another bank.

The native Windows interface was visually reviewed. The review identified and
corrected a stylesheet precedence issue affecting button contrast and a minimum
pad size that compressed row gaps.

The first portable build picked up an incompatible `icuuc.dll` from a separate
tool on the build environment's PATH. `build_windows.py` now isolates the library
search path for a reproducible Windows package. The source application was not
affected by this packaging issue.

The corrected standalone Windows executable was launched and completed an
eight-stem export through its visible interface. All eight files were verified
as stereo PCM24 WAV, 48,000 Hz, exactly 492,000 frames (10.25 seconds), and the
manifest marked every selected pass complete. This run used synthetic demo
audio and did not open an audio input or send hardware MIDI.

No physical MPC1000 or connected audio input was recorded during testing.
Hardware MIDI mapping behavior, input-driver timing, tail behavior, and musical
alignment remain subject to the acceptance procedure in RESEARCH-AND-SPEC.md.

## Version 0.1.1 — Windows progress-file locking

The updated suite passed **96 tests**, including eight new storage/error checks.
Two tests use actual Windows file handles opened without FILE_SHARE_DELETE:

- Replacing JSON succeeds after a temporary reader releases its lock.
- A lock held on `export.json` throughout a three-stem export no longer aborts
  recording. All three WAVs have the expected frame count and the latest recovery
  snapshot records every pass as complete.

Additional tests verify complete recovery JSON is preserved, invalid JSON creates
no recovery artifact, fallback stops repeated replacement attempts, metadata I/O
errors do not mark finished WAVs as failed, and a final progress-save failure
does not mask the original audio error. Persistent inability to write any progress
data still reports a storage error and stops safely, retaining completed audio.

The rebuilt **0.1.1 standalone executable** was also tested through its native
Windows UI with a separate process holding `export.json` open without delete
sharing for the entire job. It completed all eight selected stems (72,000 frames
each), and recovery revision 18 correctly reported every pass complete. This
tests the packaged application against the reported failure mode, beyond the
automated source-level checks.

## Version 0.1.2 — routing tests and driver timing fallback

20 September 2026. The suite passed **110 tests**. New checks cover:

- MIDI bank test message order, receive channel and absence of pad/mute messages.
- Two-second playback test STOP and port cleanup, including send errors,
  cancellation before playback and closing Setup while a worker is active.
- MIDI testing before audio routing has been configured.
- Input-only stereo metering, channel selection, short-tap peak retention,
  latched clipping, reset and driver error reporting with a simulated input.
- Setup meter display updates and stream release on route/rate changes,
  Save and Cancel. Demo mode cannot open the hardware meter.
- Static zero and non-finite driver timestamps use approximate timing and
  produce the requested WAV frame count. Progressing timestamps whose epoch
  begins at zero remain valid; later timestamp jumps retain the chosen window
  and report degraded timing. Overflow still discards the affected pass.

The Setup layout was rendered and visually reviewed. These automated routing
checks use simulated MIDI/audio devices; physical MPC receipt and actual signal
at the selected interface still require the visible tests in Setup.

The standalone 0.1.2 executable was launched and the new Setup controls were
reviewed in Windows. The saved `Line (SP-404MKII-G)` input using Windows WDM-KS,
channels 1/2 at 48 kHz, opened successfully and remained active in the live
meter. No signal above −60 dBFS was present during that check. Driver timing
progression was available. The meter was then stopped, releasing the input;
no audio file was created and no hardware MIDI was sent. This verifies opening
the actual selected input, but does not establish the physical MPC audio route.

## Version 0.1.3 — preparation guidance

Added the user's full-song conversion instructions and an explanation that
existing MPC mute states do not exclude selected tracks. The guide gives
deselection as an alternative to deleting MIDI data and recommends a backed-up
working copy when deletion is needed. The updated guide was rendered and
visually checked; recording and routing behavior is unchanged from 0.1.2.
