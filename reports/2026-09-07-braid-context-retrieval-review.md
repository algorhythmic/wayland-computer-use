# Braid applicability to computer-use context

Reviewed September 7, 2026 against Braid commit `b530618` and the current local
keyboard-context snapshot. Recommendation: use Braid as an optional retrieval
backend behind a computer-use context adapter. Begin with structured eligibility,
normalized action descriptions and lexical retrieval. Evaluate embeddings and
graph ranking against that baseline before enabling them.

This is a source review and catalog probe, not a Braid runtime benchmark. Neither
Go nor a Braid executable was available on PATH. No embedding service, app input,
configuration change or Braid repository change was needed for this review.

For changes to Braid itself, see the [prioritized Braid improvement
backlog](2026-09-07-braid-improvement-backlog.md). It separates reusable engine
capabilities from computer-use policy, with source evidence and acceptance
criteria for each proposed change.

## The problem is already observable

The ten generated catalogs contain 1,998 binding records, 920,434 bytes of JSON,
and 487,959 bytes of Markdown including their index. These are byte counts, not
tokenizer measurements. Full catalogs remain useful as backing data.

The current `keyboard_context.py show --search` performs an AND of substrings
over each entire serialized binding, then returns the enclosing profile metadata
alongside all matching rows. There is no ranking or output budget.

| Environment and query | Returned bindings | Response bytes | Implication |
|---|---:|---:|---|
| Chromium: `address bar` | 0 | 1,061 | Human terminology does not match `IDC_FOCUS_LOCATION`. |
| Chromium: `focus` | 29 | 8,566 | Broad matches leave selection to the model. |
| Herdr: `split right` | 0 | 3,895 | Directional intent needs a verified alias for the split action. |
| LazyVim: `find files` | 8 | 8,772 | Results include alternatives and optional-plugin mappings. |
| Neovim: `normal mode` | 214 | 114,956 | Mode should be a structured input, rather than a text query. |
| OBS: `recording` | 0 | 6,111 | Unassigned configurable actions live outside the binding list. |

These are illustrative probes, not a labeled effectiveness evaluation. For OBS,
an empty assigned-hotkey result is meaningful: retrieval should explain the
missing assignment and identify an exploration route, rather than invent a key.

## What Braid supplies

The implementation has SQLite FTS5/BM25, optional dense retrieval, anchor-seeded
graph ranking, weighted reciprocal rank fusion, an MMR shortlist, cost-limited
packing and per-item explanations. Its versioned newline-JSON subprocess
interface fits this Python plugin without embedding Go in the input server.

Dataset IDs, transactional replacement, revision-checked updates/deletions and
query snapshot identity provide a useful basis for replacing obsolete shortcut
data. Its evaluation harness includes no-answer, stale-hit and wrong-workstream
annotations that can inform computer-use-specific tests.

Relevant source:

- [Query and filter types](../../braid/pkg/braid/types.go)
- [Retrievers](../../braid/pkg/braid/retrieve.go)
- [Fusion, diversity and packing](../../braid/pkg/braid/query.go)
- [Subprocess protocol](../../braid/docs/protocol.md)
- [Dataset lifecycle](../../braid/docs/datasets.md)
- [Negative evaluation](../../braid/docs/negative-evaluation.md)

## Responsibilities of the computer-use adapter

1. **Build a small environment summary.** Include the input path (for example,
   Hyprland → Ghostty → Herdr → Neovim), known focus/mode, configured prefix or
   leader evidence, relevant profile/vault and snapshot identity. Unknown focus
   or mode should request an observation, not be inferred from retrieval score.
2. **Retrieve action records for each near-term subtask.** Use a readable action
   description and verified aliases for search; keep exact case-sensitive key
   notation, command IDs, modes, source identity and applicability as structured
   fields. Group equivalent alternatives only when their conditions agree.
3. **Resolve eligibility before ranking or packing.** Exclude known wrong apps,
   modes, disabled assignments and missing required plugins. Keep uncertain
   availability explicit. Separate executable candidates from reference material
   offered to support exploration. Source-default evidence alone never proves
   that an action is currently executable.
4. **Attach required context deterministically.** Each selected action carries
   its relevant overrides/interception, prerequisites, expected effect, source
   freshness and known outcome check. Fetch dependency records by ID and attach
   them regardless of search rank. A learned recipe also requires its entry
   conditions, version/fingerprint and validation history. Current catalog rows
   do not yet supply all of these fields; the adapter must not fabricate them.
5. **Assemble within an actual output budget.** Reserve room for environment
   context and dependencies before selecting optional alternatives or recipes.
   Measure the final rendered context, including metadata and checks. Drop a
   lower-priority action as a whole if its required context cannot fit.
