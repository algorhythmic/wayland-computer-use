# Proposal: make browser-to-note tasks reliable and materially faster

The baseline is **12m 32.586s**, 47 orchestration calls, 34 input attempts, 13 rejections, 37 screenshots, and one corrupted text insertion requiring a filesystem repair. The first performance milestone should be a **clean warm run within six minutes**, with no repair outside the plugin. This is an engineering acceptance target, not a forecast supported by the single existing run.

## 1. Establish which build is being tested

**Problem:** the reinstall selected `0.1.0+codex.20260905224921` from `/home/david/plugins/wayland-computer-use`, while this checkout already contained newer observation, capture, and timing work. Installation success did not mean that this conversation adopted the newer server contract.

**Change:** add a compact version/capability check to the test procedure. Record the marketplace version, installed source path, running revision, and advertised capabilities. Use the normal install and reconnect flow when schemas or host code change. Begin a test only after the running revision and capabilities match the intended build.

Use the existing connected plugin immediately when the request is to test that installed build. Avoid repeated marketplace investigation. Keep skill reads to the applicable workflow and filter tool discovery by exact namespace/name before expanding descriptions. A catalogue-wide description dump should never be the first discovery step for a named, connected plugin.

**Owner:** test workflow and assistant instructions. **Acceptance:** the run names its intended and actual revisions before input; warm preflight uses at most two observation/discovery calls. Cold install timing is reported separately from the app workflow.

## 2. Fix and verify long text insertion

**Problem:** the long insertion returned successfully but transformed an intended 933-character body into an incorrect 879-character file. Investigation and repair subsequently occupied 126.5 seconds. The current source still calls `wtype` for literal text; the newer capture path alone does not address this failure.

**Change:** reproduce the issue in a disposable Obsidian note and a plain editor using the exact problematic character mix: lowercase and uppercase letters, punctuation, Markdown links, parentheses, and newlines. Record submitted length, input duration, backend, modifier cleanup, and an independently observed result. The cause might involve input delivery, keymap/modifier handling, or editor behavior; these remain hypotheses until controlled reproduction.

Compare paced `wtype` input with a guarded clipboard-backed insertion operation. A clipboard implementation must validate the target before acting, scope the text and paste to the approved operation, and avoid overwriting a newer user clipboard change when restoring prior clipboard ownership. Protect password and terminal/chat fields through the existing operation context. A transport exit code should be reported separately from verified text equality.

For an application with usable accessibility or a structured document interface, verify inserted text through that interface. For the acceptance test, compare the created file with the expected contents after the app saves. A partial or uncertain insertion must return an explicit state that prevents a blind replay.

**Owner:** input backend. **Acceptance:** 100 consecutive insertions of the test corpus with zero missing, duplicated, reordered, or substituted characters; correct modifier release on failure; no clipboard clobbering during user intervention; no external file repair in the end-to-end test.

## 3. Return ready observations and reduce irrelevant visual rejections

**Problem:** thirteen rejected input attempts consumed 44.6 seconds inside tools and participated in 202.3 seconds of reject-to-next-call intervals. Some changes were necessary to review; others were tiny caret-shaped changes or browser chrome outside the click region.

**Change:** first adopt and validate the repository's existing `after`/`wait_for` and same-capture observation facilities. Return after explicit conditions such as the intended window appearing, the page title becoming available, or a relevant accessible control being ready. A window/title match is not by itself proof of page stability. Apply bounded readiness conditions to the actual operation, and report timeout separately from successful input.

Then investigate narrowly scoped visual handling. Separate target identity/focus/geometry/expiry checks from dynamic-pixel evidence. Where supported, use a uniquely identified focused editable control and fresh accessibility state. Investigate caret-specific handling only with controlled evidence and strict locality. Keep full validation of meaningful changes, modal overlays, navigation, and the intended action region. Do not solve the problem by broadly increasing RGB thresholds or skipping validation when focus has not changed.

Retain the current combined restore-focus operation for approved focus restoration. On rejection, return a fresh actionable observation and a precise reason. Avoid a separate refocus/screenshot loop.

**Owner:** guard and observation layers. **Acceptance:** under 5% rejection on a defined, stable browser/Obsidian fixture, while negative tests continue rejecting moved targets, changed dialogs, expired frames, user focus changes, altered actionable controls, and partial execution. Report readiness timeouts independently. A general false-positive rate requires a labeled test corpus, not just counting every rejection as false.

## 4. Reduce full-image delivery and unnecessary model turns

**Problem:** 37 full-monitor results became 77.35 MB of PNG payloads and 103.13 MB of base64. Target windows represented only 24.6% of the original image area. Main model request spans accounted for 537.9 non-overlapping seconds.

**Change:** use the newer same-capture window observations for input frames, with exact crop origin, delivered dimensions, and coordinate metadata. Provide complete task-relevant context, including visible menus and modal overlays. Use accessibility/metadata-only waits when no new visual decision is required. For follow-up observations, use changed-region images or reusable image references only when the host/model contract preserves the necessary full context and validates the fresh underlying capture.

