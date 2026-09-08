# Observability and latency optimization passes, September 7, 2026

This document records, in order, everything done on September 7, 2026 to the
Wayland Computer Use repository: the evaluation of the September 6 browser-to-
Obsidian reports, the diagnosis of the broken MCP connection, the observability
implementation, and three latency optimization passes that used it. It names
the commits, benchmark files, tests and documentation involved so each step can
be reproduced or narrated.

All timings are local stage timings on one Hyprland desktop (Omarchy, Linux
7.1.9, Python 3.14.7, six CPUs). They exclude model inference, approval review,
client image handling and network transit. None of them is an end-to-end agent
benchmark.

## 1. Starting point

The repository held two MCP servers sharing `scripts/cu/`: the input server
(`scripts/server.py`, tools such as `screenshot`, `pointer`, `type_text`,
`observe_window`, `wait_for`) and the read-only observer
(`wayland-desktop-observer/scripts/observer_server.py`). The previous day's
commit `4a2b34c` had added raw RGB capture, an optional native screencopy
helper, a persistent AT-SPI worker and bounded outcome waits, documented in
`docs/latency.md`.

An untracked `reports/2026-09-06-browser-obsidian/` directory contained a
retrospective of a 12 minute 32 second agent run, a proposal, and an evaluation
of whether the available evidence could support latency work:

| File | Content |
|---|---|
| `retrospective.md` | 752.586 s run; 47 orchestration calls, 54 operations, 34 input attempts, 13 guard rejections, 37 full-monitor screenshots (77.35 MB PNG), one corrupted long text insertion repaired outside the plugin. Time split: 71.5% model request spans, 9.5% automatic approval, 8.6% other tool time, 10.4% unattributed host/client time. |
| `proposal.md` | Six work items: verify the build under test, fix long text insertion, return ready observations to cut rejections, reduce full-image delivery, optimize approval overhead, and finish the measurement pipeline before claiming a speedup. Acceptance budget of 360 s, 25 cycles, 20 MiB of images. |
| `observability-evaluation.md` | Judged the evidence sufficient for prioritization but insufficient for operation-level diagnosis. Found four concrete defects in the code's timing contract and set P0/P1/P2 gates. |
| `evaluate-observability.py`, `observability-evaluation.json` | 46 consistency checks over the exported ledger plus four mocked probes of `Desktop.call`, with a SHA-256 manifest of every file evaluated. |
| `tool-calls.csv`, `phases.csv`, `metrics.json` | The 47-row call ledger, eight task phases, and aggregate metrics with provenance. |

### The evaluation's four defects, verified against the code

1. **Timing vanished on the paths that mattered.** `Desktop.call` attached
   `total_ms` only on the completed-action return path. Guard rejections
   returned only the recovery screenshot's capture time. An input backend
   exception or a post-input capture failure returned no timing at all,
   although guard and input timings existed internally at that moment.
2. **The flat timing dictionary was not composable.** `guard_ms` contained
   focus restoration, capture, crop and comparison; `input_ms` contained
   rechecks; `wait_ms` contained collection; summing them double-counted.
3. **Capture stages were incomplete.** `Capturer.capture` started its timer
   after acquiring its lock, hiding contention. A raw `grim` fallback carried no
   reason when the helper was simply missing or the output was rotated.
4. **The benchmark could lose data.** `scripts/benchmark_latency.py` wrote its
   report only after every assertion passed, so a late failure discarded all
   earlier trials. It scheduled a 200 ms label change but never persisted the
   fixture's actual update time, so detection lag was inferred, not measured.

The evaluation's own numbers pointed at one unexplained residual: subtracting
the named stages from `collection_ms` in `benchmarks/latency-20260906-031607.json`
left 34.5 to 35.5 ms, about 36% of each collection.

## 2. MCP connection diagnosis

The `wayland` MCP server failed with "connection closed" at session start.
Two causes, both confirmed by launching the host by hand over stdio:

- The tracked `.mcp.json` still contained the placeholder path
  `/absolute/path/to/wayland-computer-use/scripts/dev_host.py`, so Python
  exited before speaking JSON-RPC.
- No runtime bundle had been published, so even the correct path failed at
  `initialize` with a missing `.dev/current.json`.

