# Braid improvements motivated by wayland-computer-use

Reviewed against Braid commit `b530618`, September 7, 2026. Companion to the
[integration review](2026-09-07-braid-context-retrieval-review.md).

These are source-confirmed capability gaps and design limitations exposed by
the intended integration. They are not reports of observed Braid production
failures. The substring-search probes in the integration review tested
wayland-computer-use's collector, not Braid. Runtime latency and retrieval
effectiveness remain unmeasured here.

## Ownership and implementation order

| Order | Braid improvement | Integration problem it addresses |
|---|---|---|
| 1 | Typed hard filters and an explicit allowed-ID set | Wrong-app, wrong-mode and disabled mappings competing for a limited candidate budget |
| 2 | Exact batch reads and query revision preconditions | Retrieving an exact key or prerequisite without fuzzy matching or mixing generations |
| 3 | Required groups/dependencies in selection | Returning an action while omitting its prerequisite or override warning |
| 4 | Output projection and explicit budget accounting | A small text-cost total producing a large metadata-heavy response |
| 5 | Diversity-aware final packing | Similar shortcut alternatives consuming the budget after MMR has reordered them |
| 6 | Retrieval work proportional to enabled stages | Loading unrelated graph/vector/full-node data on a lexical-only query |
| 7 | Per-query subprocess deadlines | An optional retrieval step delaying interactive planning indefinitely |
| Throughout | Integration contract fixtures and constrained evaluation | Improving ranking metrics while violating eligibility, completeness or latency requirements |

Items 1–2 are the best first Braid changes. They simplify a correct adapter
immediately. Items 3–5 strengthen Braid's central promise of useful context
within a budget. Items 6–7 should be accompanied by measurements. These are
proposed improvements, not implemented changes.

## BRD-01: Make eligibility a first-class query constraint

**Source:** `Filters` in [types.go](../../braid/pkg/braid/types.go) contains only
types, since-time and excluded IDs. [retrieve.go](../../braid/pkg/braid/retrieve.go)
implements this restriction separately for lexical SQL and other retrievers.
Attribute boosts change rank; they do not express hard eligibility.

**Failure scenario:** a high-scoring Insert-mode binding displaces a Normal-mode
binding before the consumer removes incompatible results. Disabled optional
LazyVim extras can similarly fill a shortlist. Filtering only the final response
can leave no useful results even though eligible candidates exist farther down.

**Proposed capability:** a typed predicate structure for scalar equality, set
membership, array containment and existence, with explicit conjunction and
disjunction. Add an allowed-ID set for consumers that already resolve complex
eligibility. Define missing/null/type-mismatch behavior. Distinguish an omitted
allowed-ID constraint from an explicitly empty set. Keep raw SQL and executable
consumer predicates out of the subprocess protocol.

**Acceptance:** lexical, dense, graph and temporal results all obey the same
predicate before their candidate limits. A fixture with many higher-scoring
ineligible nodes still returns an eligible node. Empty allowed IDs return no
nodes. Array modes and absent fields behave identically across retrievers.
Specify separately whether excluded graph nodes can be traversal intermediates;
output eligibility alone does not define traversal scope. Dataset isolation
continues to constrain both.

**Consumer responsibility:** translating observed mode, enabled plugins and
binding overrides into predicates. Braid should not implement Vim mode logic.

## BRD-02: Add exact reads bound to a dataset revision

**Source:** nodes have exact IDs and attributes, but the public interfaces in
[types.go](../../braid/pkg/braid/types.go) and
[protocol.go](../../braid/cmd/braid/protocol.go) provide no batch get-by-ID method.
Queries return a consistent dataset revision but cannot require an expected
revision. Revision preconditions already exist for writes in
[dataset.go](../../braid/pkg/braid/dataset.go).

**Failure scenario:** intent search selects an action at revision A, followed by
a dependency lookup after configuration publication at revision B. A consumer
can reject mismatches today, but must implement the entire reconciliation
contract. FTS also cannot serve as exact lookup for `n`, `N`, `?` or `<C-w>v`.

**Proposed capability:** `GetMany(ids, expected_revision)` in the library and
subprocess, plus an optional expected revision on queries. Check the precondition
inside the same read transaction that produces the result. Return exact records,
explicit missing IDs and the dataset identity. Preserve the distinction between
current-generation preconditions and historical snapshot retention; this request
does not require adding historical storage.

