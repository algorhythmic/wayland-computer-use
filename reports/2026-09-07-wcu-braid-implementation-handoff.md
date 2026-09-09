# WCU execution and Braid context: implementation handoff

Prepared September 7, 2026. Status: implementation plan, not a completed release.

**September 8 implementation update:** tracks A–D are delivered and installed.
See the [implementation results and release evidence](2026-09-08-wcu-braid-implementation-results.md)
for completed checks, 27 paired desktop task outcomes, model-driven smoke tests,
Braid rollout decision, exact runtime identities and remaining race limitations.
The plan below is retained as the historical specification. Fresh installed MCP
activation is verified; existing Codex threads still need normal tool/skill
rediscovery. Track E and explicitly optional extensions remain deferred.

Implement reliable guarded execution and a useful local context adapter now.
Enable Braid behind that adapter once a pinned build satisfies the retrieval
contract. Braid's remaining performance or ranking work must not block WCU
correctness fixes, catalog normalization, eligibility, context assembly, or tests.

## Baseline and evidence

- WCU checkout HEAD: `10d40219f1d02b8ec425da497017bbd406df1331`.
  The working tree also contains keyboard-context work and the three reviews
  below. Preserve those changes and the pre-existing local `.mcp.json` edits.
- The connected plugin in the evaluation session was stale. The user will start
  a new session to update it. **No live behavior claim about the latest installed
  plugin follows from that session.** The two additional focus probes imported
  the checkout's `scripts/server.py` and mocked all desktop input; they establish
  source behavior at that snapshot only.
- Braid HEAD remains `b5306182b874cf60b94287fc70877e6221d310e9`, with substantial
  uncommitted implementation changes. Its current working-tree
  [retrieval contract](../../braid/docs/retrieval-contract.md) and
  [protocol](../../braid/docs/protocol.md) document the requested capabilities.
  Those documents were inspected for this plan; a rebuilt Braid executable and
  its test results were not independently verified. Treat names below as the
  integration target, subject to verification against the delivered build.
- The catalog review counted 1,998 binding rows. Counts and installed application
  versions are snapshot facts, not constants to hardcode into tests.

Source material:

- [Astra fit evaluation](2026-09-07-astra-fit-review.md).
- [Braid integration review](2026-09-07-braid-context-retrieval-review.md).
- [Braid improvement backlog, BRD-01 through BRD-08](2026-09-07-braid-improvement-backlog.md).
- [Keyboard context coverage and applicability](../skills/wayland-computer-use/references/environment-context.md).
- [Current setup](../README.md) and [runtime activation boundaries](../DEVELOPMENT.md).

The older Braid backlog describes the reviewed baseline, not proof that the new
working tree still lacks those features. Recheck source changes before turning
any finding into a patch; add a regression before modifying behavior.

## Delivery order and ownership

The rows below are independently reviewable changes, not a requirement to create
one large PR. Tracks A and B can proceed concurrently. Start their first rows
immediately; C can follow A without waiting for Braid. D consumes both tracks.

| ID | Deliverable | Prerequisites | Braid dependency |
|---|---|---|---|
| A1 | Target integrity, per-segment guards, corrected focus recipe | Current source assessment | None |
| A2 | Durable sequence ledger and shared execution deadline | A1 interfaces; can develop alongside A1 | None |
| B1 | Normalized action schema and immutable catalog snapshots | Existing collector | None |
| B2 | Eligibility, exact lookup, local lexical retrieval, bounded context | B1 | None |
| A3 | Capability/version reporting, skill routing, fresh-session activation | A1–A2 before broader batching rollout | None |
| C1 | Scoped accessibility, focus evidence, readiness checks | A1–A2 | None |
| C2 | Result-view continuity and verified image delivery | A2 recovery contract | None |
| B3 | Braid subprocess adapter and contract conformance | B2 and integration gate below | Pinned compatible Braid build |
| D1 | Adaptive planning and validated recipe lifecycle | A3, B2, applicable C1 checks | B3 optional |
| D2 | Controlled task evaluation and backend rollout | Relevant implemented rows, verified runtime | B3 for Braid comparison only |
| E | Richer control, local branches, asynchronous waits | Evidence from D2 and separate design review | None inherently |

This moves execution correctness and recovery ahead of image optimization and
expanded batching. Runtime identification can be added early; publishing broader
batching guidance should wait for A1–A2. Live evaluation requires a fresh session
with verified runtime, tools, and skill regardless of the implementation order.

