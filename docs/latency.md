# Local observation and latency

The model still chooses actions. A local observation service maintains versioned
evidence and evaluates explicit conditions between model decisions. It performs
no autonomous input and never interprets application text as instructions.

## Runtime

`scripts/cu/` is shared by the two independent MCP servers. Each process has its
own observation scope, six-revision history, and renewable 120-second lease.
There is no shared socket service or cross-server frame-token exchange.

The input server's `observe_window` and `wait_for` use this service directly.
When pixels are available and meet the requested freshness, they return a full
window crop and a one-use input frame from that same immutable RGB capture.
The frame records the crop origin, actual monitor/layout, target identity,
capture start time, and validation pixels. Input revalidates the target and
pixels before using a shared-observation frame, including when no focus
restoration is requested. Existing restore-focus scroll behavior is retained.
The standalone observer continues to return non-actionable revisions and deltas.

Raw `grim -t ppm` is the portable capture path. It replaces PNG compression,
ImageMagick decoding and crop extraction with bounded RGB parsing and in-memory
crops. PNG encoding happens only when an image is delivered. Validation always
uses pixels from the same capture as the delivered image; cached previews are
never substituted for input evidence.

The optional `native/capture.c` helper retains a Wayland connection, screencopy
manager and reusable shared-memory buffer across requests. It detects
wlr-screencopy v3 and named outputs at runtime. Its initial implementation only
supports scale-1, untransformed outputs and supported 32-bit RGB formats.
Rotated/scaled outputs, missing protocols, helper failures and unavailable
binaries fall back to raw `grim`. ext-image-copy-capture is not implemented.
Build with `python3 scripts/build_capture.py`; no service or input device is
installed. `WAYLAND_CU_CAPTURE_HELPER` can select an explicit helper path.

Pixel-only waits can request a bounded compositor-damage wait. A static screen
eventually receives a forced fresh copy, so an absent damage event never proves
freshness or blocks indefinitely. Frames and shared-memory buffers are released
on failure; helper failures have a 30-second retry cooldown. Actual backend and
capture/presentation timestamps accompany input images.

An interruptible pipe wakes the observer for requests and AT-SPI invalidations.
Hyprland events use the same selector; irrelevant events, including our own
screencast lifecycle, are ignored. Events are subscribed before the first sample.
Periodic collection reconciles missed/incomplete event coverage. Collection and
PNG encoding do not hold the same lock. New requests cannot be satisfied by a
sample that merely finished after the request but started before it.

The persistent AT-SPI subprocess subscribes to the selected application and
re-reads the bounded tree for each requested accessibility sample. A two-second
deadline kills a stuck worker; the next request can start a new one. This retains
process isolation for unresponsive applications without restarting Python/GI on
every normal read. Each server starts the worker at launch so its GI import
precedes the first probe; it reads nothing until a probe. Selecting other
channels, losing target focus, or stopping observation unsubscribes it from
the application but keeps the imported process, which exits after 120 seconds
without an observation scope. Missing, protected and truncated evidence is explicit.

## Tool examples

Use `wayland.observe_window` to inspect an exact focused window:

```json
{"window":"0x123","channels":["pixels","accessibility"]}
```

Review the returned full image. Its `frame_id` and coordinates belong to the
window crop, not the full monitor. The independent observer's revisions and
accessibility refs cannot authorize input.

An input tool can perform one action and then wait for an accessible name:

```json
{
  "frame_id":"reviewed-frame-id",
  "x":150,
  "y":100,
  "after":{
    "condition":{"kind":"accessible","name":"Ready","role":"label"},
    "timeout_ms":5000
  }
}
```

This is an example argument to `pointer`. Existing `restore_focus`,
`target_window`, and `target_title` requirements still apply. `after` supports
accessible or window conditions, not speculative input sequences. A timeout
does not undo input. Successful injection and successful outcome verification
are reported separately. `action_performed:"unknown"` means input may have
partially executed; inspect before any retry.

For a standalone read-only wait, call either server's `wait_for`:

```json
{
  "window":"0x123",
  "channels":["accessibility"],
  "condition":{"kind":"accessible","name":"Ready"},
  "after_action":123456789000,
  "timeout_ms":5000
}
```

Replace `after_action` with the actual `action_completed_ns` from a local input
response, or omit it. It is a monotonic clock watermark, not an action capability.
Accessible conditions require a unique exact name in a complete tree. Optional
`role`, `state`, and numeric `value` refine the match. Partial trees are still
returned as evidence, but cannot establish uniqueness for the wait predicate.