6. **Retrieve again at decision boundaries.** An initial plan proposes a few
   subgoals. New dialogs, modes or unexpected focus trigger observation and
   retrieval for the next subgoal. Local outcome waits can still run inside a
   batch; retrieval belongs at planning boundaries rather than every keystroke.

## Gaps and configuration choices

**Structured filters:** Braid currently filters by type, timestamp and excluded
IDs. `attrs` can be stored and boosted, but there is no general attribute filter
for application, mode, plugin requirements or version. Boosting a matching app
is insufficient to exclude mismatches. An initial adapter can use environment
type labels and explicit `exclude_ids` computed from the catalog; a generic
allowed-ID or typed attribute predicate would be a useful Braid improvement.
Apply these restrictions before candidate truncation, not only to packed output.

**Exact key identity:** Braid's lexical search extracts word/number/underscore
terms, and FTS uses Unicode tokenization. This cannot distinguish all Vim
punctuation/case combinations or key sequences reliably. Preserve an exact
lookup path for chords, command IDs and modes; use retrieval for intent matching.
Normalize identifiers for search separately, such as `focus location` plus
`address bar`, `URL field` and `omnibox` for Chromium's location control.

**Dependencies and diversity:** Braid ranks and packs independent nodes. A graph
edge does not guarantee inclusion of a required prerequisite or conflict. MMR
shortlists before packing; when the candidate set is already below the shortlist
limit, all candidates can survive and final packing sorts by score/cost. Use
complete action bundles and dependency closure in the adapter. Do not assume
that generic diversity automatically preserves batch prerequisites.

**Cost:** The default estimate is approximately text characters divided by four.
Returned nodes also contain attributes, scores and explanations. Set explicit
costs for the adapter's rendered bundles, reserve fixed context separately, and
check the final response length. Keep detailed debug explanations out of the
normal model payload; retain compact source/selection reasons.

**Freshness:** Disable temporal ranking initially. Recollecting all shortcuts
today does not make every binding semantically relevant or prove the defaults
apply to the installed version. Use source/config fingerprints, effective
binding resolution and revision invalidation for correctness. Snapshot revision
must agree with the adapter's binding lookup before returning action details.

**Abstention:** `require_evidence` rejects recency-only results; lexical, graph or
dense support is still merely retrieval evidence. Wrong-mode and disabled-key
checks remain mandatory. A useful response can contain no eligible shortcut and
a specific missing observation or configuration fact.

**Performance:** Braid currently loads nodes, vectors and graph data at query
setup, even with some retriever weights zero, and dense search is brute force.
At this corpus size that is a measurement question, not a reason to add ANN.
Measure warm subprocess latency and serialized output on the actual corpus;
defer scale work until it matters. The sequential subprocess also needs a
consumer timeout and restart policy.

## Suggested integration and rollout

Add a separate `context_for_task` tool or CLI operation, keeping the full-catalog
`show` interface for inspection. This interface is proposed, not implemented:

```json
{
  "intent": "split the editor and open another file",
  "environment_path": ["omarchy", "ghostty", "herdr", "lazyvim"],
  "focused_app": "neovim",
  "mode": "n",
  "max_context_tokens": 1800
}
```

The adapter returns compact environment context, ranked action bundles, required
checks, unresolved facts and a dataset/profile fingerprint. The budget above is
an evaluation starting point, not a measured optimum. Inputs describing focus
should come from observations and validated session state when available.

First build a baseline with exact lookup, human-readable action aliases,
structured eligibility, lexical ranking and bounded serialization. Run Braid
through `serve --stdio` with embeddings, temporal ranking and graph ranking
disabled initially. This permits comparison of the adapter with and without
Braid's packing/explanations while preserving one result contract.

Next evaluate embeddings on paraphrases and action discovery. Introduce graph
ranking for related controls and verified recipes only when labels demonstrate
value; required relationships continue to use deterministic lookup.

Publish normalized action data into a separate local dataset through
`replace_snapshot` initially, then revision-checked `apply` for changed/deleted
bindings. Preserve stable IDs across unrelated refreshes; include the mode,
scope and alternative identity where command IDs alone are not unique. Keep
long manuals, generated tables and machine-specific action evidence as distinct
record types so they are not all retrieved as competing text chunks.

Evaluate literal and paraphrased intents, identical keys in different modes,
custom Herdr prefixes, compositor interception, stale app defaults, disabled
LazyVim extras and OBS's unassigned recording controls. Measure relevant-action
recall, ineligible-action leakage, dependency completeness, bytes/tokens, warm
latency, and successful task completion/model turns. Compare held-out tasks with
the current substring search and a simple structured lexical baseline. Braid's
existing evaluation framework supplies useful ranking metrics; mode validity and
dependency completeness require additional adapter-level checks.

The immediate engineering work is the domain adapter and action schema. Braid is
a credible reusable engine for ranking and budgeted selection once those inputs
and invariants are explicit.
