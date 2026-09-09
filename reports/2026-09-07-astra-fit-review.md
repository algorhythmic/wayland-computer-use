**Wayland Computer Use: fit with GPT-6-Astra — September 7, 2026**

The checkout makes its best use of Astra through deterministic sequences, precise screenshot-to-action mapping, and explicit outcome observations. The largest likely remaining gains are fewer unnecessary model turns, clearer recovery evidence, and making the current capabilities discoverable. Further PNG or IPC tuning is a lower priority given the existing pilot.

This is a source-and-evidence evaluation of commit `10d40219f1d02b8ec425da497017bbd406df1331`, supplemented by unit tests and mocked failure probes. It is not a new Astra desktop benchmark. The existing September 7 pilot was driven by a Claude Code session. No desktop input, source changes, installation, or runtime switch was performed for this review. The only connected desktop call was read-only `desktop_state`.

**The connected installation is behind the checkout.** The current chat exposes eight `wayland` tools and three observer tools. Its `desktop_state` reports runtime `d58ff2a66460f695ab07540524f7c78de56b84947e43ef1937d24ef93bc6076c`, the old build identified in the pilot. The checkout defines twelve input-server tools, including `run_steps`, `observe_window`, and `wait_for`. Its local published pointer is `6df556720cb2cbaf3a927e5c49615b9019e463b5a46c831cbf8a8eaa72716d27`; that bundle's `server.py` matches the checkout. This does not establish that every bundle file matches or that another connected chat has adopted it. The skill supplied to this chat is also older and lacks the new observation and sequence guidance.

| Astra capability | What the checkout provides | Assessment |
|---|---|---|
| Plan and execute several steps | `run_steps`, preconditions, outcome waits, one final screenshot | Strongest fit; best existing evidence of reducing model turns |
| Locate interface targets visually | Lossless images, crop origins/dimensions, exact-capture frame provenance and coordinate transforms | Strong foundation; delivery and crop continuity can improve |
| Check and revise work | Freshness watermarks, accessibility evidence, explicit match/timeout states, recovery images for guard rejections | Good on normal paths; sequence exceptions discard useful evidence |
| Stay coherent across applications | Explicit window identities, condition-checked app transitions and versioned observations | Useful, but durable task state remains a host/model responsibility |
| Follow detailed instructions | A skill explaining frames, waits, authorization and decision boundaries | Mixed: its opening loop conflicts with later batching advice |
| Use code and tools efficiently | Enumerated primitives and a bounded sequence format | Good first step; limited local branching, selectors and reusable state |
| Continue independent work while tools run | Separate observer process and event-driven sampling | Some infrastructure; synchronous requests and global frame state limit further concurrency |