**Acceptance:** a concurrent update produces either a result entirely from the
requested revision or a revision-conflict response. A stale precondition fails
before provider work. IDs and exact attributes preserve case and punctuation.
Unbound/mismatched datasets cannot be read through the new method.

**Consumer responsibility:** defining stable IDs and any exact-key index from
app, scope, mode and shortcut. Checking Braid's revision does not establish that
the foreground UI is still unchanged.

## BRD-03: Keep required context with selected items

**Source:** [query.go](../../braid/pkg/braid/query.go) admits each shortlisted node
independently. Graph relationships affect ranking but impose no inclusion rule.

**Failure scenario:** a shortcut fits the budget while the record explaining its
custom prefix, interception or required starting state does not. A highly relevant
recipe can likewise arrive without a required prerequisite.

**Proposed capability:** a generic required-group or dependency contract for
selection, with explicitly mandatory context. A minimal first version can accept
preassembled atomic groups; arbitrary dependency traversal can follow. Compute
the incremental cost of adding a group, deduplicate shared dependencies and
report when a complete group cannot fit. Required context must still obey hard
eligibility and dataset scope.

**Acceptance:** an action is never returned with only part of its required group.
Shared prerequisites are charged once. Missing, forbidden or unaffordable
prerequisites prevent selection of the dependent item and produce a reason.
Cycles and zero-cost dependencies terminate deterministically. If mandatory
context itself exceeds the budget, return an explicit infeasible-budget result
rather than silently dropping mandatory content or exceeding the limit.

**Consumer responsibility:** deciding which relationships are required and what
constitutes a validated prerequisite. Braid can enforce that contract without
knowing the semantics of a keyboard shortcut.

## BRD-04: Make the budget correspond to the delivered context

**Source:** `nodeCost` in [query.go](../../braid/pkg/braid/query.go) already supports
a custom cost function, explicit node cost and an attribute cost field. Its
fallback estimates cost from text alone. Returned items also include attributes,
scores and explanations; [protocol.go](../../braid/cmd/braid/protocol.go) limits
request bytes but does not provide response projection or a response-byte limit.
This is a limitation of the default contract, not an absence of custom costs.

**Proposed capability:** selectable output fields/explanation detail, a declared
cost unit and estimator identity, and separate accounting for fixed overhead,
selected content and required context. Add a bounded context-export interface or
an explicit response-byte limit. Token-exact guarantees require a specified
tokenizer and renderer; preserve arbitrary consumer-defined costs for other uses.

**Acceptance:** very short text with large attributes cannot bypass a promised
response limit. Debug explanations have an explicit budget or separate channel.
The serialized context stays within its declared limit, or reports that a required
bundle cannot fit. Existing consumers using non-token costs retain their behavior.

**Consumer responsibility:** selecting the model tokenizer and final prompt
format where these live outside Braid. A compact generic projection avoids each
adapter reinventing response pruning.

## BRD-05: Preserve diversity through the final budget decision

**Source:** [query.go](../../braid/pkg/braid/query.go) selects an MMR shortlist, then
sorts that shortlist by raw fused score divided by cost. MMR order only breaks
ties. If the candidate union fits within `candidate_limit`, MMR selects all of it.
Existing [diversity tests](../../braid/pkg/braid/diversity_test.go) focus on cases
where the shortlist itself removes candidates.

**Source-derived counterexample:** three equal-cost candidates have normalized
relevance A=1.00, B=0.99 and C=0.90. A and B are duplicates; C is distinct. With
MMR lambda 0.4 and a shortlist limit of at least three, the MMR order is A,C,B.
With a budget of two items, the subsequent score/cost sort selects A,B. This is
an illustration of the inspected algorithm, not an executed Braid test result.

**Proposed capability:** make budget admission account for marginal diversity
against the items actually admitted, including required groups. Alternatively,
offer and document an explicit selection policy whose guarantees match the
output. Preserve deterministic tie-breaking and advance the ranking-version
identifier when selection semantics change.

**Acceptance:** cover the three-candidate example, cases where budget admission
skips an oversized item, and interactions with required groups. A query for
different subtask capabilities should not spend its budget on near-identical
alternatives solely because the entire candidate set fit the MMR shortlist.
Also retain tests where sufficiently stronger relevance properly outweighs
diversity; diversity must not become an unconditional quota.