## A1 — Keep input bound to its intended target

Primary files: [server.py](../scripts/server.py),
[observation.py](../scripts/cu/observation.py),
[server tests](../tests/test_server.py), and
[skill](../skills/wayland-computer-use/SKILL.md).

The source probes found two failures: a matched condition followed by a focus
change allowed input in the new window; an 81-character type operation continued
its second and third segments in another window after focus changed. Both runs
reported completion. Initial coordinate validation also precedes the first
step's potentially long `expect` wait.

Implementation:

- Return matched target identity and observation identity with condition results,
  rather than reducing all evidence to a boolean. Keep readiness predicates
  distinct from authorization to change the input target.
- Pin the intended window identity. An `expect` or `after` match must not adopt
  whichever window happens to be active on the next query. Define explicit
  transitions, including a newly opened window, using uniquely matched identity
  and an allowed transition; ambiguous results stop for inspection.
- Recheck lock state and intended target immediately before injection and before
  each text segment. Stop remaining segments on a mismatch. Do not automatically
  restore focus and resume partially delivered text.
- For a coordinate action following a wait, revalidate frame age, identity,
  geometry, and applicable visual evidence immediately before input. Keep the
  first-step-only coordinate restriction; a wait does not refresh an old frame.
- Fix the focus recipe: establish that the destination exists before attempting
  focus, then verify that the explicit destination received focus. Use observer
  fixtures whose result follows actual mocked state instead of always matching.

Acceptance: focus changes between observation and injection, between segments,
and during waits stop remaining input; changed or expired coordinate evidence
does not click. Valid explicit app transitions still work. These checks reduce
race windows; they do not promise atomicity between compositor queries and input.

## A2 — Make every execution exit recoverable and bounded

Primary files: `scripts/server.py`, [wayland_call.js](../scripts/wayland_call.js),
[dev_host.py](../scripts/dev_host.py), and their existing tests.

- Keep a sequence ledger available to the outer error handler, independent of
  final screenshot success. Identify each step's injection state separately from
  outcome verification. Include the interrupted step, last completed action,
  known completed segments, and whether an in-flight segment's result is unknown.
  Successful backend submission alone does not prove the app accepted all text.
- Return the ledger on guard rejection, backend exception, unmet condition,
  timeout, and capture failure. An earlier completion watermark must not describe
  a later interrupted action as completed. Report unattempted remaining work.
- Attempt bounded read-only recovery observation when feasible. Failure to obtain
  an image must not erase the ledger or imply that no input occurred. Include
  concise status/error codes without copying typed text into diagnostic records.
- Define a validated total sequence duration budget. Pass the remaining duration
  through waits and cooperative input helpers, and check it before each subsequent
  step/segment. The adapter's watchdog must allow that same duration plus bounded
  transport/recovery overhead. A client timeout is not evidence that the executor
  stopped; uncertain transport outcomes require observation and reconciliation.
- Validate the entire request before input. Resolve the inherited top-level
  `after` field explicitly: implement its documented meaning or remove/reject it;
  never silently ignore it. Apply enum and nested-field validation consistently.

Acceptance fixtures: successful first step plus failed second step; partial text
failure; final capture and recovery capture both failing; failed `after` despite
completed injection; 30-second nested wait; several waits exhausting the overall
budget; invalid later step rejected before any input. No input is automatically
replayed, and ledger availability does not depend on image availability.

## B1 — Normalize the existing context without a Braid dependency

Keep [keyboard_context.py](../scripts/keyboard_context.py) as the collector and
full-catalog inspection interface. Add a small domain layer, preferably under
`scripts/cu/` so runtime code participates in the existing bundle mechanism.
Suggested modules are `context_records.py`, `context_retrieval.py`, and later
`braid_client.py`; names are implementation choices, not compatibility promises.

Define versioned records for actions, required context, reference material, and
recipes. For action records retain:

| Field group | Required meaning |
|---|---|
| Identity | Stable action ID, record type, schema version, app, scope/profile, mode, alternative identity, exact command ID |
| Search text | Human-readable intent, curated aliases, original source description |
| Input notation | Original case-sensitive shortcut, notation, ordered strokes, placeholders, configured leader/prefix, supported translation if known |
| Applicability | Required mode/control/plugins, assignment state, known overrides/interception, version compatibility and unresolved conditions |
| Evidence | Source identity/checksum, source version, collection time, config fingerprint, evidence kind and known coverage limits |
| Execution context | Entry conditions, expected effect, supported outcome check or explicit need for inspection, required context IDs |