Other conditions are:

```json
{"kind":"window","title":"Export complete","class":"ExampleApp"}
```

```json
{"kind":"region_changed","box":[20,30,220,130]}
```

Window matching requires exactly one mapped window; it does not focus it.
Region matching requires `window`, the `pixels` channel and a retained
`since_revision` with baseline pixels. Boxes are exclusive right/bottom crop
coordinates. Keep the same window and channels as the baseline observation;
changing either resets the observation scope. Unknown/evicted baselines and
invalid boxes reject the wait.
Unrelated changed regions do not satisfy it. A changed region alone is not
semantic task completion.

`channels` selects collection: `metadata`, `pixels`, `accessibility`. Metadata is
always included; omitting `window` collects desktop metadata only. On the
standalone observer, `images:false` suppresses image delivery but does not stop
pixel collection. This keeps compatibility with existing pixel-change waits.
Default `max_age_ms:0` requires collection begun after the request; values up to
5000 explicitly permit a cached observation. `fresh_sample`,
`freshness_satisfied`, `age_ms`, `status`, `condition_met`, and `wait_timed_out`
describe what was actually obtained. Timeout is not success. Evidence remains
non-atomic across compositor metadata, accessibility, and pixels.

## Tracing and attribution

Every input-server request runs inside a request span that is closed on every
exit path. Rejections, partial input failures, and post-input capture failures
therefore return the same `timings_ms` fields as successes: `guard_ms`,
`focus_restore_ms`, `visual_check_ms`, `debug_write_ms`, `input_ms`, `wait_ms`,
`result_ms`, `recovery_ms`, and `total_ms`, whichever occurred. These values are
**inclusive**: guard time contains focus restoration, the visual check and any
recovery screenshot. Do not add them. Read-only calls report `result_ms` and
`total_ms`. Frame metadata reports `capture_backend`, an explicit
`fallback_reason` (`unsupported_output`, `helper_unavailable`,
`helper_cooldown`, `helper_failed`), `png_bytes`, and `lock_wait_ms`, the time
spent waiting for the shared capturer lock before the capture timer started.
Observation timings add `lock_check_ms` for the compositor-lock probe that was
previously unitemized inside `collection_ms`.

Setting `WAYLAND_CU_TRACE_DIR` to an absolute, user-owned, mode-0700 directory
records one JSON line per request to a 0600 file, outside MCP stdout. Records
carry the span tree with parent links and monotonic start/end nanoseconds, a
wall-clock/monotonic sync point, subprocess spans (`argv0` and exit code only),
the outcome (`ok`, `rejected`, `error`; `action_performed` true/false/unknown;
`focus_restored`; error type and a truncated reason), argument shape such as
`text_len` or `condition_kind`, and response sizes, frame provenance, guard
metrics and wait outcomes. Strings survive only under allowlisted keys, so typed
text, key chords, titles, URLs, clipboard and accessibility content are never
written. The file is capped at 64 MiB; further records increment a `dropped`
counter and `desktop_state.trace.complete` becomes false. Recording is off
unless the variable is set, and enabling it does not change any guard.

`scripts/benchmark_latency.py` now rewrites its report after every trial with
`status` `incomplete`, `complete`, or `failed` and the failure message, so an
assertion never discards earlier samples. Wait trials persist the scheduled,
applied, request and response monotonic timestamps; `after_change_ms` is the
detection lag measured from the fixture's actual label update rather than from
the scheduled delay. Each wait records the accessibility events it received and
the checkout and published revisions the run used.

## Validation and measurement

The first implementation run is recorded in
[`latency-20260906-030825.json`](../benchmarks/latency-20260906-030825.json).
Six alternating captures per backend of the same static GTK window measured:

| Median | Raw grim | Persistent native |
|---|---:|---:|
| Capture | 17.92 ms | 5.70 ms |
| Capture plus PNG encoding | 40.39 ms | 28.38 ms |
| Encoded PNG size | 480,742 bytes | 480,742 bytes |

Three accessible-label waits with a scheduled 200 ms change completed in
292–300 ms. Same-capture input-frame creation and a bounded native damage wait
also passed. This is local capture and deterministic verification, excluding
model inference, approval, client image processing and network latency. It is
not a before/after agent benchmark or a generally established p95. The initial
report predates source/dimension/event-count fields added to the runner; later
runs include those fields. Keep all reports distinct from the preserved original
comparison.

