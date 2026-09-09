# Guarded planning and recovery contract

Track the subgoal, validated facts and provenance, current observation/target,
chosen action IDs, entry/exit checks, completed work, and the next unresolved
choice in the model/host session. Local execution variables do not restore UI
state. Retrieve at subtask boundaries, then execute only the predictable portion.

`context_for_task` is read-only. `intent` is required; `exact` can name an exact
ID, app, scope, command ID, case-sensitive shortcut or mode. `max_bytes` bounds
the entire canonical UTF-8 JSON text (including accounting). `timeout_ms` bounds
retrieval. `observation_ref` identifies session evidence, not a catalog revision.
Caller `facts` remain claims. `actions` have established prerequisites;
`exploration` entries are explicitly non-executable and list missing evidence.
Required context is attached by exact ID from one immutable catalog snapshot.
An infeasible budget or unavailable catalog is an explicit result. Braid is
optional, lexical-only initially, and falls back to the consistent local catalog.
Backend diagnostics are kept out of the ordinary context text.

For a supported shortcut with known focus/mode and a checkable outcome, compose
a guarded sequence up to the next unresolved decision. An unknown menu's Tab
count must be learned through observation; accessibility child order is not Tab
order. Scoped `observe_window` can report `focused_control` after a Tab. If focus
evidence is unavailable, inspect visually. Do not assume `focus_until` exists.

A focus step must first establish an existing destination, then verify it:

```json
{"frame_id":"reviewed", "duration_ms":10000, "steps":[
  {"action":"focus_window", "address":"0x777",
   "expect":{"kind":"window", "address":"0x777"},
   "after":{"condition":{"kind":"window", "address":"0x777", "focused":true}, "timeout_ms":1000}},
  {"action":"press_key", "key":"CTRL+n",
   "after":{"condition":{"kind":"window", "address":"0x777", "title_prefix":"Untitled", "focused":true}}}
]}
```

Compositor focus alone does not establish that an application is ready for keys.
When the task requires a particular control, verify that control's focused state
before typing. Clipboard paste can finish asynchronously: end the batch, read
the expected text through a scoped `wait_for` with `text_equals`, and only then
save or submit. Use a fresh observation when that application exposes no readback.

An `expect`/`after` match is evidence about its exact matched target. It never
adopts a later active-window query. A different newly opened window requires
`transition:"matched_window"` on the opening step and a unique focused-window
`after` condition. Ambiguity or subsequent focus escape stops input. These are
race-window reductions, not atomic compositor/input operations.

The response's `sequence.steps` distinguishes injection from verification:
`submitted_segments` counts successful backend submissions, `in_flight_unknown`
marks an uncertain segment, and `application_accepted` remains unverified unless
a task-specific readback independently establishes it. `last_completed_action`
never marks a later failed action completed. Unattempted steps remain listed.
A failed outcome wait does not undo input. A failed final/recovery capture does
not erase the ledger. Transport timeouts mean the outcome is unknown until
observation and reconciliation; do not resend input.

Scoped accessibility takes `a11y_scope` or `read_text` selectors with a retained
`revision`, exact `ref`, `name`, and `role`. Readback additionally accepts
`max_chars` (up to 4096). References remain revision-local and their bounds are
not coordinate capabilities. Complete scope is required for disappearance;
partial trees cannot prove absence. Protected controls never return text.
`region_stable` indicates pixel stability for inspection, not semantic success.
Region and baseline conditions use a separate `wait_for`, with retained baseline
pixels/metadata where required; they are not inline sequence conditions.

Recipes store exact action IDs, entry state, local checks, expected outcomes,
relevant app/config fingerprints, validation history, and failure cases. Use the
local `cu.recipes` domain API for persisted records. Verify final artifacts before
recording success. Relevant keymap, app, mode, or layout changes require
revalidation. A single successful Tab sequence is evidence only for that tested
environment; compare recipes with exploration on held-out tasks.
