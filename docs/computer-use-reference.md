# Wayland Computer Use

Historical pre-consolidation reference. Installation paths, test counts and client
status below describe that development session. Use the [main README](../README.md)
for current setup; the current entry point is `scripts/dev_host.py`.

Personal MCP plugin for the ChatGPT desktop app's Codex mode on Hyprland/Wayland.
This is independent of OpenAI's bundled Sky runtime. Version 0.1 targets Hyprland,
not every Wayland compositor, and operates the user's unlocked foreground desktop.

## Current validation status

On this machine, MCP screenshots, window focus, pointer movement, Unicode typing,
Ctrl+A, replacement text, and Enter have been exercised in a disposable GTK4
Wayland window. The dispatcher implementation targets Hyprland's Lua config mode.

After explicit approval, the backend was enabled without `--keyboard-off`:
ydotool 1.0.4 requires EV_KEY for mouse buttons as well as keyboard keys. Left,
right, middle, double clicks and dragging pass in the native GTK4 fixture.
Wheel scrolling passes in both axes and both directions in an isolated Chromium
Wayland window using a local test page. The GTK4 fixture still does not report
wheel scrolling even though its Wayland trace receives complete axis events;
that application-specific discrepancy remains unresolved. This is an experimental
local integration, not a generally validated release.

Both the desktop app's bundled Codex executable and the terminal Codex executable
discover the enabled `wayland` MCP server. Fresh model-driven conversations in
each client still need a user-facing acceptance test; configuration discovery and
standalone MCP tests are not equivalent to end-to-end client validation.

## Components

- Python 3 standard-library MCP server over stdin/stdout (no network listener).
- `grim`: per-output PNG screenshots in memory, at scale 1.
- `magick` (ImageMagick): extract 8-bit RGB target crops from those same PNGs.
- `hyprctl`: window/monitor enumeration, focus and global logical pointer movement.
- `wtype`: Unicode typing and key chords through Wayland virtual keyboard.
- `ydotool`: real pointer button, wheel, and drag events through Linux uinput.
- Companion skill describes frame IDs, coordinate conventions and foreground use.

The dedicated system input service supports both keyboard and mouse injection,
restricts device access to `/dev/uinput`, and exposes a mode-0600 Unix socket owned
by UID 1000. Typing in this plugin still uses `wtype`, not the privileged backend.
The service file is specific to david (UID/GID 1000); adjust those values for another
account. The MCP server itself runs as the desktop user. No input-group membership
or passwordless sudo is required. Other processes running as that same user can
also access the socket and inject keyboard or mouse events; this is not an app
isolation boundary. The service's historical `-mouse` name does not imply that
the daemon is limited to mouse events.

## Setup

Install `grim`, `wtype`, `ydotool`, `python`, `imagemagick` and Hyprland. On Omarchy use
`omarchy pkg add grim wtype ydotool python imagemagick`. Install the supplied service file in
`/etc/systemd/system/`, then `systemctl daemon-reload` and
`systemctl enable --now wayland-computer-use-mouse.service` with administrator
privileges. On this machine uinput is loaded through a modules-load drop-in.

Install the plugin from your personal marketplace, then start a new thread with
Wayland Computer Use enabled. Existing threads do not automatically gain its tools.
The installed `.mcp.json` uses the explicit local source path
`/home/david/plugins/wayland-computer-use/scripts/server.py`; update it if moving
the plugin or installing on another account. It does not rely on placeholder expansion.

For GUI-launched processes without desktop variables, the server first resolves
the current user's runtime directory and bus socket, then imports only four
desktop connection variables from the user service manager. If needed, it queries
`hyprctl -j instances` and selects a unique matching session whose compositor and
Wayland sockets belong to the current user. It does not guess among multiple
sessions or overwrite explicit session selections. This discovery does not bypass
sandbox socket restrictions: the MCP host must permit access to the desktop's
Unix sockets. Restart the server after a session change.

## Behavior and limits

### Approval-aware input

Input tools accept optional `restore_focus` (default false), `target_window`
(window address), and `target_title`. For an approval that moves focus, pass true
and the exact address/title from the previous screenshot's `target_window` object.
The tool descriptions explicitly include focusing and acting in one approved
operation; host approval settings are unchanged. Use the screenshot returned by
`focus_window`, not an extra screenshot request that may itself steal focus.

The server remembers the window's identity, title, geometry, workspace and monitor.
After approval it verifies those values, restores the exact target once, checks
again, and acts. Coordinates must remain inside that target. Non-scroll operations
also compare uncompressed visible target pixels with an eight-logical-pixel
inset to exclude focus borders. At most 2% of pixels may change, with no RGB-channel
delta above 6/255. Around coordinate actions (32 screenshot pixels of margin),
the channel limit is 2/255 and the 2% budget applies independently. Drag checks
cover the entire start/end bounding rectangle plus margin. Keyboard input uses
the stricter limits across the whole crop because its focused control is unknown.
Coordinates in the excluded border are rejected for non-scroll operations.
This is a conservative heuristic, not semantic UI recognition: low-contrast UI
changes may pass, while carets, animation or occlusion may still cause rejection.
Scrolling retains its existing geometry/focus checks without visual comparison.