A [second run](../benchmarks/latency-20260906-031607.json) on a 1261×688 crop
measured 5.08 ms native capture and 27.19 ms capture plus encoding, versus
17.91 ms and 40.43 ms with raw grim. All consecutive RGB captures agreed exactly,
and the persistent accessibility worker received four invalidation events.

Reproduce with `python3 scripts/benchmark_latency.py --focus-fixture`. This
explicitly focuses its own disposable fixture once and stops on later focus
changes. It injects no keys/buttons and saves metrics, never screen images.
The unit suites cover in-flight freshness, real selector wakeups, cached reads,
channel selection, unrelated changes, invalid predicates, raw pixels, helper
reuse/failure, crop-to-input coordinates, stale guards, partial input failures,
and bundle reload/checksum behavior.

Next measurements should include full model-driven tasks and p50/p95 latency,
approval time, false wakeups/rejections, and background CPU. Current timing spans
already separate metadata, capture, accessibility, validation, encoding, input,
wait, and request time. Do not remove validation to improve a timing result.

## Optimization pass, September 7, 2026

Three single-factor changes, each measured with the tracing above before the
next was applied. All guards, checks and validation order are unchanged; only
the mechanism behind each check changed. Benchmarks:
[`latency-20260907-121059.json`](../benchmarks/latency-20260907-121059.json)
(baseline), [`-122446`](../benchmarks/latency-20260907-122446.json) (lock
check), [`-122659`](../benchmarks/latency-20260907-122659.json) (IPC queries),
[`-122846`](../benchmarks/latency-20260907-122846.json) (parallel deflate).

1. **Lock check.** `pgrep -x hyprlock` cost 38–41 ms per call and ran up to six
   times per input action. `cu.system.desktop_locked` reads `/proc/*/comm` for
   the same predicate in about 5 ms, without a subprocess, and fails closed when
   `/proc` cannot be read. It records a `lock_check` span.
2. **Compositor queries.** Read-only `monitors`, `activewindow` and `clients`
   queries go over the owned Hyprland IPC socket (`cu.system.hypr_query`),
   returning byte-identical JSON in about 0.05 ms instead of 4.1 ms per
   `hyprctl` spawn. Dispatch and instance discovery still use `hyprctl`; a socket
   failure falls back to it. Recorded as `ipc` spans.
3. **PNG encoding.** Row data at or above 2 MB is deflated as up to four
   independent members joined into one zlib stream, the technique pigz uses.
   Output stays lossless, decodes with any inflater, and grows by about 0.01%.

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

Rejected after measurement: zlib `Z_RLE`/`Z_HUFFMAN_ONLY` were 7–10 ms faster
but 35–44% larger; a pure-Python PNG Sub filter cost about 685 ms per frame.
The IPC-run benchmark opened its fixture on the rotated monitor, so its capture
rows report `unsupported_output` and are excluded from the capture comparison;
its wait rows are valid. The accessibility cold probe (95–102 ms for a new
worker) and the actionable-frame crop/encode/base64 step (about 23 ms) are the
largest remaining local stages. A guarded input's four lock checks (about
20 ms) can only be measured end to end with the input daemon installed. These
are local stage timings; they do not predict model-driven task time.

## Second pass, September 7, 2026

Two more single-factor changes, found with the same tracing and measured
offline and through the checkout server.

1. **Pixel guard.** `pixel_difference` looped over every pixel in Python and
   cost about 640 ms per guarded input on a 1261×688 crop, the largest local
   stage by far and invisible in the retrospective because it sat inside the
   undifferentiated guard envelope. It now detects changed pixels with byte
   XOR, computes deltas only for changed pixels while the change count can
   still be accepted, and otherwise computes the exact maximum channel delta
   with 16-bit-lane arithmetic on one big integer. Every metric equals the
   original loop; a randomized test checks 400 crops against the old code.
2. **Accessibility worker lifecycle.** The 88 ms of a cold probe was the GI
   import, not the tree read. The worker is now started when a server launches
   and when an observation scope becomes active, unsubscribed rather than
   killed on channel or focus changes and on stop, and reaped after 120 seconds
   without a scope. Hung workers are still killed and replaced.