The documented setup fixed both: `scripts/configure_mcp.py
--write-plugin-configs` wrote absolute paths into both `.mcp.json` files,
`scripts/build_capture.py` built the native helper, and `scripts/dev_publish.py
--destination .` published a bundle. The host then initialized, listed all 11
tools and answered `desktop_state`. The ydotool daemon
(`scripts/install-mouse.sh`, root) was left uninstalled, so pointer, scroll and
drag remain unavailable while screenshots, observation and keyboard input work.
The two `.mcp.json` files carry machine-local paths and were never committed.

## 3. Observability implementation (commit `34a18ec`)

Thirteen files, 1,101 insertions. Every item below maps to a P0 gate in the
evaluation.

### 3.1 Request spans that survive every exit path

`Desktop.call` now creates a `Trace` for each request, binds it to the thread,
and always finishes through one method, `finish`, whether the call returned,
raised `ActionRejected`, or raised anything else. `finish` closes the root
span, derives the flat `timings_ms` dictionary, merges it into the first text
block of the response (including rejection and error responses), and writes a
sidecar record. Stages became context-managed spans: `guard`, `focus_restore`,
`visual_check`, `debug_write`, `input`, `wait`, `result`, `recovery`,
`observe`. Focus restoration is recorded separately from input because a
rejected input can still have restored focus.

The four mocked probes from the evaluation script, rerun on the new code:

| Path | Before | After |
|---|---|---|
| Successful text input | capture, encode, guard, input, total | plus `result_ms` |
| Guard rejection | capture, encode only | plus `guard_ms`, `recovery_ms`, `total_ms` |
| Input backend exception | nothing | `guard_ms`, `input_ms`, `total_ms` |
| Screenshot failure after input | nothing | `guard_ms`, `input_ms`, `result_ms`, `total_ms` |

### 3.2 An opt-in bounded trace recorder

New module `scripts/cu/trace.py` (210 lines). `Trace` keeps spans with parent
links, monotonic start and end nanoseconds, error types, and an `incomplete`
flag for spans still open at close, so exclusive time can be derived later by
subtracting the union of child intervals. `record_exec` attaches a leaf span to
every subprocess run through either command wrapper, carrying only the
executable's basename and exit code.

`Recorder` writes one JSON line per request when `WAYLAND_CU_TRACE_DIR` names
an absolute, user-owned, mode-0700 directory. The file is created 0600 and
capped at 64 MiB; further records increment a `dropped` counter, and
`desktop_state` reports `trace.complete` as false. A structural allowlist
(`scrub`) lets strings survive only under named keys such as `tool`, `status`,
`reason`, `argv0`, `capture_backend` and `fallback_reason`; typed text, key
chords, titles, URLs, clipboard and accessibility content can never reach the
file. Each record carries the span tree, the outcome (`ok`, `rejected`,
`error`; `action_performed` true, false or unknown; `focus_restored`), argument
shape such as `text_len` and `condition_kind`, response sizes, frame
provenance, guard metrics and wait outcomes. A process record at startup gives
a wall-clock/monotonic sync point, the boot id and source hashes.

### 3.3 Capture provenance

`Capture` gained `requested_ns`, taken before the lock, so frame metadata
reports `lock_wait_ms`. Fallbacks carry explicit reason codes:
`unsupported_output` (rotated or scaled), `helper_unavailable`,
`helper_cooldown`, `helper_failed`. Frame metadata also reports `png_bytes`.
Observation timings gained `lock_check_ms` around the compositor-lock probe
that had been hidden inside `collection_ms`.

### 3.4 Incremental benchmark output

`scripts/benchmark_latency.py` rewrites its JSON report atomically after every
trial with `status` `incomplete`, `complete` or `failed` and the failure text.
Wait trials persist the scheduled, applied, request and response monotonic
timestamps; `after_change_ms` measures detection lag from the fixture's actual
label update (its `perf_counter`, the same `CLOCK_MONOTONIC` domain) and a
clock-domain sanity assertion guards the conversion. Each report records the
checkout git revision, the published bundle revision, whether the native
helper was present, and per-trial accessibility event counts.

### 3.5 Tests and documentation