## BRD-06: Load only the data required by the query plan

**Source:** query setup loads all nodes, invokes vector loading and constructs
graph data before resolving which retriever weights are zero. `vectors` returns
early without a configured model; otherwise it reads the model's stored vectors.
See [query.go](../../braid/pkg/braid/query.go),
[store.go](../../braid/pkg/braid/store.go) and
[retrieve.go](../../braid/pkg/braid/retrieve.go).

**Proposed capability:** determine stage dependencies first; use SQL eligibility
and lexical retrieval to fetch candidate IDs, then hydrate necessary nodes.
Load graph data only for graph retrieval or temporal-neighborhood behavior.
Fetch candidate vectors when dense search or the configured diversity policy
actually needs them. Dense weight zero alone does not imply MMR needs no vectors.

**Acceptance:** query-stage instrumentation confirms that unused graph/vector
paths perform no reads. Preserve ranking equivalence for unchanged policies.
Measure warm p50/p95, allocations, bytes read and time per stage on the 1,998-row
catalog and larger fixtures. No claim that the current implementation is too slow
is warranted until measured. ANN and a new storage backend are not prerequisites
for this optimization.

## BRD-07: Bound interactive subprocess queries

**Source:** the subprocess handles requests sequentially with its process-level
context. It exposes cancellation for background reindex jobs but no per-query
deadline field. The library accepts contexts and serializes operations with a
mutex. See [protocol.go](../../braid/cmd/braid/protocol.go) and
[query.go](../../braid/pkg/braid/query.go).

**Proposed capability:** a negotiated per-request timeout/deadline, with a
request-scoped context propagated through provider calls, storage and selection.
Bound time waiting for engine access as well as execution. A separate query-cancel
message would require concurrent request dispatch; adding that message to the
current sequential loop would not interrupt the request ahead of it.

**Acceptance:** cancellation-aware fake providers and lock-contention fixtures
show timely cancellation, one complete response per request, and a usable
subprocess afterward. The caller still needs a watchdog for non-cooperative
providers/process failures. Do not claim a deadline can preempt arbitrary code
that ignores cancellation.

## BRD-08: Evaluate integration invariants alongside relevance

**Source:** the existing evaluation harness already measures recall, precision,
abstention, stale hits and wrong-workstream hits. Its grid chooses a ranking
objective rather than enforcing all consumer acceptance conditions. See
[negative-evaluation.md](../../braid/docs/negative-evaluation.md).

**Proposed capability:** fixtures for hard eligibility, exact lookup, dependency
completeness, output budget and cancellation; optional evaluation constraints
that reject a proposed configuration violating declared limits. Record dataset,
ranking version, query overrides, projection/cost identity and stage timings.
Differentiate no lexical candidates, candidates rejected by eligibility and
eligible groups that cannot fit, without exposing rejected content by default.

**Acceptance:** a configuration with better recall but forbidden results cannot
win when zero forbidden hits is a declared constraint. Report completeness and
budget compliance as well as ranking metrics. Keep held-out paraphrases and
no-answer cases, including OBS with no assigned recording shortcut and a disabled
LazyVim extra. Source-state interpretation remains in the integration fixtures;
Braid enforces the generic constraints they declare.

## Work that should remain in wayland-computer-use

- Collecting app configuration, accessibility and focus evidence.
- Resolving actual app/plugin/version applicability and keyboard interception.
- Defining aliases, exact shortcut notation, prefixes and mode semantics.
- Classifying unassigned shortcuts and deciding which observation is needed next.
- Validating recipes, choosing batch boundaries and verifying action outcomes.

Default choices such as disabling recency for static shortcuts, selecting an
embedding model and setting a context budget are integration configuration and
evaluation decisions. Their necessity does not by itself indicate a Braid bug.

## Suggested first three pull requests

1. **Filtering contract:** allowed IDs, typed predicates, identical per-retriever
   semantics, protocol capability negotiation and adversarial eligibility tests.
2. **Exact consistent reads:** batch get-by-ID and query/read revision
   preconditions, with concurrent-publication tests.
3. **Selection contract:** atomic required groups and a regression test for
   diversity loss during packing; expose a selection-policy version and clear
   budget-failure reasons. Follow with output projection/accounting if that work
   would make this PR too large.

Each change is useful to other Braid consumers as well as computer use. None
requires Braid to become an application-specific planner or input executor.
