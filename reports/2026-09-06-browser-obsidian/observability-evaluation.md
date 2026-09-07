# Evaluation of computer-use latency observability

Evaluated September 7, 2026. **The available evidence is sufficient for broad prioritization, but insufficient to fully support reliable operation-level latency diagnosis or causal claims about optimizations.** The retrospective is careful about those limits. The proposal's measurement work is necessary; the newer checkout does not yet satisfy it.

The immediate priority is a reproducible, correlated trace of successful **and unsuccessful** operations, with explicit timing boundaries and independently checked outcomes. Then instrument the client/host path. More screenshots or larger transcript dumps would not resolve the main measurement gaps.

## Evaluation performed

Read the entire [retrospective](retrospective.md), [proposal](proposal.md), and all 47 rows of the [call ledger](tool-calls.csv). Cross-checked [metrics.json](metrics.json) and [phases.csv](phases.csv), inspected the current input, observation, capture, development-host and benchmark implementations, and examined the saved local capture benchmark. Consulted primary documentation for the external tools recommended below.

Ran a new [offline evaluator](evaluate-observability.py); its complete results and source SHA-256 manifest are in [observability-evaluation.json](observability-evaluation.json). It performs 46 consistency checks and four mocked probes of the current `Desktop.call` timing contract. All 46 checks passed. The existing input-server suite passed 34 tests; the observer latency suite passed 10 tests.

This was an observability evaluation, not another browser-to-Obsidian acceptance run. The probes replace desktop and subprocess effects and do not measure real input speed. Original private session/approval logs and images were not reprocessed. Consequently, agreement among exported files confirms internal consistency, not independent verification of the original log extraction. In particular, the 48 model-request intervals are not exported individually, so their pairing and overlap cannot be reconstructed from this evidence bundle alone.

The inspected checkout was at Git revision `4a2b34ca837405fe234eda50da76d16d6e021dfe`; source hashes identify the files actually evaluated. This is distinct from the older installed runtime used in the original task. No implementation, installation, desktop configuration, or approval behavior was changed.

Reproduce from the repository root:

```bash
python3 reports/2026-09-06-browser-obsidian/evaluate-observability.py > /tmp/observability-evaluation.json
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_server.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s wayland-desktop-observer/tests -p test_latency.py
```

## What the data supports

| Question | Assessment | Evidence and limitation |
|---|---|---|
| How long did this task take? | Sufficient for this run | 752.586 s completion duration; timestamp boundaries differ by 32 ms. |
| Which workflow phases and interaction loops were costly? | Sufficient for prioritization | 47 wrappers, 54 underlying operations, 34 input attempts, 13 rejections. Phase time includes preceding model work; it is not application execution time. |
| How much time was in tools and automatic reviews? | Useful coarse measurement | 136.082 s host tool envelope, including 71.707 s review. Six wrappers contain multiple operations with no exported child spans. |
| What was the main-agent request footprint? | Useful coarse measurement | Reported 537.864 s after overlap subtraction, 71.5% of task time. This is a client-observed model-service envelope, not pure inference or reasoning time. |
| Which implementation stages are slow? | Insufficient | Historical 64.375 s non-review tool envelope has no capture/encode/compare/input/debug-write split. New fields improve coverage but have omissions and nesting ambiguities. |
| Why did a guard reject, and what did recovery cost? | Partially sufficient | All 13 rejected rows retain changed-pixel diagnostics. No explicit retry/episode links, frame age at review and execution, or independently labeled false-positive corpus. |
| Did a successful input produce the intended result? | Insufficient | The corrupted insertion is marked `rejected=False`; its failure is described in prose. New `action_performed` and condition fields help but are not general text-equality verification. |
| What did images and transport cost? | Payload volume only | 77,346,651 bytes of base64-decoded PNG files, not decompressed RGB memory or network traffic. Resize, serialization and transport stages are not isolated. |
| Will a change improve representative task latency? | Insufficient | One failed/partially successful agent run and small local fixtures; no paired end-to-end distribution, instrumentation-overhead baseline or controlled attribution. |

