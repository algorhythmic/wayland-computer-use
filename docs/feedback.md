# Outcome-first desktop feedback

Contract `wcu-tools-4` separates collection, input validation, and model delivery.
The proposed HN → Obsidian speedup is an end-to-end target, not a measured result.

## Defaults and migration

- Input/observation calls return text evidence and retained frame references;
  `images:"none"` does not encode or deliver a PNG. Pixel guards still use the
  full-resolution capture. The independent observer defaults to `images:false`.
- `view_frame` reads retained pixels on demand. Default half width/height means
  approximately one quarter of the original pixels. `detail:"original"` gives
  full resolution; a changed presentation gets a new frame ID. Coordinate input
  requires a viewed frame. Frame age remains tied to the original capture.
- `images:"on_failure"` delivers a target image on failed outcome/rejection;
  `target` and `monitor` explicitly request inline views. `result_view` may crop
  to a target-relative region. Rejection recovery preserves the requested crop.
  Changed targets/overlays return `overview_required`, never an automatic monitor.
- Accessibility snapshots expose role/name/states/value, focused control and
  bounded non-protected focused text. Result captures can send deltas against the
  last delivered revision; explicit `since_revision` observations do the same.
  Apply `fields`, remove `removed_fields`, upsert nodes by ref, and remove
  `removed_refs`. Missing/partial evidence stays explicit. Scope changes reset
  observer history, so a delta request may receive a fresh snapshot.
- Without `after`, a default 1500 ms local wait looks for 100 ms of stable pixels,
  with observation backoff. Timeout/unavailable evidence stops the batch. This
  never retries input and never claims text acceptance. `settle_timeout_ms:0`
  opts out. Animating surfaces may need an explicit semantic `after`.
- A narrow caret exception requires complete AT-SPI evidence for the same focused
  control, offset and bounds before/after. All changed pixels must fit the caret
  box. No broad high-contrast tolerance or caller-supplied arbitrary mask exists.
  Toolkits without reliable extents, retain the normal guard. At end-of-text, only an explicitly reported
  insertion rectangle of at most two pixels width is accepted. Status counters are not treated as carets.

`run_steps` now accepts read-only `wait_for` and `read_text` steps. For example,
after observing/focusing an editor with a unique accessible text control:

```json
{"frame_id":"current", "images":"none", "steps":[
  {"action":"press_key", "key":"CTRL+v",
   "after":{"condition":{"kind":"text_equals","text":"Expected excerpt"},"timeout_ms":3000}},
  {"action":"read_text"},
  {"action":"press_key", "key":"CTRL+s",
   "after":{"condition":{"kind":"accessible","name":"Saved"},"timeout_ms":3000}}
]}
```

`Saved` must be an observed, app-specific accessible name; it is not a universal
save predicate. Default focused text readback is capped at 1024 characters.
Explicit revision-scoped selectors support up to 4096. Missing, protected,
incomplete or stale readback stops the batch; exact text mismatch stops the wait.
The ledger distinguishes input submission, quiescence and verified text.

## App surfaces

The [verified Obsidian transfer contract](obsidian-transfers.md) adds structured
creation, durable duplicate prevention, exact saved-file verification, and bounded
launch acknowledgement. Prefer it over manually encoded creation URIs.

See the skill's [application surface reference](../skills/wayland-computer-use/references/application-surfaces.md)
for Obsidian URI parameters, loopback CDP setup and accessibility launch flags.
`context_for_task` includes relevant surface candidates and their prerequisites
when they fit its existing byte budget. These candidates are not authorization
or proof of live app availability. Neither debugging nor accessibility flags are
silently enabled on the user's working apps.

## Measurement

Trace result counters and the handoff harness now record:

- `model_visible_image_pixels`: total width × height of emitted PNG image blocks;
  deferred/unviewed frames contribute zero.
- `model_visible_bytes`: compact UTF-8 serialized result content size, including
  image base64 where delivered. `text_bytes` separately counts UTF-8 text bytes.

These are measurements at the tool boundary, not token/quota charges or full
conversation context. The host may append metadata, the PTY adapter may suppress
images, and multiple calls may share one model turn. Aggregate by actual host turn
IDs in a session audit to obtain bytes per model turn and pixels per task; the
server cannot infer those IDs. Client-side suppression makes server-emitted image
pixels an upper bound on what reaches the model. The PTY adapter leaves old-server
images as saved-file references unless explicitly requested.

Read-only annotations were already present for observe/wait/context. New readers
also carry them; they do not force a client to omit automatic approval reviews.

## Validation and activation

Run the input and observer suites, Node adapter tests, skill validator and frozen
comparison verifier. `tests/live_cdp_read.py` additionally verifies extraction in
a disposable headless Chromium profile using the optional CDP dependency.
The updated handoff harness includes `sequence_deferred`, which verifies paste
and saves in the same batch and records visible pixels/bytes.

The tool schema and host/skill instructions changed. Publish a local tested bundle
for local harness runs. Existing installed MCP connections need normal plugin
update/reconnection and tool rediscovery; an old host rejects a changed schema.
Source edits or a local benchmark do not prove that an existing chat adopted them.

The [September 10 verification report](../reports/2026-09-10-feedback-results.md)
records the local fixture measurements. Empty GTK fields on the test machine did
not expose usable caret extents and still rejected blinking-bar changes. Focus
can also change during a submitted text segment: the natural race probe delivered
40 characters to a disposable decoy before stopping; the synchronized
between-segment probe delivered zero. These limitations remain explicit.