Eleven tests were added: `tests/test_trace.py` (span nesting and error
closure, incomplete marking, thread isolation, opt-in default, private
directory requirement, scrubbing and dropped-record accounting) and additions
to `tests/test_server.py` (timing retained on guard rejection, on partial input
failure, on read-only calls, and a sidecar record test that asserts the typed
text never appears in the file) and `tests/test_pixels_capture.py` (fallback
reason codes and lock wait). `docs/latency.md` gained the section "Tracing and
attribution"; `docs/computer-use-reference.md` and `README.md` mention the
environment variable.

### 3.6 Verification at the time

A traced run against the published host recorded three requests, kept timing on
a rejected stale-frame call, and contained no typed text. The evaluator's 46
checks still passed. The first benchmark of the day,
`benchmarks/latency-20260907-121059.json`, became the baseline for everything
after it. Its new `lock_check_ms` field read 38.6 ms, identifying the residual
the evaluation could not attribute.

The reports directory was committed separately as `389f56c` so it could be
dropped independently.

## 4. First latency pass (commit `ce1ae1f`)

Twelve files, 1,107 insertions. Method: one factor at a time, each measured with
the benchmark and a traced screenshot before the next change. Every guard and
its order was unchanged; only the mechanism behind a check changed.

### 4.1 Lock check: 41 ms to 5 ms

`pgrep -x hyprlock` cost 38 to 41 ms per call on this machine (463 processes)
and ran up to six times per input action. `cu.system.desktop_locked` reads
`/proc/*/comm` for the same predicate in about 5 ms with no subprocess, and
fails closed (reports locked) if `/proc` cannot be listed. It records a
`lock_check` span. Tests: locked desktop blocks capture and input without any
subprocess; the predicate matches the test's own process name and fails closed
when listing raises. Benchmark `latency-20260907-122446.json`: detection lag
104 ms to 18 to 38 ms, validation 46 ms to 14 ms.

### 4.2 Compositor queries: 4.1 ms to 0.05 ms

Read-only `monitors`, `activewindow` and `clients` queries now go over
Hyprland's owned IPC socket (`cu.system.hypr_query`, request `j/<name>`) and
return byte-identical JSON. Dispatch and instance discovery still spawn
`hyprctl`; a socket failure falls back to it. Recorded as `ipc` spans. Test: a
fake Unix socket server verifies the request bytes, the span, rejection of a
non-alphabetic name, and the fallback path. Benchmark
`latency-20260907-122659.json`: metadata 13 ms to 0.6 ms. That run's fixture
opened on the rotated monitor, so its capture rows carry `unsupported_output`
and are excluded from capture comparisons; the wait rows remain valid.

### 4.3 PNG encoding: 97 ms to 29 ms

Rejected first: zlib `Z_RLE` and `Z_HUFFMAN_ONLY` were 7 to 10 ms faster but
35 to 44% larger; a pure-Python PNG Sub filter cost 685 ms per frame. Adopted:
row data at or above 2 MB is deflated as up to four independent raw-deflate
members joined into one zlib stream, each ending on a byte boundary with a
sync flush and the last finishing the stream, with the Adler-32 of the whole
input appended. This is the technique pigz uses; zlib releases the GIL so the
members compress concurrently. Output is lossless, decodes with any inflater,
and grows by about 0.01%. Test: a forced multi-stream image round-trips through
`zlib.decompress` and through ImageMagick. Benchmark
`latency-20260907-122846.json`: native fixture capture plus encode 27.8 ms to
14.8 ms.

### 4.4 Results of the first pass

| Measurement (DP-1, 2560×1440 unless noted) | Before | After |
|---|---:|---:|
| Compositor-lock probe per call | 38–41 ms | 5 ms |
| Compositor query per call | 4.1 ms | 0.05 ms |
| Full-monitor PNG encode | 95–98 ms | 29 ms |
| `screenshot` tool total | 229 ms | 71 ms |
| `desktop_state` total | 13 ms | 1.2 ms |
| Observation validation stage | 46 ms | 5–9 ms |
| Native fixture capture plus encode (1261×688) | 27.8 ms | 14.8 ms |
| Detection after label update, accessibility wait | 102–105 ms | 17–19 ms |

Documented in `docs/latency.md`, "Optimization pass, September 7, 2026".

## 5. Second latency pass (commit `91c24af`)