### Quantitative findings from the audit

The named broad envelopes cover **89.55%** of task elapsed time. The remaining **78.640 s (10.45%)** is unidentified host/client time. This already misses the proposal's 95% target. Even 95% broad coverage would not establish diagnostic sufficiency: a label such as “tool” or “model request” can conceal the entire mechanism under investigation.

The ledger's call-to-result measurement exceeds host tool duration by **8.975 s**. Image-bearing calls account for **8.956 s** of that difference. Their median difference is **239 ms**, versus **2 ms** for calls without images. This is a useful lead for investigating host image/result handling. It does **not** identify resizing as the cause: the boundaries also include delivery, scheduling and logging, and the call groups differ. This difference is already within the wider task accounting; it is not another additive cost.

Rejection-loop intervals total **202.297 s**, while rejected tool envelopes total **44.644 s**, including **25.842 s** of approval. These are overlapping descriptions. The next chronological call is not necessarily a retry of the same action; explicit episode links are needed before measuring recovery or estimating avoidable work.

The saved [second local benchmark](../../benchmarks/latency-20260906-031607.json) also exposes a current instrumentation gap. Subtracting named metadata, accessibility and validation durations from `collection_ms` leaves **35.451, 34.540 and 35.206 ms**—**35.1–36.0%** of those collections. Source inspection places a target lock check and some setup/cleanup outside named children, but the residual cannot be assigned precisely without new spans. These fixture residuals are separate from the historical 78.640 s task gap.

## Current instrumentation: useful foundations and specific defects

The repository already has monotonic capture/action watermarks; backend and presentation metadata; collection, metadata, accessibility and validation timings; capture/encode and successful guard/input/wait totals; freshness and timeout fields; and runtime revision reporting in the development host. Reuse these foundations rather than starting a second observation system. See [server.py](../../scripts/server.py), [observation.py](../../scripts/cu/observation.py), [capture.py](../../scripts/cu/capture.py), and [dev_host.py](../../scripts/dev_host.py).

### 1. Timing disappears on the paths most important to diagnosis

The four mocked probes exercise the actual `Desktop.call` control flow with external effects replaced:

| Path | `action_performed` | Operation total returned? | Timing information retained in response |
|---|---|---|---|
| Successful text input | `true` | Yes | Guard, input, total and screenshot timings |
| Guard rejection | `false` | No | Recovery screenshot timings only, if a screenshot succeeds |
| Input backend exception | `unknown` | No | None in the error response |
| Screenshot failure after input | `true` | No | None, although guard/input timings exist internally |

In `Desktop.call`, `total_ms` is attached only on the normal completed-action return path. `guard_ms` is assigned only after `prepare` returns. Exceptions bypass those assignments or discard accumulated timings. Argument validation also happens before the operation timer. Standalone screenshot and desktop-state operations lack a uniform full-request total.

**Fix:** start a request span at server ingress, finalize it in `finally`, and close stage spans on exceptions. Keep timing/status sidecar records even when no normal response can be returned. For process termination, let the host mark an unmatched span as incomplete rather than fabricating its end. Record focus restoration separately: an input rejected before key/button injection may still have restored focus.

### 2. The timing dictionary is not a composable trace

`guard_ms` includes identity/focus checks, possible focus restoration, capture, crop and comparison. `input_ms` includes rechecks and subprocess execution, not just event injection. `wait_ms` contains observation work; `collection_ms` contains its stage fields; `request_ms` contains waiting and response construction. Adding these values double-counts work.

Cached samples expose earlier collection timings, which should not be charged again to every reader. For input-server observations, `Observer.observe(images=False)` finishes its `request_ms` before `observation_content` adds the actionable frame and PNG. The response can contain multiple timing objects whose similarly named capture fields describe overlapping work. Observer history keeps bounded summaries, not every collection's spans.