Keep semantic identity separate from source/config fingerprints: IDs should
survive unrelated refreshes; changed content produces a new snapshot revision.
Preserve multiple scoped alternatives without conflating them. Do not uppercase
Vim notation or treat a multi-stroke sequence as one chord. Unknown aliases,
outcome checks, or applicability must remain unknown rather than be generated as
facts. Source defaults are candidate evidence, not proof of live availability.

Retain configurable but unassigned OBS actions, disabled mappings, and missing
optional LazyVim extras as explainable states. Preserve Herdr's configured prefix
and collision evidence. Include Omarchy, browser, Obsidian, terminal, Herdr, OBS,
LazyVim/Neovim, and other relevant collected apps through one schema.

Publish immutable local snapshots atomically, with schema/normalizer version and
source manifest. A failed refresh must expose unavailable/stale coverage rather
than silently presenting an older snapshot as fresh. Keep machine-specific data
in ignored `.dev/` storage; use small sanitized checked-in fixtures for tests.
If runtime context code needs checked-in data or sidecar files outside `cu/*.py`,
extend packaging/manifest verification deliberately; the current publisher does
not bundle `scripts/keyboard_context.py` or reference catalogs automatically.

Acceptance: stable IDs across unrelated refreshes; deletion and disabled-state
preservation; exact `n`/`N`/`?` distinction; configured prefix retained; version
unknown represented explicitly; atomic snapshot replacement; packaging fixture
loads the same intended context schema as the development checkout.

## B2 — Deliver a backend-independent context operation

Implement the domain API and CLI first, then expose a read-only
`context_for_task` tool with the same contract. Keep it outside the input path:
context retrieval does not dispatch keys, move focus, or authorize effects.
The local implementation remains usable when Braid is missing or unhealthy.

Proposed WCU-owned contract, to freeze with fixtures before B3:

- Request: near-term `intent`; environment/observation reference; known input path,
  focused window/app, mode, profile and plugin facts with provenance; optional
  exact action/key lookup; declared output budget and deadline.
- Response: contract version; catalog identity; compact environment facts;
  eligible action bundles; separately identified exploration references;
  required checks; unresolved facts; explicit no-result/budget status; measured
  output size and estimator identity where applicable. Backend diagnostics stay
  separate from the ordinary model payload.
- A caller-supplied mode or window is a claim until supported by session evidence.
  A catalog revision is not a UI observation revision or screenshot frame ID.

Retrieval pipeline:

1. Resolve the relevant input path, for example Omarchy → Ghostty → Herdr → Neovim.
   Resolve eligibility before any ranking or candidate limit. Known mismatches
   are excluded; unknown execution prerequisites produce exploration candidates
   and a specific missing observation, not an executable recommendation.
2. Support exact lookup by structured identity independently of fuzzy search.
   Implement a deterministic local lexical baseline over descriptions and verified
   aliases. Keep the existing substring search available for evaluation.
3. Attach required context by exact ID from the same immutable snapshot, including
   prefixes, applicability constraints, interception and verification needs.
   Reject incomplete bundles; graph relevance cannot satisfy this requirement.
4. Reserve environment/envelope overhead, then admit complete bundles within the
   final rendered budget. Deduplicate shared context; drop whole optional bundles
   when necessary. Return an explicit infeasible result when mandatory context
   cannot fit. Never truncate away a prerequisite to retain an action.
5. Use an exact UTF-8 byte cap initially. If accepting a token budget, identify the
   tokenizer/renderer and measure that representation; label estimates explicitly.
   Do not equate Braid's arbitrary costs or characters/4 with actual model tokens.

Acceptance: wrong-mode/default/version and disabled mappings cannot compete away
eligible actions; references never appear as executable recommendations; all
selected actions retain required context; final model payload respects its stated
limit; no assigned OBS recording shortcut produces an informative no-answer.
Use the same labeled fixtures for every future backend.

## A3 — Align the runtime, tool contract, and skill

- Report runtime identity, tool-contract version/capabilities, and context-schema
  compatibility in `desktop_state` or an equivalent read-only capability response.
  Record the loaded skill revision separately if the server cannot observe it;
  do not claim the server knows which instructions the client actually loaded.
- Rewrite the skill's opening workflow around inspection, deterministic guarded
  sequences, and local outcome waits. Add context lookup at subtask boundaries.
  Remove contradictory one-action-only wording while retaining actual guard rules.
