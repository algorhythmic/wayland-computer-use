# WCU/Braid implementation and evaluation results

September 8, 2026. Implementation of handoff tracks A–D is delivered and installed;
track E and the explicitly optional traversal/high-DPI extensions remain deferred.
Fresh MCP connections verified the installed runtime, tool contract, skill files,
context schema and independent observer. An existing Codex thread still needs
normal client rediscovery; start a new thread to use the updated plugin tools and
skill. No app restart or private session manipulation was performed.

**All 27 paired desktop task artifacts passed.** With monitor results held constant,
guarded batching reduced median form time by 37% and browser-to-note transfer time
by 47%. Target crops produced additional reductions. These are small, deterministic
local task benchmarks, excluding model inference and approval time. Two separately
recorded model-driven form tasks also passed, including reuse with held-out values;
that smoke test does not establish a general model speedup.

## Delivered scope

| Handoff | Implementation and evidence |
|---|---|
| A1 | Intended identity remains pinned across readiness checks; every input segment rechecks focus, identity and lock state. Explicit destination transitions, corrected focus recipe, and coordinate revalidation after waits. Source regressions and live interruption tests. |
| A2 | Complete execution ledger survives partial injection, failed outcomes, deadlines and failed final/recovery captures. Shared validated duration budget, bounded recovery, full request validation and no automatic replay. |
| B1–B2 | Immutable normalized snapshots, stable scoped IDs, exact case-sensitive lookup, eligibility before ranking, deterministic local lexical retrieval, exact dependency closure, canonical UTF-8 byte accounting, read-only domain API/CLI/MCP operation. Failed refresh is explicit. |
| A3 | Runtime/source/tool/context/observer identity and separate published skill checksums, including references. Updated adaptive workflow and normal plugin reinstall. Fresh installed MCP connections verified; existing-chat rediscovery is not claimed. |
| C1 | Revision-local ancestor scopes, focused-control identity, complete-scope absence, state predicates, baseline/window change, region stability and bounded non-protected text readback. Fake and real GTK coverage. |
| C2 | Monitor, target and supported-region result views with immutable capture provenance and overview fallback. Original detail requested at both PTY loading and delivery. Decoded direct-MCP payloads and model-visible PTY delivery checked separately. |
| B3 | Pinned supervised Braid process, negotiated v1 contract, lexical-only ranking, dedicated datasets, exact revision reads, bounded paged reconciliation, protocol limits/deadlines and consistent local fallback. |
| D1–D2 | Adaptive batch policy and recipe lifecycle, source/process conformance, labeled retrieval comparisons, paired desktop task tests and a model-driven exploration/held-out recipe smoke test. Braid remains opt-in. |

Implementation entry points: [execution](../scripts/cu/execution.py),
[server](../scripts/server.py), [context normalization](../scripts/cu/context_records.py),
[retrieval](../scripts/cu/context_retrieval.py), [Braid adapter](../scripts/cu/braid_client.py),
[recipes](../scripts/cu/recipes.py), and the
[workflow contract](../skills/wayland-computer-use/references/execution-and-context.md).
Recipes are declarative session/domain records, not an autonomous server planner.
`focus_until`, semantic activation, arbitrary code, concurrent input, async waits,
vector/graph ranking and native high-DPI regions are not advertised as implemented.

## Desktop E2E benchmark

[Harness](../scripts/benchmark_handoff_e2e.py) and
[raw metrics](benchmarks/wcu-braid-20260908/desktop-e2e.json).
Three trials per task/variant, with variant order reversed on alternating trials;
every trial resets disposable GTK state. Browser-to-note transfer uses an isolated
Chromium profile and a local synthetic page. Final JSON artifacts are read directly
and checked independently of MCP success. Setup/reset/focus and process startup are
outside task timing; task outcome checks and final artifact verification are inside.

| Task, median | Single actions, monitor | Sequence, monitor | Sequence, target | Combined time reduction |
|---|---:|---:|---:|---:|
| Native two-field form | 658 ms | 416 ms | 311 ms | 53% |
| Browser-to-note transfer | 825 ms | 437 ms | 338 ms | 59% |
| Accessibility-poor canvas toggle | 172 ms | 171 ms | 64 ms | 63% |

Keeping the monitor view fixed isolates batching's benefit: 37%, 47%, and effectively
0% respectively. The one-input canvas task benefits from cropping, not batching.
Keeping sequence routing fixed isolates target-view savings: 25%, 23%, and 62%.
The comparison is between routes on the revised executor; the frozen old executor
is used separately for the interruption comparison below.

| Task | Tool calls: single → sequence | Images: single → sequence | PNG bytes: sequence monitor → target |
|---|---:|---:|---:|
| Form | 4 → 1 | 4 → 1 | 4,023,151 → 89,851 (98% lower) |
| Transfer | 8 → 4 | 7 → 3 | 4,193,814 → 257,211 (94% lower) |
| Canvas | 1 → 1 | 1 → 1 | 4,020,985 → 88,112 (98% lower) |

