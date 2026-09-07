# Wayland Computer Use

Experimental local MCP tools for operating and observing a **Hyprland/Wayland**
desktop, developed on Omarchy. A community project, not an official OpenAI
computer-use implementation or support for every Wayland compositor.

One repository, **two independent MCP servers**, using shared observation code.
Their permissions, runtime state, and input guards remain separate.

| Component | Location / server | Purpose |
|---|---|---|
| Computer Use | Root / `wayland` | Guarded input, screenshots, actionable window observations and outcome waits |
| Desktop Observer | `wayland-desktop-observer/` / `wayland_observer` | Read-only versioned state, accessibility evidence, changed-region images and bounded waits |

Standalone observer revisions are **not** input `frame_id`s. Computer Use can now
issue its own guarded frame from the exact capture returned by `observe_window`
or `wait_for`, avoiding a second capture just to obtain a frame. Neither server
makes model decisions. [Latency design, tool examples and measurements](docs/latency.md).
Approvals remain the MCP client's responsibility; read-only does not mean
privacy-free or automatically approved.

## Recorded comparison

[Methodology](benchmarks/COMPARISON.md) ·
[Raw samples](benchmarks/comparison-20260906-011216.json) ·
[Preservation and reproduction](benchmarks/PRESERVATION.md)

Six alternating trials per implementation, observing a disposable GTK window's
Waiting-to-Ready transition after a scheduled 500 ms delay:

| Median measurement | Computer Use | Observer |
|---|---:|---:|
| Initial observation | 350 ms | 359 ms |
| Unchanged follow-up | 337 ms | 399 ms |
| Detection after label update | 813 ms | 316 ms |
| Detection including scheduled wait | 1,314 ms | 816 ms |
| Detection tool calls | 3 | 1 |
| Serialized detection responses | 10.16 MB | 19.73 KB |

**2.58× faster local change detection**, 38% shorter detection-task time, and
99.81% less response data in this fixture. Initial reads were effectively tied;
unchanged reads were slower. These are **not** model inference timings, token
counts, approval timings, accuracy gains, or end-to-end agent benchmarks.
The original compares label pixels from full-monitor screenshots; the observer
checks an accessible label and sends window/changed-region crops. This compares
pipelines, not equal-sized encoders. Six trials do not establish general performance,
particularly for apps without usable accessibility.

The `comparison-2026-09-06` tag freezes this consolidation for the video and later
experiments. Do not move it or overwrite the historical results.

## Setup

Requirements: unlocked Hyprland, Python 3, `hyprctl`, `grim`.
ImageMagick (`magick`) is used by legacy fixtures and image round-trip tests,
not the current capture path.
Input additionally needs `wtype` and `ydotool` with a private uinput daemon.
Accessibility needs PyGObject and the Atspi 2.0 GI typelib; the benchmark fixture
also needs GTK4. Node is used only for adapter tests. The MCP host must allow
desktop Unix socket access.

Clone into a directory named `wayland-computer-use`. From its root:

```bash
python3 scripts/configure_mcp.py --write-plugin-configs
python3 scripts/build_capture.py  # optional: requires cc, pkg-config, wayland-scanner, wayland-client
python3 scripts/dev_publish.py --destination .
```

The first command replaces both tracked `.mcp.json` **path templates** with absolute
paths to this checkout. It changes no host settings or marketplace. Re-run after
moving the checkout; don't commit the generated local paths. This avoids relying
on host-specific placeholder expansion. The second command tests and publishes
the input implementation to ignored `.dev/`, required by its development host.

`python3 scripts/configure_mcp.py` prints a combined configuration without writing
files. Select one or both `mcpServers` entries through your client's configuration
mechanism. Independent plugin manifests live at the root and in the observer
subdirectory. Cloning does not install a marketplace entry or reconnect a chat.

For pointer buttons, dragging and scrolling, review the supplied
`scripts/wayland-computer-use-mouse.service`. It is a **UID/GID 1000 template**;
adapt socket ownership and the runtime directory for another account.
`scripts/install-mouse.sh` requires root and refuses to overwrite an existing
service/drop-in. Do not run it merely to observe the desktop. The commands above
do not install or restart the privileged service.