| Measurement | Before | After |
|---|---:|---:|
| Guard pixel comparison, 1261×688, unchanged crop | 640 ms | <1 ms |
| Guard pixel comparison, one changed pixel or an 18-pixel caret | 640 ms | 20 ms |
| Guard pixel comparison, fully changed crop (rejected) | 640 ms | 77 ms |
| First accessibility probe after server start | 95 ms | 12 ms |
| Accessibility probe after a pixels-only observation | 95 ms | 9 ms |

A guarded input performs one full-crop comparison and one action-region
comparison, so the pixel change alone removes roughly 0.6 s from every input
action's guard. The retrospective's 34 input attempts would have spent about
20 s there. These remain local stage timings; an end-to-end guarded input
measurement still needs the input daemon installed.

## Remaining candidates, September 7, 2026

Measured after the second pass. Two were worth changing; two were not.

- **Lock scan.** Reading `/proc/*/comm` through raw descriptors instead of file
  objects halves the check from 4.3 ms to 2.1 ms. A guarded input runs it four
  times.
- **Changed-region tiling.** `changed_box` compared every 64-pixel tile row by
  row, 13 ms on a 1261×1390 crop with one changed pixel. It now compares whole
  rows first and tiles only rows that differ, scanning from each edge for the
  outermost changed tile: 1.2 ms for one pixel, a band, or a full change, with
  the same tile-aligned result. A randomized test checks 400 crops and tile
  sizes against the full comparison.
- **Actionable-frame construction** after an observation is about 10 ms on a
  1261×1390 crop and is almost entirely the parallel PNG deflate. No change.
- **First-probe subscription** on a warm worker is about 12 ms of AT-SPI event
  registration with the application. No change.

Local stages are now small enough that the next evidence should come from the
paired browser-to-Obsidian rerun described in the reports, with the input
daemon installed, rather than from further fixture timings.

## End-to-end pilot, September 7, 2026

Two pairs of a browser-to-Obsidian task, old build against current build,
same model and host, are recorded in
[`reports/2026-09-07-browser-obsidian-ab/results.md`](../reports/2026-09-07-browser-obsidian-ab/results.md).
Per-call host envelopes fell 4 to 6× for key presses and focus changes and
about 2.4× for the task's total tool time, but tool time is 1 to 4% of wall
time, which the model dominates. Removing the fixed post-action sleep exposed
a title-change race that cost an extra round trip; the post-action capture now
retries once when the target changed mid-capture and reports `result_retried`.
The typed-text corruption reproduced in all four trials on both builds.

## Typed text: the keystroke cutoff and its fix

`tests/live_text_entry.py` opens a disposable GTK text view, types fixed
corpora through the real `type_text` path, reads the buffer back through the
fixture's stdin protocol, and reports dropped characters with their
distinct-character rank. It focuses only the window it created and aborts on
any later focus change. Baseline runs with `--no-split` established the
mechanism by elimination (`benchmarks/text-entry-20260907-16*.json`):

| Corpus | Result without splitting |
|---|---|
| 40 distinct alphanumerics | exact |
| 62 distinct alphanumerics | exact |
| 32 punctuation characters with long keysym names | exact |
| Tiny text with newlines | exact |
| 120 × `a` then ` bcdefghij` | every character after the run dropped, including the space |
| 80 × `a` then new characters | exact |
| 100 × `a` then new characters | new characters dropped |
| 40 keys at 15 ms delay, then new characters (0.9 s) | exact |
| 20 keys at 40 ms delay, then new characters (1.2 s) | exact |
| Proposal corpus, 300 characters, 64 distinct | 68 characters dropped from the 30th distinct on |

So the cutoff is a keystroke count between 86 and 100 within one `wtype`
process, independent of elapsed time, newlines, keysym-name length or the
number of distinct keys. After it, keymap updates for newly introduced
characters are no longer applied, while previously introduced characters keep
working. GTK4 and Electron clients behave identically on Hyprland. The
September 6 corruption (62 characters lost from a 933-character body) and the
pilot's four identical corruptions are the same defect. `type_text` now sends
text in consecutive `wtype` invocations of at most `TEXT_SEGMENT_CHARS` (40)
characters, each a fresh virtual keyboard, and reports `text_segments`.
Acceptance: 24 of 24 trials across eight corpora exact, then 100 consecutive
insertions of the proposal corpus, all exact
(`benchmarks/text-entry-20260907-165710.json`).