The transfer deliberately ends its first batch after paste, resolves the unique
text control, waits for exact text readback, then saves. Both routes check destination
control readiness. This fixed two real pilot failures: compositor focus arrived
before GTK was ready for keys; and asynchronous paste completed after an early Save.
The live pilot also found and fixed a PyGObject `Accessible.get_text`/`Text.get_text`
method collision missed by a simplistic fake interface.

The final paired run had zero task guard rejections, outcome timeouts or recovery
attempts. Direct-MCP image payloads decoded to 2560×1440 monitor and 409×340 target
images. Payload bytes are measured PNG/JSON bytes, not tokens or host rendering
latency. Three trials and a shared desktop do not support general statistical or
model-performance claims. The catalog timing probe overlapped the beginning of the
first form trial; subsequent trials and all transfer/canvas trials ran after it
finished. Medians are reported; no confidence interval is inferred.

### Focus interruption and remaining race

Each test types 81 harmless `x` characters into a disposable entry in three backend
segments (40/40/1). The fixture requests focus on its own decoy window after the
first 40 characters. Three trials per executor per condition:

| Ordering | Revised executor, decoy characters | Frozen baseline, decoy characters |
|---|---:|---:|
| Confirmed focus change between backend segments | 0 / 0 / 0 | 41 / 41 / 41 |
| Natural asynchronous app/compositor race | 40 / 40 / 40 | 41 / 41 / 41 |

The first condition uses the same test-only wrapper for both executors: it calls
real `wtype`, then waits for the fixture's acknowledged focus change before that
invocation returns. This establishes the ordering under test; production input is
not delayed by that wrapper. The natural race is measured independently, without
the wrapper.

The revised executor rejects and reports partial submission, leaving later segments
unattempted. It does **not** eliminate the race between a guard and delivery: in the
natural test a second segment was submitted before the app-triggered focus change
became observable. No automatic refocus/replay was used. Backend submission remains
explicitly distinct from application acceptance. Zero wrong-window input is not
claimed for asynchronous focus changes.

### Model-driven smoke and recipe reuse

[Call/image metrics](benchmarks/wcu-braid-20260908/model-smoke.json),
[exploration artifact](benchmarks/wcu-braid-20260908/exploration-form.json), and
[held-out artifact](benchmarks/wcu-braid-20260908/recipe-form.json).

The same Codex thread inspected the real form and focused-control evidence, queried
local context, learned the Name→Code Tab transition through observation, and saved
`Delta 739` / `K93`. A declarative navigation recipe was recorded only after the
artifact was independently verified. After reset, the model reused that checked
transition in a guarded sequence with held-out values `Echo 846` / `Z17`; the second
artifact also matched. Configuration-change invalidation is covered by domain tests.

There were three rejected input requests across this smoke test: two visual-guard
rejections and one focus-change rejection. Their ledgers showed no submitted input.
The model reconciled each result; when the user changed focus, input stopped until
the user explicitly made the desktop available again. The held-out run then passed.
This interrupted two-task sample is not a paired timing comparison. Initial
exploration needed three successful input calls; the final held-out sequence needed
one, with rejected attempts, observation, context and explicit refocus separately
retained in the raw metrics. Model/effort and decision-token timing were not
independently instrumented, so no inference-time or general recipe-speed claim follows.

The PTY adapter visibly delivered 409×688 target, 2560×1440 overview and 1080×1920
rotated-monitor images using `view_image(detail="original")` and original-detail
image delivery. The direct stdio benchmark verifies decoded MCP payload dimensions;
it does not prove that every external MCP host honors the image metadata hint.
Private screenshots remain in ignored/local storage and are not included here.

## Context and Braid results

[Labeled results](benchmarks/wcu-braid-20260908/context-labeled.json) use eleven
sanitized cases: eight relevant-action cases and three expected no-answer cases.
They cover literal/paraphrased browser intents, Herdr prefix/collision, exact Vim
case/punctuation/modes, absent optional mappings, Obsidian overrides/interception,
OBS unassigned recording and incompatible source versions. Ten warm repeats per
case/backend use the same eligibility, closure and 8,192-byte renderer.

| Backend | Relevant-action recall | Warm p50 | Warm p95 |
|---|---:|---:|---:|
| Original full-binding substring search | 3/8 | 0.55 ms | 0.71 ms |
| Structured local lexical/alias adapter | 8/8 | 0.70 ms | 0.92 ms |
| Braid lexical adapter | 8/8 | 78.79 ms | 81.13 ms |

Every backend had zero ineligible executable recommendations, incomplete bundles,
mixed revisions, byte overruns or incorrect expected no-answers. Exact lookups use
the common structured lookup path, independently of ranking. Curated aliases improve
these fixture intents; this is not a broad language-understanding evaluation.