Eleven files, 595 insertions. The trace pointed at two remaining stages.

### 5.1 The pixel guard: 640 ms to 20 ms

The largest finding of the day. `pixel_difference` in `scripts/server.py`
looped over every pixel in Python computing per-channel deltas, the changed
count, the maximum delta and a bounding box. On a 1261×688 window crop that
took about 640 ms, and a guarded input runs it twice (full crop, then the
action region). It had never appeared in any measurement because the old build
reported only the undifferentiated guard envelope, and the fixture benchmarks
never exercised input.

The replacement keeps every metric exact:

- Byte XOR of the two crops (big-integer XOR, C speed) marks changed bytes; a
  per-pixel indicator is the OR of the three channel bytes taken by strided
  slicing. Count comes from `bytes.count`, the bounding box from `lstrip` and
  `rstrip` per row.
- If the changed count is at most 20,000 pixels, the only regime in which the
  guard could still accept, deltas are computed per changed pixel found with a
  regex over the indicator.
- Otherwise the exact maximum channel delta comes from 16-bit lane arithmetic:
  each byte is placed in the low half of a 16-bit lane by slice assignment into
  a zeroed buffer, the lanes are read as one big integer, `(A | 0x8000) - B`
  per lane cannot borrow across lanes, and a mask derived from the guard bit
  selects `a-b` or `b-a`. The maximum is found by binary search using
  `bytes.translate` with a deletion table, avoiding a regex character-class bug
  found in the prototype when the threshold byte was `]`.

Tests: 400 random crops compared against the original loop under both
regimes, plus every delta value that is an ASCII or regex special byte.
Measured on a 1261×688 crop: unchanged under 1 ms, one pixel or an 18-pixel
caret 20 ms, fully changed 77 ms.

### 5.2 Accessibility worker lifecycle: 95 ms to 12 ms

The first accessibility probe cost 90 to 102 ms. Breaking it down: Python
startup 10.7 ms, `import gi` plus the Atspi typelib 87.7 ms, registry
connection 2.6 ms; a pre-spawned worker answered in 11.9 ms and a warm probe in
5.9 ms. The cost was the import, and the code killed the worker on every
channel change, focus loss or stop, then paid it again.

Changes in `scripts/cu/accessibility.py`, `accessibility_worker.py`,
`observation.py`, `server.py`, `dev_host.py` and `observer_server.py`:
`Accessibility.warm()` starts the worker without a probe; `unwatch()` sends a
one-line message that deregisters the application listeners but keeps the
imported process; `Collector.suspend()` replaces `close()` when a scope ends;
the observer worker thread warms on activity and reaps the worker after 120 s
without a scope; both servers and the development host call `Desktop.warm()`
at launch. Hung workers are still killed and replaced. A fake worker script
tests pre-start, unwatch idempotence and close; a fake collector tests warm on
activity and reap when idle; a host test checks legacy bundles without `warm`
still load. Measured through the plain server: first probe 12.3 ms, after a
pixels-only observation 8.6 ms. Through the development host: 14.2 ms once the
host also warmed at launch, which it had not in the first attempt.

### 5.3 Results of the second pass

| Measurement | Before | After |
|---|---:|---:|
| Guard pixel comparison, 1261×688, unchanged crop | 640 ms | <1 ms |
| Guard pixel comparison, one changed pixel or an 18-pixel caret | 640 ms | 20 ms |
| Guard pixel comparison, fully changed crop (rejected) | 640 ms | 77 ms |
| First accessibility probe after server start | 95 ms | 12 ms |
| Accessibility probe after a pixels-only observation | 95 ms | 9 ms |

Applied to the retrospective's 34 input attempts, the guard change alone would
have removed roughly 20 s from the 64 s non-approval tool envelope. Benchmark
`latency-20260907-124952.json` confirmed detection lag unchanged at 18 ms.
Documented in `docs/latency.md`, "Second pass, September 7, 2026".

## 6. Remaining candidates (uncommitted at time of writing)

Measured after the second pass; two changed, two left alone.

- **Lock scan** through raw descriptors (`os.open`/`os.read`) instead of file
  objects: 4.3 ms to 2.1 ms, four times per guarded input.
