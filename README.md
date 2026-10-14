# Wayland Computer Use

Two MCP servers that let an AI agent observe and operate a Linux Hyprland/Wayland desktop with guarded input.

Focus changes and partial input make desktop automation hard to verify. The input server rechecks its target before each segment, records submitted actions, and never replays them automatically. A separate observer uses accessibility data and changed-region images where available.

**Proof:** 27/27 paired desktop task artifacts passed; batching cut median task time 37–47% ([results](reports/2026-09-08-wcu-braid-implementation-results.md), [raw data](reports/benchmarks/wcu-braid-20260908/desktop-e2e.json)). These are deterministic local pipelines, excluding model inference and approval time.

**Works with:** Claude Code and Codex. Claude Code 2.1.296 passed a read-only MCP call; input, images and approvals were not tested in that version. [Client setup and test scope](#use-with-claude-code).

**Part of:** the agent runtime alongside [Heimdall](https://github.com/algorhythmic/heimdall) and [Braid](https://github.com/algorhythmic/braid). Runs independently; Braid is an optional context backend and Heimdall integration is not delivered.

## Quick start

Needs an unlocked Hyprland session, Python 3, `hyprctl` and `grim`; input also needs `wtype` and `ydotool`.

```bash
git clone https://github.com/algorhythmic/wayland-computer-use.git
cd wayland-computer-use
python3 scripts/configure_mcp.py  # prints client config; writes nothing
```

Follow [Setup](#setup) to publish the runtime and configure your client.

## Status and limits

Hyprland only. A community project, not an official OpenAI implementation. Guards reduce focus races but cannot make input atomic. Benchmarks use one machine and do not measure general agent performance. Local development suite: 184 passed, 7 skipped; [validation details](#validation-and-benchmark-limits) below.

---

## Use with Claude Code

Tested on October 14, 2026 with Claude Code 2.1.296 on the development machine,
against a locally published development runtime (`c618243`), ahead of the public
main branch at the time of this check. Write the `wayland` entry printed by
`configure_mcp.py` to a file, for example `wcu-mcp.json`. The tested file kept
`command`, `args` and `env`, and omitted the Codex `startup_timeout_sec` field:

```json
{"mcpServers": {"wayland": {"command": "/usr/bin/python3",
  "args": ["/absolute/path/to/wayland-computer-use/scripts/dev_host.py"],
  "env": {"PYTHONDONTWRITEBYTECODE": "1"}}}}
```

Load only that server and allow only the read-only `desktop_state` tool:

```bash
printf '%s' "Call mcp__wayland__desktop_state once and report tool_contract_version." |
  claude -p --strict-mcp-config --mcp-config wcu-mcp.json --tools "" \
    --setting-sources "" --no-session-persistence \
    --allowedTools mcp__wayland__desktop_state
```

What this established: the `wayland` server connected and exposed all 19 tools. The
model made one `desktop_state` call, which returned without error and reported
`wcu-tools-5`. A separate config for `wayland-desktop-observer/scripts/observer_server.py`
connected and listed its 4 tools; none was called. What it did not establish: input,
image delivery, approval prompts, or a full desktop task in this Claude Code version.
On September 7, a Claude Code session drove an earlier build through four
model-driven desktop task trials. Each trial finished, but each left a corrupted
source line ([results](reports/2026-09-07-browser-obsidian-ab/results.md)).

## Components

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

[September 8 implementation and E2E results](reports/2026-09-08-wcu-braid-implementation-results.md):
guarded execution, bounded local context, optional Braid, paired desktop tasks,
runtime activation evidence, and measured limitations.

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
`type_text`, `press_key`, `scroll`, `drag`, `run_steps`, `observe_window`,
`wait_for`, `read_text`, `view_frame`, `open_uri`, `obsidian_create_note`,
`obsidian_note_status`, optional `cdp_read`, `stop_observing`, and read-only
`context_for_task`. Input returns scoped evidence
and a deferred target frame by default. `images` controls delivery independently
of guard capture: `none` (default), `on_failure`, `target`, or explicit `monitor`.
`view_frame` delivers retained pixels only on request, at half size by default;
`detail: "original"` requests full resolution. Coordinates map back to original
guard pixels, and viewing never extends the 120-second expiry. Without `after`,
a bounded quiescence wait checks for 100 ms of stable pixels (up to 1500 ms). A screenshot
alone does not establish task completion; use an explicit outcome condition.
Captures use raw RGB internally and encode lossless PNG at delivery. The optional
persistent helper uses wlr-screencopy on untransformed scale-1 outputs; other
configurations use raw `grim`. A frame records capture time, target identity,
layout and visible pixels, and expires after 120 seconds from capture start.

The public `wcu-tools-4` contract (extended by `wcu-tools-5` in the local
development build tested above) pins the intended target before every input segment,
reports partial submission and outcome verification in a recovery ledger, and
shares a validated duration budget across sequence steps (60 seconds by default,
120 seconds maximum). An explicit matched-window transition is required to adopt
a newly opened target. These guards reduce focus races; they cannot make input
atomic with compositor queries or prove that an app consumed submitted events.
Target and region result views retain capture provenance, including on rejection.
An overlay or changed target reports `overview_required`; monitor overview must
be requested explicitly. Scoped accessibility, focused-control evidence, bounded non-protected
text readback and readiness predicates support checks inside predictable batches. `wait_for`
and `read_text` steps allow paste → exact readback → save in one call.
See the [execution and context contract](skills/wayland-computer-use/references/execution-and-context.md).

Approval dialogs can steal focus. With `restore_focus:true`, input names the
observed window/title, validates it, restores it once, rechecks, and acts within
one approved operation. Unexpected changes reject the action; there are no blind
retries. Non-scroll input also uses a noise-tolerant pixel guard, stricter near
the action. It is not semantic UI recognition: animation may still be rejected
and subtle changes may pass. A small caret-shaped diff is ignored only with
unchanged, complete AT-SPI focus/offset/bounds evidence; other bars or status
updates do not automatically pass.

The observer exposes `observe`, `wait_for_change`, `wait_for`, and `stop_observing`.
An interruptible pipe, filtered Hyprland events, and a persistent AT-SPI worker
wake a sampler; periodic sampling reconciles missing events. An initial window
crop is followed by versioned metadata/accessibility changes and a bounding crop
of changed tiles. A renewable 120-second lease bounds collection; explicit stop
clears retained history. `wait_for_change` detects **any revision change**;
`wait_for` evaluates an explicit accessible-name, window, or changed-region
condition. Neither infers task success from a repaint. `channels` selects what is
collected; `images` controls delivery and defaults to false on the standalone
observer. Accessibility deltas upsert changed nodes and remove listed refs without
repeating the preceding tree. Freshness can require collection after a
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

## Feedback and application surfaces

[Verified Obsidian transfers](docs/obsidian-transfers.md) provide structured note
creation with exact encoding, durable operation receipts, and saved-content checks.
Retries and reconnects reconcile the same operation without creating duplicates.

[Outcome-first feedback contract and verification](docs/feedback.md) describes the
new defaults, readback steps, trace counters, migration and remaining limits.
`open_uri` dispatches bounded Obsidian open/new and http(s) URIs through the
registered handler. It does not claim the app accepted or saved the content.
Optional `cdp_read` requires an existing loopback debugging browser,
`WCU_CDP_PORT`, and Python `websockets>=15`. It reads bounded DOM text/links without
script evaluation. No browser configuration is changed by either tool.

## Development

[Keyboard environment context](skills/wayland-computer-use/references/environment-context.md)
provides versioned shortcut catalogs and a read-only collector for planning.
The DaVinci Resolve 21.1 catalog includes 248 reviewed core Linux bindings with
manual-page provenance and panel-specific conditions; installed/user presets
remain unverified. [Coverage and lookup examples](skills/wayland-computer-use/references/keyboard/davinci-resolve.md).
`python3 scripts/keyboard_context.py refresh --vault /path/to/vault` writes full
local tables and searchable JSON under ignored `.dev/keyboard-context/`.
Each catalog states its completeness boundary; current compositor bindings and
vault overrides are distinguished from application source defaults.

Refresh also atomically publishes an immutable normalized snapshot. The read-only
`context_for_task` operation ranks only after applicability checks, attaches exact
required context from that snapshot, and caps its canonical JSON payload in UTF-8
bytes. Caller facts remain claims; unknown prerequisites produce exploration
references. Source-default shortcuts require live availability evidence before
they can become executable recommendations.

```bash
printf '%s\n' '{"intent":"enter URL","max_bytes":8192}' | python3 scripts/context_for_task.py
python3 scripts/benchmark_context.py --output .dev/context-benchmark.json
python3 scripts/benchmark_handoff_e2e.py --trials 3 --output .dev/desktop-benchmark.json
```

The desktop benchmark launches disposable GTK and Chromium windows and verifies
saved artifacts. It measures deterministic task pipelines, excluding model and
approval time. Braid stays opt-in: set `WCU_BRAID_EXECUTABLE` and its exact
`WCU_BRAID_SHA256`, then request `backend: "braid"`. WCU owns a dedicated local
dataset, uses lexical-only ranking, verifies revisions and dependency closure,
and retains local fallback. Run real-process conformance with
`WCU_BRAID_TEST_BIN=/absolute/path/to/braid` in the test/publish environment.
No Braid process or network access is required for the default local backend.

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
entry point. The original sibling observer checkout remains independent; continue
source development in this repository. Installed plugin activation is recorded
separately from publication and existing-chat tool discovery in release evidence.

Licensed under [Apache License 2.0](LICENSE).


## Validation and benchmark limits

- **Hyprland only.** Developed on Omarchy. This is a community project, not an
  official OpenAI computer-use implementation, and it does not support every
  Wayland compositor.
- **The benchmarks are narrow.** They are local, deterministic task pipelines on one
  machine and shared desktop: three trials per task and variant for the paired
  tasks, and six trials on one GTK fixture for change detection. They exclude model
  inference and approval time. They are not end-to-end agent benchmarks or token
  measurements. The change-detection comparison is between two pipelines, not
  equal-sized encoders, and depends on usable accessibility data.
- **Guards reduce focus races but do not make input atomic.** In a natural
  asynchronous focus race, 40 characters reached a decoy window before the next
  segment guard rejected the rest. Zero wrong-window input is not claimed.
- **The Braid backend has no measured quality advantage.** On eleven labeled cases it
  matched local retrieval quality but was slower. Local retrieval is the default.
- **Tests:** on October 14, 2026, `python3 -m unittest discover -s tests -v`
  on local development commit `c618243` with uncommitted evaluation changes
  ran 191 tests: 184 passed and 7 optional Braid tests skipped. The temporary Unix
  socket fixture requires a host that permits local socket binding. The September
  10 record of 167 input tests applies to an older build. The 191-test run is
  development evidence, not a fresh test result for the public main branch.
- **Approvals belong to the MCP client.** Read-only does not mean privacy-free or
  automatically approved.

## License

[Apache License 2.0](LICENSE).