- Test examples as contracts, especially focus transitions and stopping after an
  unknown menu/dialog. Clearly mark future selectors and `focus_until` as absent
  until implemented and advertised by the runtime.
- Use the repository's documented build/publish and normal plugin update flow.
  A local bundle publication does not rediscover a client's tools or skill.
  Reconnect/start the new session for host/schema/skill changes and restart the
  independent observer when it changes.

Release evidence must identify checkout plus dirty-source manifest if applicable,
published runtime hash, connected runtime hash, exposed tool contract, actual
skill revision, observer version, and context schema. Acquire new frames after
activation. Do not report fresh-session validation until these match.

## C1 — Improve exploration and readiness evidence

Extend the existing bounded AT-SPI worker/observer instead of raising global
tree limits or dumping whole applications. Query under an identified window or
ancestor; return focused control identity, name, role and states when supported.
Make completeness and uniqueness relative to that explicit scope. Accessibility
references remain revision-local and their reported bounds remain unverified for
coordinate input unless separately validated.

Add bounded predicates for visible/enabled controls, loading-indicator
disappearance, change from a baseline, and a region settling. Absence requires
adequate coverage; a missing node in a partial tree does not prove disappearance.
Region stability permits inspection, not a claim of semantic task success.
Task-scoped text readback may verify supported non-protected controls; unavailable
or protected text remains explicitly unverifiable through that channel.

For Tab exploration, observe the focused control after a transition. Do not infer
exact traversal order from accessibility child order. A later bounded `focus_until`
operation may perform one Tab, inspect focus locally, and repeat up to a step/time
limit, detecting cycles, ambiguous targets and focus escape. That is a new guarded
capability with its own tests, not an assumed current `run_steps` action. Without
reliable local focus evidence, return to model inspection.

Acceptance: partial/duplicate/unavailable trees; focus identity after Tab; loading
state disappearance with complete scope; baseline geometry change; non-settling
region timeout; protected text treatment; local traversal stopping on a cycle or
wrong window if that optional operation is implemented.

## C2 — Preserve useful visual evidence

Add a result-view policy for target window, monitor overview, or supported region
to ordinary actions and sequences. Preserve immutable capture provenance and
coordinate transforms. Dialogs/overlays outside a crop require an overview or an
explicit broader observation. Coordinate validity must not be inferred merely
from retention of a previous crop preference.

Request original image detail at both loading and delivery in the PTY adapter,
where the host supports it. Verify direct MCP client behavior before relying on
metadata hints. Measure actual delivered dimensions through each supported host,
not just PNG dimensions on disk. Optional native-resolution high-DPI region
capture follows only if target tasks demonstrate a need.

Acceptance: crop → action → appropriate result view; overlay outside target;
recovery capture failure; scale/origin/rotation mapping; received dimensions and
payload bytes for direct MCP and PTY delivery. Keep the monitor overview path as
a supported option and compare task outcomes as well as payload reduction.

## B3 — Integrate the delivered Braid contract

### Gate and responsibilities

Before enabling Braid, obtain a pinned executable/build identity and run the
WCU conformance fixtures against it. Record negotiated protocol/capabilities,
ranking version and dataset identity. A working-tree document saying implemented
or a successful `hello` alone is insufficient evidence of the required semantics.

| Braid backlog item | WCU work that starts now | Gate or later adoption |
|---|---|---|
| BRD-01 hard filters | Domain eligibility and exact eligible-ID sets | Hard gate: filtering before limits, empty set means no candidates |
| BRD-02 exact/revision reads | Stable IDs, local exact index, immutable snapshots | Hard gate: exact batch reads and query/read revision consistency |
| BRD-03 required context | Deterministic WCU bundle closure | Optional engine adoption after conformance; local closure remains sufficient |
| BRD-04 projection/budget | Final WCU renderer and actual output budget | Optional bounded export; WCU always budgets its full delivered payload |
| BRD-05 final diversity | Alternative grouping and labeled baseline | Quality gate for choosing a ranking policy, not an initial integration blocker |
| BRD-06 stage loading | Warm latency/output measurements | Performance work; no speculative ANN dependency |
| BRD-07 deadlines | Caller deadline, watchdog, bounded response reader | Negotiate query timeout when available; watchdog required either way |
| BRD-08 evaluation | Shared eligibility/completeness/no-answer fixtures | Hard gate for the semantics used by the selected configuration |