- **Changed-region tiling** (`cu.pixels.changed_box`) compared every 64-pixel
  tile row by row, 13 ms on a 1261×1390 crop with one changed pixel. It now
  compares whole rows first and tiles only rows that differ, scanning inward
  from each edge: about 1.2 ms for one pixel, a band or a full change, with the
  same tile-aligned box. Test: 400 random crops across five tile sizes against
  the full comparison.
- **Actionable-frame construction** after an observation is about 10 ms on a
  1261×1390 crop, almost all parallel deflate. Unchanged.
- **First-probe subscription** on a warm worker is about 12 ms of AT-SPI event
  registration. Unchanged.

Documented in `docs/latency.md`, "Remaining candidates, September 7, 2026".

## 7. Cumulative effect on the local stages

| Stage | Start of day | End of day |
|---|---:|---:|
| Full-monitor `screenshot` tool | 229 ms | 71 ms |
| Guard pixel comparison, per call | 640 ms | 20 ms |
| Compositor-lock check, per call | 41 ms | 2 ms |
| Compositor query, per call | 4.1 ms | 0.05 ms |
| First accessibility probe | 95 ms | 12 ms |
| Accessibility-wait detection lag | 104 ms | 18 ms |
| Observer changed-region box | 13 ms | 1.2 ms |

A guarded input previously spent on the order of 1.5 s in local stages
(two 640 ms comparisons, six 40 ms lock checks, ten 4 ms queries, a 229 ms
result screenshot). The same path now spends on the order of 150 ms plus the
input backend itself. These are stage timings; the retrospective showed that
model request spans and rejection loops dominate task time, so the paired
browser-to-Obsidian rerun in the proposal remains the evidence that matters.

## 8. Measurement discipline used throughout

- One factor per change; benchmark and trace before the next.
- Every guard, check and validation order unchanged; only mechanisms changed.
- Negative results recorded (zlib strategies, Python PNG filter).
- Non-comparable runs labeled rather than dropped (the rotated-monitor run).
- Randomized equivalence tests against the original implementation for every
  rewritten computation (pixel difference, changed box).
- No screen content, typed text or titles in any recorded artifact.

## 9. Reference index

**Commits (origin main):** `34a18ec` observability; `389f56c` reports;
`ce1ae1f` first pass; `91c24af` second pass. The remaining-candidate changes
are uncommitted.

**Benchmarks (`benchmarks/`):** `latency-20260906-030825.json` and
`latency-20260906-031607.json` (previous day, pre-observability);
`latency-20260907-121059.json` (baseline); `-122446` (lock check); `-122659`
(IPC, rotated monitor); `-122846` (parallel deflate); `-124952` (second pass).
`comparison-20260906-011216.json` is the preserved original comparison and was
not touched.