**Fix:** give each timing a span ID, parent/link relationship, start/end, scope and clock domain. Link consumers to the actual sample/capture span. Export background collections separately, including collections never returned to a caller. Calculate exclusive time by subtracting the **union** of contained child intervals, not their summed durations. Report wall-clock critical-path occupancy separately from cumulative resource work.

### 3. Capture and result stages are incomplete

`Capturer.capture` starts its timer **after acquiring its lock**, so internal capture duration excludes lock contention. A failed native attempt and fallback can occupy one capture envelope. The capture object has a fallback reason, but ordinary frame metadata does not consistently expose it; missing-helper and unsupported-output selection need explicit reason codes too.

`capture_content` reports PNG encode duration before base64 conversion. Target crop extraction, JSON serialization and stdout flush lack individual spans. Observation `response_ms` combines delta calculation, crop, PNG and base64 work when images are enabled. Client resize and model submission sit outside this repository's server timings.

**Fix:** separate lock wait, helper startup/request/read, compositor wait, fallback, RGB parsing, crop, hash/diff, PNG encode, base64, JSON and transport boundaries. Give each capture a unique ID and propagate actual backend and fallback outcome to its consumers. Log image dimensions and byte counters at each representation change.

### 4. Existing benchmarks can hide failures and readiness delay

[benchmark_latency.py](../../scripts/benchmark_latency.py) alternates capture methods and preserves source hashes, which is useful. However, it saves its report only after all assertions pass; a failure can discard earlier measurements. It schedules a 200 ms label change but does not persist the fixture's actual application timestamp, so request duration minus 200 ms is not a reliable detection-lag measurement. Its event count is aggregate, not linked to wakes and collections. It also does not identify the running installed plugin.

The older [MCP comparison harness](../../wayland-desktop-observer/scripts/benchmark.py) already times request/reply boundaries and counts serialized reply bytes. Its variable named `wire` represents local stdio response bytes, not network traffic; request bytes are not included in that counter.

**Fix:** write a manifest and append each trial as it completes; always emit a final success/failure/incomplete summary. Persist stimulus scheduled/applied, event received, collection started/completed, predicate matched and response delivered timestamps. Label fallback trials by their actual backend. Preserve attempted and failed samples and report their denominators.

## Recommended measurement design and tool choices

### First: a small repository-owned recorder and analysis adapter

Add a bounded local JSONL recorder, outside MCP stdout, with an allowlist of numeric/categorical fields. Instrument both command wrappers (`server.run` and `cu.system.run`) and the stages above. Use a common schema so it can later export OpenTelemetry spans. A dependency-free recorder fits this server; a telemetry backend is optional for the first useful iteration.

Minimum records should include:

- **Provenance:** run/experiment/pair IDs, intended and actual bundle/source/schema hashes, process instance and boot IDs, Python/native-helper/app/compositor versions, model configuration, output scale/rotation, capture/guard settings, fixture version, warm/cold and debug flags.
- **Correlation:** trace/span/parent IDs, outer orchestration call ID, nested MCP request ID plus connection generation, operation ID, retry-of ID, observation/capture/frame IDs, reviewer request ID, and model request/response IDs where accessible. Use span links for reused samples and fan-out; one shared correlation string alone cannot express concurrency or retries.
- **Time:** monotonic start/end nanoseconds, clock-domain identifier and paired wall/monotonic synchronization points. Never directly subtract remote server, browser, compositor and local monotonic timestamps without establishing their mapping and uncertainty.
- **Outcome:** transport status, guard reason, focus side effects, input not-started/completed/partial-or-unknown, observation status, verification passed/failed/unavailable, error stage and recovery episode. Unknown, absent and zero are different values.
- **Cost:** backend and fallback reason, subprocess duration/exit status, image count/dimensions/encoded bytes/base64 bytes, request and response bytes by boundary, CPU and IO counters, waits/polls/wakes, and main/reviewer token usage once per response. Log text lengths and a local verification equality result, not submitted text.

