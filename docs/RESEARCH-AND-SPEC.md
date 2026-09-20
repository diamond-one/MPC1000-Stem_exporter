# MPC1000 JJOS3 Stem Exporter

Research and implementation specification · 19 September 2026

## Decision

Build a Python desktop prototype with PySide6, Mido/python-rtmidi, sounddevice,
and soundfile. Send MIDI directly to the MPC. JJ's controller and Bome are not
runtime dependencies. Use a 16-pad, four-bank interface backed by 64 persistent
track objects. Validate the hardware workflow before considering a JUCE rewrite.

The exact control numbers are published in JJ's controller diagrams. We do not
need to reverse-engineer the setup binary to obtain the required mapping.
The actual MPC is still required to verify button behavior, setup compatibility,
track isolation, and timing. Documentation establishes feasibility, not a completed
hardware acceptance test.

## Evidence and MIDI preset

JJ lists OS3 among supported versions and supplies an OS-specific setup file in
the official controller download. The downloaded archive contains the member
`MPC1000/OS3/MPC1K_SETUPS.SYS` (784 bytes; SHA-256
`16f89897c313ddf424f589d08e50c145853b30d3768257a7fedf13f6a3d97a12`).
The website visually spells it `MPC1 K_SETUPS.SYS`; use the archive's filename
unchanged. Download it from JJ and choose the MPC1000 **OS3** folder. The app
does not redistribute that file or execute JJ's Windows utility.
[Official controller and download](https://www7a.biglobe.ne.jp/~mpc1000/mpc-ctl/index.htm).

The following decimal values are transcribed from JJ's diagrams:

| Control | Message | Number | Source |
| --- | --- | ---: | --- |
| PLAY START | Note | 1 | [Transport diagram](https://www7a.biglobe.ne.jp/~mpc1000/mpc-ctl/playrec.jpg) |
| PLAY | Note | 2 | Same transport diagram |
| STOP | Note | 3 | Same transport diagram |
| MAIN | Note | 77 | [Bank/control diagram](https://www7a.biglobe.ne.jp/~mpc1000/mpc-ctl/bankcur.jpg) |
| TRACK MUTE | Note | 88 | Same bank/control diagram |
| BANK A / B / C / D | Note | 89 / 90 / 91 / 92 | Same bank/control diagram |
| F1 / F2 / F3 / F4 / F5 / F6 | Note | 93 / 94 / 95 / 96 / 97 / 98 | [Function diagram](https://www7a.biglobe.ne.jp/~mpc1000/mpc-ctl/function.jpg) |
| PAD 01–16 | CC | 16–31 | [Track/pad diagram](https://www7a.biglobe.ne.jp/~mpc1000/mpc-ctl/trackpad.jpg) |
| TRACK SELECT 01–16 | Note | 29–44 | Same track/pad diagram; **not used for isolation** |

Send Note On velocity 127, then Note Off velocity 0 after 20 ms. Send pad CC
value 127, then value 0 after 20 ms. Start with a configurable 100 ms gap after
each button operation. These timings are prototype engineering choices, not
published JJ guarantees.

OS3's manual links to the button-assignment page documenting Note On as a button
press, and CC values at least 64 as pad-on and at most 63 as pad-off. It also
documents MIDI input enable, input port 1/2 and receive channel selection. Configure
the channel explicitly in both the MPC and app; the prototype defaults to channel
1 for convenience, **not as a verified property of the binary setup file**.
The direct TRK MUTE/SOLO note assignments have hold/release semantics and only
name tracks 1–16, so they are not the 64-track isolation strategy.
[OS3 manual](https://www7a.biglobe.ne.jp/~mpc1000/128xl/manual_v2_os3_e.htm) ·
[OS3-linked button assignment page](https://www7a.biglobe.ne.jp/~mpc1000/128xl/button_light.htm).

## Track-isolation sequence

For a global track `n` in 1–64:

```
bankIndex = (n - 1) // 16
localPad  = (n - 1) % 16 + 1
bankNote  = 89 + bankIndex
padCC     = 15 + localPad

STOP → MAIN → TRACK MUTE → BANK → F1 (ALLMUTE) → PAD
     → wait for previous audio to decay
     → arm recorder and collect pre-roll
     → PLAY START
     → capture sequence + tail
     → STOP → finalize WAV
```

MAIN establishes a known screen before entering TRACK MUTE. F1 is context
sensitive and must only be sent on the Track Mute screen. The controller's
`isolate_track(n)` deliberately re-establishes all-muted state for each pass;
it never presents a pad toggle as an idempotent set-enabled operation. The
transport API uses `play_from_start()` because PLAY START combines locate and
play. A separate silent `gotoStart()` is not assumed to exist in this preset.

| Global track | Bank | Local pad | Bank note | Pad CC |
| ---: | --- | ---: | ---: | ---: |
| 1 | A | 1 | 89 | 16 |
| 16 | A | 16 | 89 | 31 |
| 17 | B | 1 | 90 | 16 |
| 36 | C | 4 | 91 | 19 |
| 64 | D | 16 | 92 | 31 |

OS3 documents four banks covering 64 tracks, F1 all-mute, F2 clear, and pad
mute/unmute. It also documents mute groups, stored mute events, and sample
stop behavior. Groups can affect additional tracks; recorded mute events can
change the isolation during playback. Use ungrouped tracks and set Use events
OFF for this workflow. Start outside solo mode. The prototype cannot query or
restore a previous hardware mute mask. It stops playback and leaves the last
isolation in place; F2 CLEAR on Track Mute restores all tracks manually.
[OS3-linked Track Mute page](https://www7a.biglobe.ne.jp/~mpc1000/128xl/t_mute_light_e.htm).

## One-time setup and per-sequence checks

1. Save the MPC's existing system setup separately before loading JJ's setup.
   This is a system configuration file, not merely an app-local MIDI preset.
   Keep its filename unchanged.
   [System setup save/load](https://www7a.biglobe.ne.jp/~mpc1000/128xl/saveload_light.htm).
2. Obtain the OS3 setup from JJ's official download, copy to CF, and load it on
   the MPC1000. Alternatively assign just the table's needed functions manually.
3. Connect computer MIDI OUT to the selected MPC MIDI IN. Enable button input
   and match the receive channel in MPC MIDI/SYNC → BUTN and the app.
4. Connect MAIN OUT L/R to two line inputs. Select their device, host API,
   physical channel numbers, and sample rate in the app. Set input gain on the
   audio interface; the app does not normalize recordings.
5. Load the intended sequence, disable sequence looping, disable metronome during
   playback, turn off mute-event playback and mute groups, exit solo/record
   standby, and check that PLAY START begins at the intended first bar.
6. Set sequence duration accurately. For constant tempo with `b` bars and time
   signature numerator `p` and denominator `q`: seconds = `b * p * (4/q) * 60/BPM`.
   Tempo changes require the actual integrated duration or manual measurement.
7. Set tail and inter-pass settling time long enough for one-shots/effects.
   Verify the MPC's stop/mute behavior with the material being exported.

Looping must be OFF: leaving playback running for the tail while a sequence loops
would record the beginning again. JJ documents loop settings and different
PLAY START combinations. The prototype requires a one-shot sequence and sends
STOP only after capture of the tail; natural sequence completion prevents new
events during the tail. Confirm this on the unit.
[OS3 sequence-loop behavior](https://www7a.biglobe.ne.jp/~mpc1000/128xl/seq_loop_v2_os3_e.htm).

## Alignment: explicit acceptance boundary

The original request's sample-exact alignment cannot be promised from one-way
MIDI control and unsynchronized analog capture alone. OS scheduling, MIDI wire
time, the MPC response, audio-driver timing, and independent clocks introduce
latency or variation. Equal frame counts are not evidence of equal musical starts.

Prototype behavior:

- Every selected pass uses one frozen timing configuration and exactly
  `round((preRoll + sequence + tail) * sampleRate)` stereo PCM24 frames.
- Default pre-roll is 250 ms, retained in every file. File zero is the common
  nominal pre-roll origin; the nominal sequence start is at +250 ms. The files
  can share DAW zero, but timing has not been certified to sample precision.
- Input ADC timestamps and the audio clock at MIDI dispatch estimate the start.
  If timestamps are unavailable or do not progress, v0.1.2 estimates the input
  position from captured samples, callback arrival and reported input latency.
  This fallback is explicitly logged and saved as `sample_clock_estimate`.
  Capture warm-up is discarded using that fixed reference, never signal onset.
  Intentional leading silence in the sequence is preserved. No per-track trim,
  normalization, onset alignment, or resampling is applied.
- The recording goes to a temporary float WAV, then a fixed frame window is
  written to the final PCM24 WAV. Capture overflows, invalid samples, device
  loss and cancellation fail/discard the current pass. A timestamp discontinuity
  after the recording window is selected logs degraded timing and retains that
  same sample window; it never performs signal-based realignment.
- A manifest records sample count, sample rate, peak, clipping flag, mode, and
  alignment limitation. Hardware alignment is never automatically marked verified.

For a production promise of sample-exact musical starts, add a separately
recorded reference from the MPC in every pass, independent of the isolated
track (for example an appropriately routed sync impulse), and align from that
reference while retaining the intended timeline silence. Define the reference
route and verify repeatability on the actual MPC/interface before committing to
that feature. Software MIDI clock alone is not a proof of sample accuracy.

Audio-driver APIs expose capture timestamps and status flags; Mido describes its
send behavior as immediate dispatch without a guarantee of device action time.
[sounddevice streams](https://python-sounddevice.readthedocs.io/en/0.5.3/api/streams.html) ·
[Mido ports](https://mido.readthedocs.io/en/stable/ports/index.html).

## Product and architecture requirements

- Always maintain exactly 64 Track objects with global number, selected flag,
  custom name, and export status. Derive bank and local pad from global number.
  Banks only project this model; they never mutate it.
- Display pads in physical order: 13–16 / 9–12 / 5–8 / 1–4. Always show the global
  track number. A/B/C/D correspond to 1–16 / 17–32 / 33–48 / 49–64.
- Single click toggles inclusion; double click edits a name without toggling.
  A focused-track editor beneath the grid supports Enter commit and Escape
  cancel. Support Tab, arrow keys, Space and F2.
- Show neutral, selected/waiting, recording, completed and error states using
  text/symbols as well as color. Keep bank browsing and the pad grid available
  during recording; freeze edits and job settings until the job ends.
- Select All and Clear affect all 64 tracks. Select Bank/Clear Bank affect only
  the visible bank. Show total selected count and dynamic EXPORT N STEMS.
- Save/open JSON projects; autosave names, selection and setup. Loading an
  interrupted project resets queued/recording statuses rather than claiming
  success. Preserve complete/error statuses for review.
- Separate modules: model, MPCController, AudioRecorder, StemExporter, UI.
  UI never constructs or sends raw MIDI. Run export work off the GUI thread.
- Freeze selected tracks/configuration at export; record sequentially in global
  ascending order, including selections outside the displayed bank.
- Use custom names or `Track_<global number>`. Sanitize filenames internally;
  preserve UI text. Handle Windows reserved names and case-insensitive duplicate
  collisions. Never overwrite a pre-existing WAV. Create a unique session folder.
- Cancel stops transport, discards an unfinished pass, retains finished files,
  writes an export manifest, and allows a subsequent fresh export. Device errors
  stop the queue rather than silently producing incomplete stems.
- Provide a clearly labeled demo using synthetic audio and no MIDI/audio input
  access. This proves software flow only. The first-run example is demo data.

## Validation and limits

Automated checks must exercise cross-bank persistence, global naming, duplicate
and reserved filenames, malformed project rejection, all 64 MIDI mappings,
ascending selected-only exports, WAV channel/rate/frame properties, fixed
pre-roll, cancellation cleanup, error propagation, and STOP attempts. Render and
exercise the native interface independently of physical MIDI/audio devices.

Hardware acceptance, still required:

1. Verify STOP and MAIN, then enter Track Mute with no modal window/solo active.
2. Verify A01, A16, B01, C04 and D16 isolate global tracks 1,16,17,36,64.
   Confirm ALLMUTE affects every bank, and release messages do not retrigger pads.
3. Record a known sequence with leading silence; confirm start position, full
   duration and decaying tail without a loop restart or metronome.
4. Repeat one reference pass ten times; compare measured onsets, duration and
   drift in a DAW. Record the maximum timing error before deciding tolerance.
5. Cancel mid-pass and disconnect MIDI/audio; verify transport stops where
   reachable, partial files are discarded, and completed stems are preserved.
6. Reopen a project, change banks and repeat export; names/selections survive and
   no file is overwritten.

No connected MPC1000 has been used during this build. Hardware state readback,
automatic sequence/tempo discovery, arbitrary previous mute-state restoration,
multi-sequence export, and reference-based sample alignment are outside the
prototype. Shared effects and nonlinear processing can make isolated passes
differ from the original full mix; do not promise exact mix reconstruction.