WCU owns focus/mode evidence, app precedence, assignment status, aliases, required
relationships, batch boundaries and outcome verification. Braid owns generic
retrieval and the negotiated filtering, consistency, selection and export rules.

### Adapter implementation

- Run a supervised local `serve --stdio` process with one outstanding request per
  connection. Negotiate version 1 and required capabilities; match response IDs
  and handle structured error codes. Bound response bytes, elapsed time, and
  diagnostic retention. A timeout/malformed response cannot become context text.
- Use a dedicated WCU-owned dataset/database. Start with `replace_snapshot` and
  retain the mapping between WCU source fingerprints and Braid dataset revision.
  Add revision-checked incremental `apply` only when full snapshots work reliably.
- Begin with lexical retrieval enabled and dense, graph, and temporal ranking
  disabled. For a strict lexical baseline, ensure the chosen diversity policy
  also avoids vector use; dense weight zero alone is insufficient. Record the
  ranking version and effective policy with every evaluation.
- Query with the expected revision and eligible IDs/predicates, then use
  `get_many` for exact closure at that revision, or adopt verified atomic required
  context. Include eligible required-context IDs in filter construction: an
  action-only app/mode predicate must not accidentally exclude its prerequisites.
- On `revision_conflict`, discard the complete partial planning result and perform
  at most one fresh read under the original deadline; otherwise fall back or
  abstain. Never combine records from different generations. A failed mutation
  needs dataset inspection/reconciliation, not an assumed successful publication.
- If using `export_context`, the current contract bounds its `context` array,
  **not the diagnostic envelope or WCU's surrounding context**. Retain WCU's
  transport cap and final render check. Keep debug explanations out of the normal
  model response unless explicitly budgeted.
- The current contract permits graph traversal through filtered nodes and can
  mention their IDs in explanations. Keep graph disabled initially; later adoption
  must account for this rule rather than assuming filters isolate traversal.
- Fall back to the local backend using a known internally consistent snapshot,
  with freshness/applicability still checked. Restart a wedged optional retrieval
  process as needed; never restart or replay desktop input as retrieval recovery.

Conformance fixtures must exercise: missing/null versus empty allowed IDs; many
higher-scoring forbidden candidates; absent/type-mismatched attributes; exact
case/punctuation; wrong dataset; publication between search and dependency read;
missing/forbidden/shared dependencies; mandatory content too large; metadata-heavy
export; timeout/process failure; oversized or mismatched protocol response; and
no Braid installed. If any invariant fails, retain the local backend and report
the failing contract to Braid with a minimal fixture.

## D1 — Choose batch boundaries from evidence

Implement the initial planning policy in the skill and domain context layer;
do not introduce an autonomous server-side planner or arbitrary-code executor.
Maintain task state in the host/model session: subgoals, validated facts, current
observation/target, chosen action IDs, entry/exit checks, completed work, and the
next unresolved decision. Persistent execution variables do not restore UI state.

The loop is: observe relevant state → retrieve for the next subgoal → compose a
guarded predictable portion → execute with local checks → inspect/replan at an
unresolved decision. A planning phase can propose subgoals; it cannot establish
an unknown menu's Tab count. Retrieval occurs at meaningful boundaries, not after
every keystroke and not only once at the start of a long task.

| Evidence available | Execution decision |
|---|---|
| Applicable shortcut with established target/mode and supported outcome check | Include it in a guarded sequence up to the next unresolved decision |
| Known deterministic transition with unique resulting target | Continue only through the explicit checked transition |
| Unknown menu order or newly revealed content determines the next step | End the batch and inspect; use bounded local focus traversal only if supported |
| Source default, unknown extra, ambiguous collision or unassigned action | Retrieve/explore missing applicability; do not emit executable steps |
| Unexpected focus, mode, dialog, timeout or partial injection | Stop, reconcile the ledger with fresh observation, and plan remaining work |

Batch size is bounded by reliable intermediate checks, runtime step/time limits,
the cost of an incorrect action, and recoverability. Do not convert retrieval
scores into execution confidence or hardcode a larger batch as an optimization.
Retain the runtime's coordinate-action restrictions; later pointer targets need
freshly reviewed/resolved evidence and a supported action contract.

Learned recipes store entry state, applicable app/config fingerprints, exact
actions, local checks, expected outcomes, validation history and failure cases.
Invalidate or require revalidation after relevant app/keymap/layout/mode changes.
A successful first pass is evidence for that environment, not a universal Tab
macro. Compare recipes against exploration on held-out tasks in D2.