For same-chat development, see [DEVELOPMENT.md](../DEVELOPMENT.md). The development
host reloads explicitly tested/published implementation builds between tool calls
without disconnecting MCP; every reload invalidates prior screenshot IDs.

The baseline crop now comes from the exact PNG returned to the agent, not a
separate earlier capture. Visual rejections include changed-pixel count, maximum
RGB-channel difference (0–255), total pixels, and an exclusive-end bounding box
relative to the crop. Metrics also include the policy, acceptance decision and
action-region comparison. Thresholds are fixed in code, not agent-overridable.

Optional `WAYLAND_CU_DEBUG_DIR` saves rejected before/after RGB crops as PPM files
and `metrics.json`. Directories must be private (0700); files are created as 0600.
Retention stops after 20 rejection directories without deleting any existing data.
This local installation temporarily enables it at
`/home/david/Work/wayland-computer-use-debug` for the requested investigation.
Remove the variable from `.mcp.json` and restart the server to disable retention.
These crops may contain sensitive screen contents: do not commit or share them.
Debug-write failures are reported and never turn a rejection into an allowed input.

Optional `WAYLAND_CU_TRACE_DIR` records per-request timing spans, outcomes and
counters as JSON lines in a private 0700 directory, capped at 64 MiB with an
explicit dropped-record counter reported by `desktop_state`. Records contain no
typed text, key names, titles, URLs or screen contents. See
[latency.md](latency.md#tracing-and-attribution).

Expired frames require renewed review rather than extending their lifetime.
Pre-input revalidation failures return `action_performed: false`, `requires_review`,
and a fresh screenshot when available. Focus restoration may already have occurred.
Input interrupted during a drag is reported as partial execution and the button
is released. There is still a race between the final check and input on a shared
desktop; restoration is not an app isolation mechanism. No automatic input retries.

`tests/browser_scroll.py --approval-roundtrip` simulates approval focus changes
before each wheel action in a disposable Chromium window. Real client approval
dialogs still need an acceptance trial after installing this update.

Call `desktop_state`, focus the intended window if needed, then `screenshot`.
Use the returned `frame_id` with one action. Each action returns the resulting
screenshot. Only the latest frame is accepted; it expires after 120 seconds.
Monitor reconfiguration and active-window changes invalidate it. Coordinates are
pixels in the original returned image, not a scaled preview. Rotation and scale
are mapped to Hyprland's global logical coordinates.

These checks reduce stale input, but the user and other apps can still change the
screen between inspection and input. There is no accessibility tree, per-app
permission dialog, background desktop, or locked-desktop support. Full monitor
screenshots can include other visible apps and are sent to the model. Text and
screenshots are not persisted by the server unless the opt-in rejection diagnostics
described above are enabled. The host app may retain
tool history according to its settings. Newlines in typed text may execute a
terminal command or send a chat message; the server does not automatically append
Enter. The tools expose no arbitrary shell execution API.

On action failure, take a fresh screenshot before retrying. `hyprlock` blocks input;
other lock implementations are not explicitly detected. The compositor still owns
lock-screen isolation. Changing workspaces/focus is visible to the user.

## Disable / uninstall

Disable or uninstall the plugin in the desktop app. To stop its mouse service:
`pkexec systemctl disable --now wayland-computer-use-mouse.service`.
The installed custom files are `/etc/systemd/system/wayland-computer-use-mouse.service`
and `/etc/modules-load.d/wayland-computer-use.conf`. Removing this plugin does not
remove those files or the ydotool package automatically. No stock Omarchy or OpenAI
runtime files are modified.

## Development

Run `python3 -m unittest discover -s tests`. Unit tests cover protocol handling,
argument validation, rotated/scaled monitor mapping, stale focus/frame rejection,
literal text transport, and releasing a drag after a failure. These do not replace
live input testing. `python3 tests/live_input.py` is an opt-in test that launches
a disposable native Wayland GTK4 window and exercises input through the configured
MCP server. It requires python-gobject and GTK4, moves the real pointer, and changes
keyboard focus. Run it only with the desktop user's permission.

Use `--monitor DP-1 --skip-wheel` or `--monitor HDMI-A-1 --skip-wheel` for the
native input checks while the GTK wheel discrepancy is under investigation.
`python3 tests/browser_scroll.py --monitor DP-1` tests actual content scrolling
in Chromium with a temporary profile; substitute another connected monitor name.
Without `--skip-wheel`, the native test retains the failing wheel check so the
limitation is not silently hidden. Unit tests currently total 25.
`python3 tests/smoke.py --clean-session` removes all four session variables before
launching the MCP server, then verifies actual inspection and screenshots. This
passes with desktop socket access; it intentionally cannot bypass a restricted
host that denies socket connections.
