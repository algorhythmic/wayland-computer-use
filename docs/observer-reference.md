# Wayland Desktop Observer — experimental v0.1

Historical pre-consolidation reference. Relative code paths and commands below
are relative to `wayland-desktop-observer/`, except the now root-level benchmark
directory. Use the [main README](../README.md) for current setup.

Separate, read-only infrastructure for developing a richer state channel beside
Wayland Computer Use. The working plugin, its installation, its input daemon and
its frame/approval guards are not modified. This prototype is not installed into
Codex and does not publish to the existing development host.

## Implemented

- `observe`: desktop window/monitor/focus metadata; optionally the currently
  focused window's composited crop and a bounded AT-SPI probe.
- `wait_for_change`: wait up to 30 seconds for any revision change. Animated pixels
  can complete this wait; it does **not** mean a requested task has finished.
- `stop_observing`: suspend collection and clear retained history. An in-flight
  read can finish, but its result is discarded.
- A background thread listens to Hyprland's event socket while a scope is active,
  with periodic sampling to detect pixel changes. A 120-second lease, renewed by
  observation calls, suspends collection when the model stops requesting it.
- Revisions change only when observed state changes. Deltas compare against the
  caller's actual revision, not just the immediately previous sample. Unknown,
  evicted, restarted, or different-scope revisions return a full snapshot.
- A first window observation returns an overview. Later responses return an exact
  changed-tile bounding crop with its source revision, dimensions and origin.
  Omit `since_revision` to request another overview. `images:false` suppresses
  image delivery, not local capture, when a window scope is selected.
- Evidence includes collection timestamps, freshness, source and explicit
  unavailable/partial states. Recent samples record visual change without
  interpreting its meaning. No OCR, image-model inference or audio analysis.

## Isolation and dependencies

`baseline/` holds frozen copies of the installed server, its 28 server tests, and
reference documentation. `baseline/PROVENANCE.json` records SHA-256 checksums and
the source directory. The observer imports utility functions and session discovery
from this frozen server. It never calls `Desktop.call` or exposes its input tools.
No shared frame IDs, hot-reload pointers, screenshot caches, or debug directories.

Required: Python 3, Hyprland, grim and ImageMagick (`magick`). Optional: PyGObject
and the Atspi 2.0 GI typelib. A missing, stalled or inaccessible AT-SPI service is
reported as unavailable; each probe runs in a subprocess with a two-second timeout.
No new packages, services, marketplace entries or desktop settings are installed.

The MCP entry point is `scripts/observer_server.py`, with separate server name
`wayland_observer` in `.mcp.json`. The config points to this development directory;
update that path if moving it. A host must allow access to desktop Unix sockets.
The current chat has not been connected to this new MCP server.

## Tool examples

First enumerate desktop metadata (no screenshots/accessibility reads):

```json
{"name":"observe","arguments":{"images":false}}
```

Use a real window address from that response:

```json
{"name":"observe","arguments":{"window":"0xADDRESS"}}
```

Then use the returned revision and the **same window scope**:

```json
{"name":"observe","arguments":{"window":"0xADDRESS","since_revision":"EPOCH:1"}}
{"name":"wait_for_change","arguments":{"window":"0xADDRESS","since_revision":"EPOCH:2","timeout_ms":1000,"images":false}}
{"name":"stop_observing","arguments":{}}
```

One server tracks one scope at a time. Omitting `window` selects desktop metadata,
not the previously selected window. Changing scope resets revision history.
`wait_for_change` reports `wait_timed_out`; `fresh_sample:false` and `age_ms` identify
responses returned before a new sample completed. Backend errors become explicit
state, never old screenshots represented as fresh. Collection is not atomic.

## Coordinate and identity contract

Window IDs survive observed title/geometry changes and change after an observed
removal or PID change. They are local to this observer process. Window close/reopen
cycles entirely between samples can be missed; do not treat IDs as capabilities.
Event payloads currently wake a rescan rather than forming a lossless event journal.

Images use logical scale 1. `box_in_window_crop` is `[left, top, right, bottom]`,
exclusive at the right/bottom. `desktop_origin` gives the returned image's logical
desktop origin. Crops include only the target rectangle clipped to its assigned
monitor. Windows spanning monitors are therefore incomplete. No image downscaling
or noise tolerance is used. The four-million-pixel limit yields unavailable state
for oversized targets. One bounding crop can still be large if distant tiles change.

Accessibility matches an application PID and a unique top-level window title.
It returns up to 200 objects and 12 levels, marking truncated trees partial. Names
are capped at 160 characters; protected-entry names/values are redacted and text
contents are not queried. Tree refs are **revision-local**, not stable control IDs.
Reported bounds are AT-SPI window-relative and unverified for input. Missing or
partial accessibility must not be interpreted as evidence that controls vanished.

These are observation revisions, **not original-plugin `frame_id`s**. All input
still requires a fresh original-plugin screenshot and its existing validations.
The observer never focuses a window or injects input. It stops window capture when
the target is unfocused, missing or `hyprlock` is running. Focus/layout changes
during capture discard the image and accessibility evidence. Other lock detection
and complete occlusion analysis are not implemented. A composited crop can include
overlays, menus and transparent background content. This is not app isolation.

Memory holds at most six RGB snapshots (at most about 72 MB of raw pixels), 60
sample records and transient capture/encoding buffers. No screenshots or UI text
are written to disk by the observer. Lease expiry suspends collection but retains
bounded history; `stop_observing` clears it. The MCP host may retain returned data.
UI content remains untrusted task data, not permission or instructions.

## Validation

The first same-task comparison is recorded in
[benchmarks/COMPARISON.md](../benchmarks/COMPARISON.md), including raw samples,
methodology and limits. Run `python3 scripts/benchmark.py` to repeat it with a
disposable GTK status window. This measures local tool performance, not model
inference latency.

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s baseline/tests -v
```

Read-only live MCP smoke test, on an unlocked desktop with socket access:

```bash
python3 scripts/live_smoke.py
```

This observes the current focused window without moving focus or injecting input,
tests discovery, snapshots, deltas, bounded waits and stop, and prints metrics only.
Focus changes by the user can legitimately make capture unavailable.

Initial local validation on 2026-09-05: 18 new tests and 28 frozen baseline tests
passed. A direct live check returned the first 961×701 focused-window crop in
about 271 ms and a changed-region crop on the next revision. This is one local
measurement, not a model-latency benchmark. Hyprland event socket connected.
HUSH exposed 41 accessible nodes. OBS registered an AT-SPI application but exposed
zero top-level windows in this session; no OBS configuration was changed.

## Next implementation stages

1. Benchmark token/image volume and latency across static settings, downloads and
   moving meters. Add a controlled GUI fixture for missed-transition testing.
2. Add AT-SPI event subscriptions and reconcile window lifetimes using compositor
   event payloads; retain periodic resync for dropped events.
3. Add explicitly scoped wait predicates (specific value, dialog appearance),
   independent of unrelated animation. No follow-on actions chosen by the server.
4. Improve image delivery with multiple changed regions and periodic overviews;
   add stable control identity only where evidence supports it.
5. Evaluate target-specific input validation in a separate stage after coverage
   and race tests. Do not weaken the existing guards to accommodate this prototype.

API references: [Hyprland IPC](https://wiki.hypr.land/IPC/),
[AT-SPI Accessible](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/class.Accessible.html),
[AT-SPI Component bounds](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/method.Component.get_extents.html).
