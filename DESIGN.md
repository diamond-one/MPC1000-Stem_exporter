# MPC Stem Exporter interface

This is an Operate surface: select and name MPC tracks, confirm MIDI/audio routing, then record stems. This is the standard application interface.

## Visual system

Paper `#ECE9DC`, working surface `#F5F1E5`, ink `#1E2731`, blue `#244BA4`, red `#B63230`, yellow `#D0A72D`. Space Grotesk provides the heading and track-number voice; Segoe UI keeps controls familiar. Both font weights and their SIL license ship with the app.

Flat rectangular pads carry track numbers, names and explicit selection/status words. Red means selected or recording, pale blue means saved, yellow identifies the current bank, and blue identifies primary actions and keyboard focus. Status is always expressed in text as well as colour. A small circle/square/bar mark introduces the geometric vocabulary without competing with track selection.

## Workflow and layout

- Bank buttons A–D browse the persistent 64-track model. Bottom-left is pad 1. Changing bank preserves names and selections.
- MIDI output to the MPC and audio input from the MPC are separate labelled routes. Setup keeps connection tests and timing/preparation guidance in two scrollable tabs.
- The right side contains the recording session, duration, output folder and selected-track queue. Activating a queue row jumps to its bank and pad without changing selection.
- The primary action explains readiness: select tracks, set up recording, or record the selected number of stems. Record and Stop remain outside the scrolling settings area.
- At 1040 × 760 the pad grid, track editor and recording actions remain visible. Setup supports 710 × 620 with fixed Save/Cancel controls. Keyboard navigation, F2 naming, Enter/Escape and native project dialogs remain available.

## Boundaries

Light and dark appearance are the standard UI. The top-bar theme button switches
all app surfaces in place and saves a separate UI preference, independent of
project files. Both modes retain explicit selection/status text, visible keyboard
focus and contrasting actions. The executable and Qt windows share the app icon.

Recording, timing, MIDI command order, cancellation and persistence remain owned by the existing engine. Styling cannot imply that a device has been tested or that MIDI timing is sample exact. New sessions start empty; documentation screenshots use a separate illustrative project with unconfigured connections.