## D2 — Validation and rollout

Build the fixtures during A/B work, not after integration. Keep three evidence
levels separate: source/mocked regression, real Braid process conformance, and
fresh-session desktop task evaluation. Passing one does not establish another.

Relevant existing checks, run according to changed components:

```bash
python3 -m unittest discover -s tests -v
node tests/test_wayland_call.js
python3 -m unittest discover -s wayland-desktop-observer/tests -v
```

Follow the repository preservation checks when shared observer/benchmark behavior
changes. Add focused context and Braid adapter tests to the root suite. No live
desktop benchmark may be silently substituted for a unit test. Publication
already reruns applicable Python suites; avoid repeating unchanged checks without
a reason. Document environment restrictions separately from implementation failures.

Context evaluation uses held-out literal/paraphrased intents and adversarial
eligibility cases: Chromium address bar; custom Herdr prefix/collision; identical
Vim keys in different modes; disabled LazyVim extras; stale source versions;
Obsidian overrides; compositor interception; OBS unassigned recording controls.
Compare existing substring search, the structured local baseline, and Braid
lexical retrieval under the same WCU eligibility, renderer and output budget.

Hard acceptance on the labeled fixtures: zero ineligible executable actions, zero
incomplete admitted bundles, zero mixed revisions, and zero declared-budget
overruns. Expected no-answer cases must remain no-answer. Record relevant-action
recall, abstention reason, actual bytes/tokens, warm p50/p95 latency and backend
failures. Set a measured interactive latency budget before enabling Braid by
default; do not claim the previous implementation was too slow without evidence.

For desktop evaluation, first pass A3's fresh-session identity check. Hold model,
effort, host, plugin revision, source catalog, task start state and authorization
policy fixed. Use paired/counterbalanced trials, resetting task state each time:
browser-to-note transfer, native form, accessibility-poor canvas, and controlled
focus/error interruption. Compare one change at a time: revised routing, context
retrieval, scoped readiness, result views, then recipes. Independently verify final
artifacts and record model decision turns, input/tool calls, delivered image
dimensions/bytes, total time, guard rejections, timeouts and recovery attempts.
Small pilots guide iteration; they do not establish general Astra speedups.

Enable Braid as an opt-in backend first. Make it the default only if it preserves
all invariants and demonstrates useful retrieval/task improvements over the
local baseline within the agreed latency budget. Retain the local fallback.
If Braid integration slips, ship A, B1–B2, and applicable C/D work independently.

## E — Deferred extensions

After measured need, design separately: constrained local branching; richer
Python/JavaScript facades over existing guarded primitives; semantic activation;
modifier-drag, configurable paths, and held-key operations with guaranteed
release; native-resolution high-DPI captures; embeddings/graph ranking; and
cancellable asynchronous waits. Async requires host dispatch support, observation
scope isolation and explicit frame ownership. It does not imply concurrent input
on a shared desktop. None of these is required for the initial handoff milestones.

## Completion and next-session checklist

Track completion through three release milestones:

- **M1 — Reliable execution and local context:** A1–A3 and B1–B2, with passing
  regression/context fixtures, a versioned context contract, and verified
  fresh-session activation. This milestone has no Braid dependency.
- **M2 — Adaptive WCU workflows:** core C1/C2 and D1, with D2's desktop evaluation
  against the local backend. Explicitly optional traversal/high-DPI extensions
  can remain deferred; record that disposition. This also has no Braid dependency.
- **M3 — Optional Braid backend:** B3 conformance and D2's backend comparison,
  retaining the local fallback. M3 can proceed before M2 when its own prerequisites
  pass. Default enablement requires the additional quality/latency gate above.

Each milestone needs independently reviewable code and recorded validation.
M1–M3 complete the committed scope of this plan; E remains a separate future
backlog. The original evaluation remains historical evidence.

Start the next implementation session by:

1. Reading this plan and current repo instructions; inspecting both working trees
   without overwriting ongoing work; recording exact source/build identities.
2. Reproducing A1/A2 against current source and checking whether another change
   already fixes each finding. Start those fixes and the B1/B2 fixtures separately.
3. Confirming which Braid build is ready for contract testing; keep local context
   work moving while that answer is pending.
4. Before any live validation, using the newly connected plugin to verify A3's
   runtime/tool/skill identities and obtaining fresh observation frames.