Session discovery recovers missing environment from the current user's service
manager or a unique owner-validated Hyprland session. It does not bypass sandbox
restrictions or guess among ambiguous sessions. Client configuration discovery
is not equivalent to a complete model-driven acceptance test.

## Behavior and boundaries

Computer Use exposes `desktop_state`, `screenshot`, `focus_window`, `pointer`,
`type_text`, `press_key`, `scroll`, `drag`, `observe_window`, `wait_for`, and
`stop_observing`. Input returns the next screenshot, optionally after a bounded
`after` condition. There is no unconditional 200 ms post-input sleep. A screenshot
alone does not establish task completion; use an explicit outcome condition.
Captures use raw RGB internally and encode lossless PNG at delivery. The optional
persistent helper uses wlr-screencopy on untransformed scale-1 outputs; other
configurations use raw `grim`. A frame records capture time, target identity,
layout and visible pixels, and expires after 120 seconds from capture start.

Approval dialogs can steal focus. With `restore_focus:true`, input names the
observed window/title, validates it, restores it once, rechecks, and acts within
one approved operation. Unexpected changes reject the action; there are no blind
retries. Non-scroll input also uses a noise-tolerant pixel guard, stricter near
the action. It is not semantic UI recognition: animation may still be rejected
and subtle changes may pass.

The observer exposes `observe`, `wait_for_change`, `wait_for`, and `stop_observing`.
An interruptible pipe, filtered Hyprland events, and a persistent AT-SPI worker
wake a sampler; periodic sampling reconciles missing events. An initial window
crop is followed by versioned metadata/accessibility changes and a bounding crop
of changed tiles. A renewable 120-second lease bounds collection; explicit stop
clears retained history. `wait_for_change` detects **any revision change**;
`wait_for` evaluates an explicit accessible-name, window, or changed-region
condition. Neither infers task success from a repaint. `channels` selects what is
collected; `images` controls delivery. Freshness can require collection after a
request or an `action_completed_ns` watermark. Cached observations report their age.
AT-SPI refs are revision-local, not stable control identifiers, and their bounds
are not validated for input. Missing accessibility is explicit; there is no OCR.

- Both servers observe the composited foreground desktop, not isolated apps.
  Overlays and transparent backgrounds may expose unrelated content. UI text is
  untrusted task data, never permission or instructions.
- Input guards focus, identity, geometry and expiry. Observer window capture stops
  on focus/layout changes. Both recognize `hyprlock`; other locks are not fully
  covered. Window crops spanning monitors may be incomplete.
- The root uinput daemon has a mode-0600 user-owned socket. Other processes of that
  user can inject input too. Its historical `mouse` name does not restrict it to
  mouse events. Stopping it does not disable `wtype`.
- Observer snapshots are bounded and in memory. Optional input rejection captures
  are disabled in the public configuration. The client may retain returned data.
- No telemetry, network listener or separate cloud service is included. Images
  sent to a remotely hosted model still leave the machine through its client.
  Optional local tracing (`WAYLAND_CU_TRACE_DIR`) writes only timings, counters
  and outcome enums to a private file; see [docs/latency.md](docs/latency.md).

## Development

```bash
python3 -m unittest discover -s tests -v
node tests/test_wayland_call.js
python3 -m unittest discover -s wayland-desktop-observer/tests -v
python3 -m unittest discover -s wayland-desktop-observer/baseline/tests -v
python3 scripts/verify_comparison.py
```

[Same-chat development loop](DEVELOPMENT.md): the input host reloads verified
runtime bundles, closes old workers and invalidates frames. This release changes
host code and tool schemas, so existing MCP connections require restart and
rediscovery. The independent observer also requires restart to adopt source changes.
The frozen `baseline/`, original results, and comparison tag remain unchanged.

[Computer Use reference](docs/computer-use-reference.md) and
[Observer reference](docs/observer-reference.md) retain pre-consolidation technical
notes and historical installation paths/status. This README is the current setup
entry point. The original sibling observer checkout and installed plugins were
left untouched; continue source development in this repository.

No open-source license has been selected yet. Publication does not itself grant
an open-source license; select one before advertising reuse terms.