[Installed-catalog timing](benchmarks/wcu-braid-20260908/context-catalog.json) covers
2,052 normalized records derived from 1,998 binding rows plus context/unassigned
records, five intents and fifty warm samples per backend. Live availability remains
unknown, so returned candidates remain exploration references. It excludes file
loading, model inference and desktop input:

| Backend | First query | Warm p50 | Warm p95 | Median payload |
|---|---:|---:|---:|---:|
| Local | 61 ms | 61 ms | 82 ms | 6,752 bytes |
| Braid | 483 ms | 147 ms | 182 ms | 7,244 bytes |

There were no backend failures, unsupported executable recommendations, incomplete
bundles or budget overruns in the final run. Initial attempts exposed an oversized
exact-read response when reconnecting to an existing dataset. A real 5 MB catalog
regression reproduced it; paged exact reads now retain the same expected revision
and transport cap throughout reconciliation.

**Local remains the default.** Both warm p95 values meet the provisional 250 ms
retrieval-pipeline target, but Braid shows no quality advantage over local on these
labels and costs additional latency. Its cold/reconciliation time exceeds that
warm target. Braid is optional and lexical-only: weights lexical=1, dense/graph/
temporal=0, `mmr_lambda=1`; no vector diversity is used. Deployment needs a pinned
binary path and SHA-256, and requests must opt into `backend: "braid"`.

## Validation, identity and reproduction

- Root: **124 tests passed**, including seven real-Braid tests and two transport
  fault tests. Added regressions were observed failing before their fixes.
- Independent observer: **31 tests passed**. Frozen baseline: **28 tests passed**.
- JavaScript adapter tests passed, including original detail at both stages,
  shared-duration watchdog bounds, fragmented replies and no replay on failure.
- Skill/plugin validation, `git diff --check`, and frozen-comparison hash checks
  passed. Existing `.mcp.json` customizations and historical benchmark/baseline
  artifacts were preserved. Braid's working tree was not modified.

[Release identity](benchmarks/wcu-braid-20260908/release-identity.json),
[desktop-time activation](benchmarks/wcu-braid-20260908/desktop-activation.json), and
[dirty-source manifest](benchmarks/wcu-braid-20260908/source-manifest.json) record
exact hashes. WCU base checkout is `10d40219f1d02b8ec425da497017bbd406df1331` with
uncommitted source changes. Installed release is
`fb4ac4da6ab03192d99ca4b8e26963a3eb60e9f8e5748350adbc0badad1957ab`.
The desktop/model tests ran on
`6bfc5c5c72b861a8b163ede300595386f07a4a6d21665d1c0d15714a08aed452`;
manifest comparison confirms **only `cu/braid_client.py` changed afterward**.
All execution, capture, observation, host contract and skill bytes tested on the
desktop are unchanged in the final release. Final retrieval tests and a fresh
installed identity read verified the final release separately.

The input contract is `wcu-tools-2`, context contract `wcu-context-1`, schema 1.
Both plugin registrations were reinstalled as `0.1.0+codex.20260908075753` through
the normal personal-marketplace flow; the subsequent Braid-only bundle update needs
no schema rediscovery. Main skill SHA-256 is
`b1bb77b49d09948340babaa9603962d27195eec86615728b26feede6e5c345bf`;
the full reference manifest is included in release identity. The server reports
`client_loaded_skill_revision: null`; the client-read files were compared separately.

Braid is pinned to clean commit `65c7f6e23cdbb0e01aa43bd287ed913e6b17faa2`, built
with checksum-verified Go 1.27.1. Binary SHA-256:
`a3b079f58d427f972df8acaecab42cefa5f3c67183c064c35e9e6738328a11f6`.
Negotiated protocol is 1 and ranking version is `mmr-admitted-dependencies-v2`.
Capabilities, dataset identities, toolchain checksum and package versions are in
the identity artifact. Runtime source hashes, not checkout HEAD alone, identify
this implementation.

```bash
WCU_BRAID_TEST_BIN=/absolute/path/to/pinned-braid python3 scripts/dev_publish.py --destination .
node tests/test_wayland_call.js
python3 scripts/verify_comparison.py
python3 scripts/benchmark_context.py --braid /absolute/path/to/pinned-braid --output .dev/context.json
python3 scripts/benchmark_context.py --catalog .dev/keyboard-context/normalized --braid /absolute/path/to/pinned-braid --output .dev/catalog-latency.json
python3 scripts/benchmark_handoff_e2e.py --trials 3 --baseline /path/to/frozen/scripts/server.py --output .dev/e2e.json
```

The desktop harness intentionally changes the clipboard to its synthetic transfer
text and does not restore the prior clipboard. It closes its own fixture/browser
processes and restores the original window when it still exists. Run only while
the shared desktop is available. No external messages, submissions or publication
were part of these tests.