**Documentation:** `docs/latency.md` (runtime design, "Tracing and
attribution", three dated pass sections, validation); `docs/computer-use-
reference.md` (environment variables); `README.md` (setup and boundaries);
`DEVELOPMENT.md` (publish and reload loop); `benchmarks/COMPARISON.md` and
`PRESERVATION.md`.

**Reports:** `reports/2026-09-06-browser-obsidian/` as listed in section 1.

**Code:** `scripts/cu/trace.py`, `scripts/cu/system.py` (`desktop_locked`,
`hypr_query`), `scripts/cu/capture.py`, `scripts/cu/pixels.py` (`deflate`,
`changed_box`), `scripts/cu/accessibility.py` and `accessibility_worker.py`,
`scripts/cu/observation.py`, `scripts/server.py` (`Desktop.call`, `finish`,
`pixel_difference`, `max_channel_difference`), `scripts/dev_host.py`,
`scripts/benchmark_latency.py`, `wayland-desktop-observer/scripts/observer_server.py`.

**Tests:** `tests/test_trace.py`, `tests/test_server.py`,
`tests/test_pixels_capture.py`, `tests/test_dev_host.py`,
`wayland-desktop-observer/tests/test_observer.py`. Final counts: 66 and 30.

**Reproduce:**

```bash
python3 scripts/configure_mcp.py --write-plugin-configs
python3 scripts/build_capture.py
python3 scripts/dev_publish.py --destination .
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s wayland-desktop-observer/tests
python3 reports/2026-09-06-browser-obsidian/evaluate-observability.py
cd scripts && python3 benchmark_latency.py --focus-fixture
WAYLAND_CU_TRACE_DIR=/absolute/private/dir python3 scripts/server.py  # then drive over stdio
```

## 10. Suggested walkthrough order for the explainer

1. The 12-minute run and what its retrospective could and could not say.
2. The four timing defects, shown as the four probe paths before and after.
3. The trace recorder: a span tree, an allowlist, a byte cap.
4. The benchmark residual, and `lock_check_ms` naming it as `pgrep`.
5. First pass: three single-factor changes, three benchmark files.
6. The rotated-monitor run as an example of provenance catching a bad
   comparison.
7. Second pass: the 640 ms pixel loop nobody had measured, and the lane trick.
8. The accessibility import, and why lifecycle beat optimization.
9. The remaining candidates and the decision to stop fixture work.
10. What evidence comes next: the paired task rerun.

## 11. End-to-end pilot (added later the same day)

The paired trial the reports kept deferring to was run in the afternoon:
two pairs of the browser-to-Obsidian task, order B, A, A, B, same model
(this Claude Code session), same development host, same desktop, windows,
article and wording. The old build is the exact legacy release from
September 6, loaded by the same host via `scripts/select_runtime.py`; each
switch required an MCP reconnect because the host pins its tool contract. A
host-side envelope recorder in `dev_host.py` timed every call identically
for both builds. Full results: `reports/2026-09-07-browser-obsidian-ab/results.md`.

| Per-tool host envelope, median | Old build | Current build |
|---|---:|---:|
| `press_key` | 489 ms | 81 ms |
| `focus_window` | 434 ms | 106 ms |
| `drag` | 905 ms | 528 ms |
| `type_text` | 873 ms | 488 ms |
| `desktop_state` | 16 ms | 3 ms |
| Tool time per task | ≈5.5 s | ≈2.3 s |
| Wall time per task | 148–172 s | 171–295 s |

Three results for the explainer:

1. Local tool time per task fell about 2.4× with identical guards, but it is
   1 to 4% of wall time. The model's turns dominate, exactly as the
   retrospective said.
2. Removing the fixed 200 ms post-action sleep exposed a race: Obsidian
   retitles its window after Ctrl+N and Enter, the result capture is declined,
   and the agent spends an extra model turn on a screenshot. Two extra turns
   per trial outweigh the local savings. Remedy: retry the read-only result
   capture once on a title change, and use `after` window conditions.
3. The typed-text corruption reproduced in all four trials, on both builds,
   at the same positions. The pilot's first reading, a cap at the 37th
   distinct character, was wrong: the controlled text-entry fixture showed the
   cutoff is a keystroke count between 86 and 100 per `wtype` process,
   independent of time, newlines and key count, after which newly introduced
   characters are dropped. `type_text` now sends at most 40 characters per
   invocation; 100 of 100 consecutive proposal-corpus insertions were exact.
   The post-action capture also retries once on a mid-capture title change.

## 12. Deterministic sequences (evening)

The pilot's conclusion that turns, not tool time, dominate led to a batch
tool. `run_steps` takes up to twelve inputs; the first is guarded by the
reviewed frame like any single action, later steps act only after their
`expect` window or accessible condition holds or while the active window is
unchanged, each may wait on an `after` condition, and the sequence stops at
the first unmet condition and reports every step. Window conditions gained
`title_prefix` and `focused`. The skill document states the rule, a step
belongs in a sequence when its success can be checked without pixels, and
gives recipes for this machine's launch hotkeys and Obsidian.

Rerunning the pilot task with the seven post-copy steps as one sequence:

| | Trials 1 and 4, unbatched | Trials 5 and 6, batched |
|---|---:|---:|
| Calls per task | 14 | 4–5 |
| Screenshots | 10 | 3 |
| Response bytes | 27 MB | 7.7 MB |
| Tool time | 2.3 s | 1.9 s |
| Wall time | 171–295 s | 68–71 s |
| Text exact | no | yes |

Every removed call was a model turn reading a state the sequence could verify
itself; the title-change race vanished because the title change became the
between-step condition. A title proves the window's state, not its content:
the setup navigation matched its title 390 ms after Enter while the page was
still painting, so the drag still waited for a screenshot.