Ensure that the coordinate system describes the image the model actually receives. In this run the tool described 2560×1440 frames while the retained images were 2048×1152. The host and plugin should either preserve original dimensions or expose an explicit transformation.

Add bounded semantic operations where they remove deterministic keyboard sequences: launching an allowlisted application, navigating a validated browser address field, and inserting literal text into a known editable control. They must remain explicit, approved operations with target validation and outcome evidence. Do not batch speculative clicks across unseen menus or dialogs.

For this particular workflow, a direct Obsidian launch avoids the System-menu detour and browser protocol-dialog sequence. Remove the unnecessary maximize attempt. Keep article selection and reflection writing as substantive model decisions, while reducing repeated decisions about mechanical cursor movement and ready-state polling.

**Owner:** tool API, host image handling, and assistant workflow. **Acceptance:** at most 25 model/tool orchestration cycles for the warm task; at most 20 MiB of decoded task image payloads as an initial target; correct coordinates across cropping, resizing, scaling, and monitor rotation. Measure accuracy and failure recovery alongside speed.

## 5. Optimize approval overhead within existing authorization boundaries

**Problem:** 26 automatic reviews took 71.7 seconds and used 691,061 input-plus-output tokens, mostly cached context. Nine reviews approved actions that the plugin subsequently rejected without performing input.

**Change:** first reduce redundant input attempts; this reduces review cost without changing policy. Separately, the approval host can evaluate compact structured operation descriptions and incremental evidence, rather than accumulating unrelated transcript content. Investigate a deterministic path for actions that already match an explicitly approved, narrowly bounded task scope, while preserving the host's required checks and escalation for materially different actions.

A read-only readiness preflight could identify an already stale frame before an input approval is requested. This must be part of a supported host/tool design: the final approved execution still needs fresh validation because state can change during review. Do not automatically replay rejected inputs, loosen user-intervention behavior, or globally disable approval gates.

**Owner:** approval host; this is not solely a plugin change. **Acceptance:** unchanged allow/deny results on the approval regression corpus, fewer reviews proportional to eliminated attempts, and separately measured review queue, model, and decision-delivery time. No permission changes are part of this proposal's analysis run.

## 6. Finish the measurement pipeline before claiming a speedup

The current repository already contains raw RGB capture, optional persistent capture, window observations, and timing fields. Use them, but validate the installed runtime first. The historical local fixture's roughly 13ms native-capture advantage over raw grim would amount to under a second across 70 captures if it transferred unchanged to this workload. That calculation illustrates scale; it is not an end-to-end prediction. Removing PNG round trips and Python pixel work could have a larger effect, which needs stage-level measurement.

Add one correlation ID from orchestration call to approval, server request, input, observation, and model request. Record monotonic spans for metadata, capture, PNG encode/decode, crop, pixel comparison, focus restoration, input, readiness wait, debug writes, client resize, result delivery, model request/first output/completion, and approval request/decision. Record explicit outcomes: rejected-before-input, injected, partially executed/unknown, and independently verified.

Record image dimensions and encoded bytes both before and after host resizing, request/response wire bytes where available, and token usage by main agent and approval reviewer. Keep recorded byte counts distinct from `Content-Length`, local files, decoded images, base64 representations, and cumulative tokens. Default telemetry should contain timings and counters, not private screenshot contents. Debug images should remain opt-in with bounded retention.

Investigate the 23 same-window model-catalog downloads as a separate host observation. Attribute them to a process/request purpose before caching work is prioritized; the existing evidence does not establish that they delayed this task.

**Owner:** plugin telemetry and client host. **Acceptance:** at least 95% of elapsed time attributable to named, non-overlapping spans; complete request/response byte counters for the measured path; per-operation verification results; no sensitive screen or approval-reasoning dumps in shared reports.

## Rollout and evaluation

1. Verify and record the active build, then reproduce and fix text corruption.
2. Exercise existing same-capture observations and readiness waits; measure the remaining guard failures.
3. Add the smallest missing application-launch and text-insertion primitives, and reduce broad discovery and full-image returns.
4. Evaluate approval-host and model-effort changes only after correctness and tool behavior are stable. Keep the baseline model configuration for the first comparison so improvements are attributable. A later controlled experiment can compare a lower effort or faster model with the same task and correctness checks.
5. Run paired warm trials of the same workflow on both builds, with identical starting windows, a fixed article/excerpt, and disposable notes. Record cold installation separately. Begin with ten paired trials; collect substantially more samples before making p95 claims. Publish failures as well as successful runs.

The first acceptance budget is **≤360 seconds**, ≤25 orchestration cycles, ≤20 MiB of image payloads, zero text corruption, and no repair outside the plugin. Guard and approval negative tests must continue to pass. These targets are intentionally measurable and should be revised from observed distributions.

Potential savings overlap: the 202-second rejection-loop footprint includes model and approval time already counted elsewhere, and the repair phase includes a guard rejection. Those numbers must not be summed into a promised saving. The evidence supports prioritizing fewer interaction cycles and reliable insertion; it does not yet support a guaranteed final runtime.