Extend the offline evaluator into an exporter that joins legacy session/host records to the new sidecar, reports unmatched and ambiguous joins, and produces `spans.jsonl`, `operations.csv`, `models.csv`, `approvals.csv`, `run.json` and an analysis report. Preserve parser/schema versions and input hashes. If raw logs are unavailable, it should say which claims cannot be reproduced. This would remove the manual forensic reconstruction needed for the present retrospective.

Use standard trace IDs, spans, events and links from **OpenTelemetry** rather than inventing a tracing model. These primitives support parent-child work and relationships between asynchronous operations. Automatic instrumentation alone will not identify guard, capture or outcome boundaries; those require custom spans. [OpenTelemetry traces](https://opentelemetry.io/docs/concepts/signals/traces/).

Export a local **Perfetto** timeline for inspecting model, approval, host, server, worker and application tracks together. Its Trace Processor supports SQL analysis, useful for repeatable interval/gap queries, and clock snapshots support alignment. Exported legacy intervals must retain their inferred/coarse labels. Perfetto cannot recover unrecorded stages. [Trace Processor](https://perfetto.dev/docs/analysis/trace-processor), [clock snapshots](https://perfetto.dev/docs/reference/trace-packet-proto).

### In parallel: host/client instrumentation for the largest unresolved costs

The plugin cannot observe work before its request arrives or after it returns. Host cooperation is necessary for full coverage. Record:

1. Tool emitted, dispatch queued/dequeued, approval requested/started/decided/delivered, MCP sent/received, result decoded/resized/encoded and delivered to the agent.
2. Model context/image preparation, request queued/sent, first response event, first usable output/tool call, stream completion and usage receipt. Record retries and cancellations. First output is not necessarily an executable tool call.
3. Actual client request/response counters and image dimensions before/after transformation. Include an image lineage ID and coordinate transform, especially given the historical 2560×1440-to-2048×1152 change.

Persist the 48 individual model spans and per-review records instead of only summary totals. Provider queue/inference splits may remain unavailable; label that interval as an external service envelope. Local CPU profiling cannot explain server-side inference. If host hooks are unavailable, use the exported host logs for coarse bounds, mark the missing fields explicitly, and keep full end-to-end attribution as unmet.

Attribute model-catalog downloads by process, request purpose and task linkage before optimizing them. Neither their count nor declared `Content-Length` establishes blocking cost in this workflow.

### Use profilers only after spans identify a suspect stage

| Tool | Useful question | Limit / proposed use |
|---|---|---|
| Existing local capture/wait and MCP benchmark harnesses | Did a capture, encode or observation change improve a controlled fixture? | Extend their output and failure handling first; they exclude model/approval costs. |
| Python `cProfile` | Which Python functions consume a suspect stage? | Available through the standard library; deterministic profiling perturbs execution, so keep diagnostic profiles separate from acceptance timings. [Python profiler documentation](https://docs.python.org/3/library/profile.html). |
| `py-spy` | Are pixel loops, JSON/base64 work or worker threads consuming CPU? | Sampling profiler with subprocess/native options; short targeted captures, not a replacement for request correlation. [Project documentation](https://github.com/benfred/py-spy). |
| Linux `perf stat` | Did CPU time, context switches, faults or hardware counters change? | Use scoped process/workload measurements with a control run; resource totals alone do not establish the latency critical path. [perf-stat manual](https://man7.org/linux/man-pages/man1/perf-stat.1.html). |
| Chromium DevTools Protocol Network events | Is article loading delayed by browser requests/cache behavior? | Instrument a disposable browser fixture, linked to navigation IDs. Request IDs, timestamps and encoded received lengths describe browser loading; they do not account for host model requests or all TLS/link-layer bytes. [Network domain](https://chromedevtools.github.io/devtools-protocol/tot/Network/). |

`perf`, `py-spy`, `strace` and `trace_processor` were not found on the evaluated shell's PATH. No installations are needed to use the supplied evaluator or begin local span instrumentation. Avoid starting with broad packet capture: it gives weaker semantic attribution than application counters and is unnecessary for the first iteration.

### Measure observability itself

Keep default records free of screen contents, titles, URLs, clipboard contents, accessibility text and approval reasoning. Use run-local opaque target IDs and sanitized reason enums; opt-in failure artifacts can remain local with a byte/time retention bound. Do not make every numeric span another model-visible response block: that would expand context and distort the behavior being measured.

Measure recorder queue depth, dropped records, export/flush time, output bytes, CPU and memory. Use bounded buffering with an explicit loss counter; classify lossy runs as incomplete. Preserve all operation outcomes in evaluation runs. Compare tracing enabled/disabled on randomized paired fixtures; a provisional acceptance budget is at most 2% median end-to-end overhead, with an absolute noise floor reported for very short operations. This is a proposed budget, not a measured property.

## Prioritized development loop and acceptance gates

| Priority / owner | Deliverable | Evidence required before proceeding |
|---|---|---|
| P0 — plugin + evaluation harness | Exception-safe request/stage spans, outcome schema, run/build manifest, incremental trial output | Every attempted call has an outcome or explicit incomplete record; all four probe paths retain operation timing; no silent record loss. |
| P0 — analysis tooling | Versioned join/export and interval accounting | Unique correlation or explicit unmatched status for every operation; model/approval intervals retained; no negative exclusive times or double counting on concurrency/cached-sample fixtures. |
| P1 — plugin/observer | Guard children, lock/subprocess/fallback stages, per-sample/wake links, actual fixture readiness timestamp | At least 95% of plugin request wall time explained by named exclusive stages, separately for success, rejection, timeout and failure; report residual distribution. |
| P1 — host/client | Approval/model/image/result lifecycle and measured byte counters | At least 95% of task wall time assigned to named boundaries; external service envelopes remain explicitly opaque. Publish missing transport boundaries instead of asserting complete wire accounting. |
| P1 — input/guard fixtures | Independent insertion verification and labeled guard cases | Submitted/observed equality, save-completion evidence, uncertainty and recovery retained; report incorrect accepted inputs as well as rejected valid inputs. |
| P2 — experiment harness | Paired before/after runs and targeted profiles | Correctness gates pass and paired latency improvement exceeds measurement overhead/noise; do not accept a faster run that corrupts text or requires external repair. |

These gates refine the proposal's “95% attributable” requirement into **boundary coverage and diagnostic stage coverage**. Keep the provider's opaque service envelope visible in both the report and uncertainty statements. Complete wire-byte counters should be scoped by measurement layer; browser/CDP, local MCP and client HTTP counters are different quantities.

For each improvement, state a testable hypothesis and change one main factor. For example: “Returning a ready editable observation reduces rejection episodes and model requests while preserving the negative guard corpus.” Use spans to check the mechanism, then paired task runs to measure the outcome. A quicker capture fixture alone cannot establish that mechanism or the final task speedup.

Start with the proposal's ten paired warm trials as a pilot. Counterbalance/randomize build order, reset windows and disposable notes, pin article/excerpt and expected contents, and record app/browser cache state, model configuration and actual runtime. Separate cold install, first capture/worker startup and steady-state results. Keep all attempts, including crashes, timeouts, corruption and recovery, in the exported dataset.

Report task success and verified completion latency together; also report total cost across all attempts, rejected/partial operations, recovery time, model/approval calls, image bytes and resource use. Show individual paired differences, medians and uncertainty; successful-only timing can be a secondary labeled view. Do not infer stable p95 performance from ten pairs or six capture trials. Likewise, 100 correct insertions are a useful gate but do not prove a zero failure rate.

The best first optimization loop is therefore: **verify build → reproduce and verify input correctness → record complete operation traces → remove measured retry/decision cycles → compare paired tasks → profile remaining expensive stages.** The present data justifies that priority. It does not justify a guaranteed six-minute runtime, assigning the 78.6 s gap to networking, or treating every rejected action as a false positive.