OpenAI recommends code execution for Astra computer use, persistent execution state, and screenshots after short groups of actions. Its Astra guide also describes stronger instruction following, long-task coherence, and asynchronous tool calling. These are useful design targets, not proof that a particular plugin change will improve success rates. [Computer-use documentation](https://developers.openai.com/api/docs/guides/tools-computer-use), [Astra guidance](https://developers.openai.com/api/docs/guides/latest-model).

**What is already working especially well**

1. **The sequence abstraction gives the model useful work to plan.** `run_steps` separates predictable keyboard/app transitions from actions requiring another visual decision. It bounds sequence length, checks conditions locally, stops on unmet conditions, and avoids automatic input replay. This fits a model capable of planning meaningful chunks of work. See [sequence executor](../scripts/server.py#L936) and [skill](../skills/wayland-computer-use/SKILL.md#L58).

   Your pilot reduced calls from 14 to 4–5, delivered images from 10 to 3, and response payload from about 27 MB to under 8 MB. Observed wall time fell from 171–295 seconds to 68–71 seconds, while tool time changed only from about 2.3 to 1.9 seconds. The direction is promising. The small sample, extra verification in one unbatched trial, and simultaneous typing fix prevent assigning the entire wall-time improvement to batching, or predicting an Astra speedup. [Pilot evidence](2026-09-07-browser-obsidian-ab/results.md#L120).

2. **Observation provenance supports reliable visual targeting.** Actionable crops and validation pixels originate from the same immutable capture. Metadata includes dimensions, origin, target identity and timestamps; coordinate conversion handles crop origins, monitor scale and rotation. This gives Astra an explicit relationship between what it sees and where input lands. [Frame construction](../scripts/server.py#L477), [coordinate mapping](../scripts/server.py#L665).

3. **Local waits remove low-value polling turns.** Accessible-name and window predicates, event-driven wakeups, and `after_action` freshness let Astra express what it is waiting for. The server returns evidence instead of asking the model to inspect repeated screenshots. Distinguishing successful input from a verified outcome is particularly valuable. [Outcome integration](../scripts/server.py#L801), [predicate matching](../scripts/cu/observation.py#L247).

4. **The instrumentation makes optimization accountable.** Inclusive timing spans, capture provenance, error-path timing and host envelopes let you distinguish model/client delay from local execution. The 40-character typing segmentation also addresses an observed backend problem directly; a better model cannot compensate reliably for silently dropped input. [Latency evidence](../docs/latency.md#L315), [typing implementation](../scripts/server.py#L830).

**Recommended improvements, in order**

1. **Expose the current capabilities and make the preferred workflow unambiguous.**

   Install/publish the intended version through the normal plugin flow and reconnect for schema and skill rediscovery. Verify the runtime hash, tool list and skill revision together. Refreshing Python implementation bytes alone does not refresh a connected client's tool contract or instructions. Consider returning an explicit capability summary and skill/contract version in `desktop_state`.

   In the checkout skill, lines 11–12 still say to use a frame for “one input action” and inspect the result before the next action. Later instructions introduce sequences. Rewrite the opening around three choices: inspect when the next action requires interpretation; use a guarded sequence for already-understood steps; use a task-specific wait for asynchronous outcomes. Keep the detailed guard rules as reference. Astra's documented sensitivity to instructions makes this ambiguity consequential. [Opening instructions](../skills/wayland-computer-use/SKILL.md#L9), [host instructions](../scripts/dev_host.py#L153).

   There is also a concrete recipe error: the skill suggests `focus_window` with an `expect` requiring the destination window to be focused. `expect` is evaluated before the focus action. From another app, it times out without focusing. A mocked reproduction confirmed zero focus dispatches and a stopped sequence. Check that the destination exists beforehand and verify focus afterwards. The existing success test masks this by having its mocked observer report every condition as matched. [Recipe](../skills/wayland-computer-use/SKILL.md#L90), [precondition order](../scripts/server.py#L969), [test](../tests/test_server.py#L560).

   Concurrent uncommitted work appeared while this review was running: an environment-shortcut section, versioned app catalogs, and `scripts/keyboard_context.py`. This is a useful additional fit with Astra’s planning: it supplies candidate shortcuts, version/coverage information and possible compositor interception instead of relying on recalled defaults. It also corrects earlier guidance that incorrectly said `desktop_state` enumerates bindings. I inspected this addition briefly; it is not covered by the earlier suite results below. [Keyboard context collector](../scripts/keyboard_context.py), [environment guidance](../skills/wayland-computer-use/references/environment-context.md).

2. **Preserve the best visual evidence through every action.**

   `observe_window` provides an actionable window crop, but ordinary input and the end of `run_steps` call `result_capture`, which calls full-monitor `screenshot`. Thus crop-based interaction usually returns to full-monitor images after one action. Allow a result view such as target window, monitor overview, or selected region, preserving coordinate metadata and returning an overview when a dialog or overlay needs wider context. [Result capture](../scripts/server.py#L429), [ordinary input result](../scripts/server.py#L811), [sequence result](../scripts/server.py#L1029).

   The PTY adapter calls `view_image` without requesting original detail and forwards only the image URL. In this host, the default view mode can resize images. Request original detail through both image loading and delivery, and test the actual received dimensions. Direct MCP images also carry no explicit original-detail hint; check what the client supports before adding one. This is a delivery-contract risk, not evidence that every direct MCP image is currently resized. [Adapter](../scripts/wayland_call.js#L33), [MCP image construction](../scripts/server.py#L497).

   For high-DPI applications, consider optional native-resolution region captures with explicit transforms. The fallback currently uses `grim -s 1`, so it captures logical resolution. Current connected monitors are scale 1, so this is a future capability gain rather than an observed present bottleneck. Prefer useful regional detail over indiscriminately larger full-screen images. [Capture path](../scripts/cu/capture.py#L108).

3. **Make partial sequence outcomes easy for Astra to recover from.**

   The per-step report is local to `run_steps` and attached only after its final capture succeeds. A backend exception in a later step escapes to the generic error handler, losing the completed-step ledger and returning no recovery screenshot. A mocked two-step run with a successful keypress followed by failed typing returned `action_performed: true`, no `sequence`, and zero screenshot attempts. The timestamp described the earlier completed action; it could not establish the failed step's outcome. [Error handling](../scripts/server.py#L698), [report attachment](../scripts/server.py#L1026).

   Preserve a ledger on every exit: completed steps, interrupted step, injection status, verification status, and last completed-action watermark. Attempt a bounded read-only recovery observation where possible. Return it even if final capture fails. This lets Astra inspect and repair the remaining task without reconstructing what happened or repeating an input.

   The PTY adapter also budgets only a top-level timeout. It ignores `run_steps`' nested `expect_timeout_ms` and `after.timeout_ms`. A mocked sequence with a valid 30-second nested wait hit the adapter's 20-second deadline at simulated second 25. Give the sequence a total deadline understood by both transport and executor, plus bounded capture overhead. [Adapter deadline](../scripts/wayland_call.js#L6).

4. **Provide stronger readiness evidence for applications without a complete accessibility tree.**

   The pilot records a browser title matching while content was still painting. A fresh screenshot is temporally new; neither freshness nor a title proves that the desired content is ready. Current sequence conditions cover windows and exact accessible names. Region changes are excluded from sequences and input `after`; there is no region-settled predicate. [Pilot observation](2026-09-07-browser-obsidian-ab/results.md#L154), [sequence validation](../scripts/server.py#L907).

   Add bounded, composable predicates for a visible/enabled control, disappearance of a loading indicator, a state transition relative to a baseline, and a region becoming stable. Region stability should establish readiness to inspect, not semantic success. Preserve a model decision whenever the observed content determines the next action.

   AT-SPI collection stops at 200 nodes/depth 12, and partial trees cannot satisfy an exact-name predicate. That conservative rule avoids claiming uniqueness from incomplete evidence, but it limits usefulness in large applications. A scoped query under a specified window/ancestor could establish uniqueness in a bounded scope. Optional, task-scoped text readback could verify typed content when accessibility supports it; protected controls should retain their existing treatment. [Accessibility extraction](../scripts/cu/accessibility_worker.py#L40), [matching rule](../scripts/cu/observation.py#L256).

5. **Extend programmatic control incrementally after the contracts above are reliable.**

   Astra could use a small Python/JavaScript facade or richer declarative plans to store observations, select between explicitly allowed branches, and reuse verified app recipes. Keep these operations on the guarded primitives. The absence of arbitrary Python in this MCP server does not prevent the surrounding agent from using code or application APIs already available elsewhere.

   The current first-step-only coordinate restriction is appropriate for coordinates taken from one reviewed screenshot. Simply allowing later clicks would introduce stale-coordinate actions. Future richer sequences need freshly resolved semantic targets, validated static controls, or an explicit return to the model for visual decisions. Scope any such feature to measurable workflows.

   For complex native editors, modifier-drag, key-down/key-up with guaranteed release, and configurable drag paths/duration are also useful candidates. Today `drag` is a fixed left-button straight-line operation and is excluded from sequences. These capabilities would let Astra express more of the operations used in CAD, drawing and media software. [Drag implementation](../scripts/server.py#L861).

   Asynchronous waits are a later opportunity: the host currently processes requests serially, observation uses one scope per server, and each actionable capture replaces the frame dictionary. A cancellable wait handle could free the model to work on independent information while waiting. It requires host support and explicit frame ownership; concurrent input on the shared desktop would not follow from adding asynchronous tool support. [Host loop](../scripts/dev_host.py#L186), [frame replacement](../scripts/server.py#L481), [observation request state](../scripts/cu/observation.py#L379).

**Validation and the next useful experiment**

The 76 root Python tests, 31 observer Python tests, and Node adapter checks passed. One Unix-socket fixture initially failed because the execution sandbox disallowed binding its temporary socket; rerunning the root suite outside that sandbox passed. Three additional mocked probes reproduced the focus-recipe deadlock, missing sequence recovery evidence, and nested-wait adapter timeout. These probes exercised no desktop input. The repository's pre-existing changes to both `.mcp.json` files and the concurrent keyboard-context work were left intact. Test counts apply to the snapshot before the new keyboard-context tests appeared.

Use a fixed Astra model/effort, host, plugin revision, approval policy and task state to compare one change at a time. First compare current instructions with revised routing; next compare full-monitor delivery with target-window delivery; then compare richer readiness predicates. Include browser-to-note transfer, a native form, an accessibility-poor canvas application, and controlled interruption/error recovery. Record independently verified final artifacts, model decision turns, delivered image dimensions/bytes, total time, guard rejections, outcome timeouts and recovery attempts. Small pilots can guide iteration; larger paired runs are needed before claiming general success-rate or tail-latency gains. This evaluates the capabilities the model can actually exploit while keeping the source of each improvement identifiable.
