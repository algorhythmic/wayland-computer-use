# Browser-to-Obsidian test: time and resource retrospective

The recorded run took **752.586 seconds (12m 32.586s)**, matching the user's 12m 32s measurement to within a second. The main cost was the repeated model/action cycle, amplified by visual rejections and avoidable navigation and setup work. Automatic approval review was measurable, but accounted for only 9.5% of elapsed time.

The browser navigation, article opening, excerpt copy, Obsidian launch, note creation, and clipboard paste succeeded. Long text entry corrupted the reflections. A direct filesystem repair produced the final usable note, so this was **a partially successful plugin acceptance test**, rather than a clean end-to-end success through the plugin.

The tested version was `0.1.0+codex.20260905224921`, runtime revision `d58ff2a66460f695ab07540524f7c78de56b84947e43ef1937d24ef93bc6076c`. The marketplace reinstall used the existing older installation. It did not activate this repository's newer implementation or tool schemas.

## Evidence and accounting method

This analysis covers the original turn only, from **03:42:56.308 to 03:55:28.862 PDT on September 6, 2026** (10:42:56.308–10:55:28.862 UTC). It excludes the subsequent retrospective request and analysis.

The evidence consists of:

- The main session JSONL, identified in [metrics.json](metrics.json), containing tool calls, outputs, usage records, and task completion timing.
- The associated automatic-approval review JSONL, used for review timing, outcomes, and aggregate token counters. Review reasoning is not reproduced.
- The read-only `logs_2.sqlite` host log, joined by the original turn ID and tool call IDs. This supplies millisecond tool durations and model-request timestamps.
- Thirteen private rejection diagnostics, used for changed-pixel metrics and disk-size accounting. Screen images remain outside this report.
- The exact installed runtime source and repository documentation, used to explain the execution path. Code-derived counts are explicitly distinguished from instrumented timings.

[tool-calls.csv](tool-calls.csv) lists all 47 orchestration calls, their operations, timings, approval times, image bytes, and rejection metrics. [phases.csv](phases.csv) and [metrics.json](metrics.json) contain the numerical breakdowns.

The completion event reports 752.586 seconds; subtracting the JSONL boundary timestamps gives 752.554 seconds. This 32ms difference is consistent with distinct recording boundaries. The completion event is the reference total. First output began after 2.270 seconds.

## Cost by task phase

Phase boundaries end at the last tool result in each phase. Thus each row includes the model work leading to those calls. Approval time is already included in tool time; neither column should be added to elapsed time.

| Phase | Elapsed | Share | Orchestration / underlying calls | Tool time | Of which approval | Rejections |
|---|---:|---:|---:|---:|---:|---:|
| Discovery and reinstall | 61.9s | 8.2% | 6 / 11 | 4.3s | 3.5s | 0 |
| Focus browser, navigate HN, open article | 98.8s | 13.1% | 8 / 8 | 25.4s | 12.2s | 3 |
| Read, locate, and copy excerpt | 101.2s | 13.4% | 6 / 6 | 19.2s | 8.4s | 2 |
| Launch and focus Obsidian | 160.0s | 21.3% | 11 / 12 | 36.9s | 20.9s | 2 |
| Create, name, and paste note | 138.9s | 18.5% | 9 / 9 | 32.7s | 19.4s | 4 |
| Enter source and reflections | 54.0s | 7.2% | 2 / 2 | 7.0s | 0.0s | 1 |
| Diagnose corruption, repair, verify | 126.5s | 16.8% | 5 / 6 | 10.6s | 7.2s | 1 |
| Final response | 11.1s | 1.5% | 0 / 0 | 0.0s | 0.0s | 0 |
| **Total** | **752.6s** | **100%** | **47 / 54** | **136.1s** | **71.7s** | **13** |

Rounded percentages may not sum exactly.

Discovery took a minute even though the plugin was already connected and ready. The actual reinstall wrapper took 3.73 seconds, including 3.54 seconds of automatic review. Most setup time came from model turns spent reading four skills, inspecting installation paths, and producing broad discovery output. The initial tool search matched descriptions containing “plugin,” returning many irrelevant tool schemas. It generated an output reported as 33,616 tokens before truncation; the retained text payload alone was 40,155 bytes. A targeted search by the Wayland tool-name prefix would have been enough.

The browser phase used an existing Chromium window and a new tab; it did not cold-start a browser process. Three visual rejections added extra model turns before the article opened. The excerpt phase used Find, text entry, Escape, and Copy, with two further rejections. This selected a precise short quote successfully, but required six calls and over 100 seconds including reading and selection.

Launching Obsidian was the longest individual phase. The guessed `SUPER+Space` shortcut opened the System menu. Dismissing it and searching configuration paths consumed additional turns without opening the app. The fallback through `obsidian://open` worked, but involved the address bar, protocol suggestion, external-app dialog, two rejections, desktop discovery, and focus. A known application-launch operation would have avoided much of this 160-second phase.

Creating and pasting the note took another 139 seconds. This included an unnecessary window-size attempt, two title-related rejections, and three paste attempts before one succeeded. The failed paste attempts explicitly reported that no action had occurred, so the final paste did not duplicate the excerpt.

The first long-text attempt was rejected. The second returned a screenshot as though injection had completed, but the saved content was wrong. The intended body had 933 characters; the file contained 879. A sequence comparison found 62 deleted characters, eight inserted characters, and one replacement segment. That comparison describes the observed corruption, not its cause. The following 126.5 seconds covered investigation, file repair, and visual verification. The final note was 947 bytes, including the added excerpt heading and blockquote formatting.

## Where elapsed time actually went

| Non-overlapping category | Seconds | Share |
|---|---:|---:|
| Main model request spans, excluding overlap with tools | 537.9 | 71.5% |
| Automatic approval reviews | 71.7 | 9.5% |
| Other tool execution and orchestration | 64.4 | 8.6% |
| Other client/host time and gaps | 78.6 | 10.4% |
| **Total** | **752.6** | **100%** |

There were **48 main model responses**. Host request-start timestamps paired with response usage timestamps give 541.6 seconds of observable model-request spans, with a median of 9.01 seconds and a range of 3.51–32.96 seconds. These spans include service processing, network transit, queuing, and streaming. They are not measurements of pure GPU inference or private reasoning time. They overlap tool execution by 3.75 seconds; subtracting that overlap produces the additive table above.

The 78.6-second remainder includes client scheduling, output handling, image processing outside the tool span, and other uninstrumented gaps. Available records do not identify a reliable split among those components. It should not be described as idle time or assigned wholly to networking.

The median gap from the preceding tool result to the next call was 10.53 seconds. There was also a **35.69-second gap before the final verification scroll**, after the repaired note was already visible. Such gaps illustrate why removing an unnecessary model turn can matter more than shaving milliseconds from a local operation.

Tool durations came from host telemetry, rather than the rounded “Wall time” text. The sum was 136.082 seconds. JSONL call-to-result intervals summed to 145.057 seconds; the additional 8.975 seconds reflects a wider recording boundary and output delivery/handling. These are alternate measurements of overlapping work, not costs to add together.

## Tool and approval counts

| Underlying operation | Attempts |
|---|---:|
| Shell execution | 15 |
| Desktop state | 2 |
| Focus window | 3 |
| Press key/chord | 18 |
| Type text | 9 |
| Pointer action | 6 |
| Scroll | 1 |
| **Total** | **54** |

The 54 operations were contained in 47 `functions.exec` calls because some independent shell reads were batched. There were **39 Wayland operations**, including **34 input attempts**. Twenty-one input attempts executed; thirteen were rejected, a **38.2% input rejection rate**. One executed long-text operation produced incorrect content, so execution success must not be equated with task success.

There were **26 automatic approval reviews**, all allowed: 24 for key/pointer operations and two for shell writes. Their total recorded duration was **71.707 seconds**, median **2.616 seconds**, range **1.737–4.518 seconds**. The focus, typing, and scroll calls did not incur a separate reviewer invocation in this run. These are observed policy behavior, not recommendations to alter permissions.

There is **no recorded human approval wait** and no automatic-approval denial. The visual rejections were plugin guard decisions, not permission denials. Nine of the thirteen rejected inputs had nevertheless incurred an automatic review, totaling **25.842 seconds**. Approval review accounted for about 52.7% of recorded tool time, but only 9.5% of the complete task.

## Visual rejections and their multiplier effect

Rejected calls consumed **44.644 seconds of tool time**, including the 25.842 seconds of review above. They also returned thirteen full screenshots. Across the thirteen intervals from a rejected attempt's start to the next tool call's start, **202.297 seconds** elapsed. This includes the rejected attempt, fresh-image review, and the next decision. It is a useful measure of the rejection loop's footprint, **not a claim that all 202 seconds would disappear with a different guard**. Some changes warranted fresh review, and one following call was a diagnostic read rather than a retry.

Four rejections had a one-pixel-wide bounding box containing only **17, 18, 19, or 28 changed pixels** in a text field. Their location and shape are consistent with caret blinking. This is strong diagnostic evidence, but not a controlled confirmation of the cause. Another pointer rejection involved only twelve changed pixels in browser chrome while the 65×65-pixel action region was unchanged. A keyboard rejection reported a maximum RGB difference of only three, exceeding the configured limit of two.

Other rejections involved actual page completion, an external-app dialog settling, graph/layout changes, or status content. Those should not all be labeled false positives. The measured 38.2% rejection rate is an operational failure rate; this run does not establish a general false-positive rate.

The installed guard rejects high-contrast changes anywhere in its checked window interior, including keyboard actions with no smaller action region. It therefore couples a valid text action to unrelated dynamic pixels. The earlier commentary's blanket description of “overly sensitive checks” was too broad; the narrower finding is that several guards rejected changes apparently unrelated to the intended action, while others caught genuinely changing UI.

## Token usage

The table sums each request's `token_usage_record.usage` exactly once. It does not also sum the duplicate cumulative `token_count` events.

| Recorded usage | Main agent | Automatic reviewer | Combined |
|---|---:|---:|---:|
| Model responses | 48 | 26 | 74 |
| Input tokens | 3,777,427 | 688,844 | **4,466,271** |
| Cached input tokens, included above | 3,654,912 | 614,912 | **4,269,824** |
| Uncached input tokens | 122,515 | 73,932 | **196,447** |
| Output tokens | 10,003 | 2,217 | **12,220** |
| Reported reasoning tokens, included in output | 3,271 | 762 | **4,033** |
| Total input plus output | 3,787,430 | 691,061 | **4,478,491** |

About **95.6% of combined input tokens were cached**; main-agent caching was about 96.8%. Millions of input tokens here mean cumulative processing of largely repeated context across many requests. They do not mean millions of unique words were generated or transmitted. The main request input grew from 16,140 tokens initially to 125,813 in the final response.

The recorded main model was `gpt-6-astra` at `xhigh` effort; the approval model was `codex-auto-review` at `low`. These facts help define this baseline. They do not prove that changing models would preserve accuracy or produce a particular speedup.

Image tokens are not separately itemized in these usage records. PNG bytes cannot be converted into token counts by dividing by four. Nor should reasoning-token counters be added again to output tokens. There may be background host requests outside the two attributable usage streams; their complete token usage is not available here. No dollar charge is inferred from subscription telemetry or cached-token counts.

## Image payloads, transfer, and file conversions

| Measured or derived quantity | Amount | Meaning |
|---|---:|---|
| Screenshot responses | 37 | Includes 13 rejected attempts |
| Decoded PNG payload bytes in logged tool results | **77,346,651 bytes** | 77.35 MB / 73.76 MiB |
| The same images as base64 strings | **103,128,916 bytes** | 103.13 MB / 98.35 MiB |
| Serialized logged tool-result content, including text | **103,293,093 bytes** | JSON representation size, not wire capture |
| Text in tool results | 147,589 bytes | Includes discovery output |
| Tool-request JavaScript text | 18,867 bytes | Excludes schemas, message envelopes, repeated context |
| PNG bytes from rejected calls | **27,380,950 bytes** | 35.4% of all PNG payload bytes |
| Rejection diagnostic files | **65,934,575 bytes in 39 files** | Local disk writes; not uploaded by this analysis |

All 37 logged images were **2048×1152**, while the plugin frame metadata described **2560×1440** screenshots. This establishes a client resize between capture metadata and the retained model-facing image. The recorded images total 87.29 million pixels; the corresponding original full-monitor frames total 136.40 million pixels. The average logged PNG was about 2.09 MB.

The intended target windows occupied about **24.6% of the original frames' combined area**. The remaining 75.4% showed other desktop content. A crop-based observation path could therefore remove substantial visual area, though PNG bytes and model tokens will not necessarily decrease in the same proportion. Keeping coordinates aligned to the actual delivered image is also a correctness requirement; the model had to infer the 1.25 coordinate scale in this run.

**Actual network bytes are not established.** The values above count each logged tool-result image once after resizing. They do not measure the original local MCP PNG payload, repeated model request bodies, WebSocket compression, TLS overhead, retransmission, image ingestion, or browser page downloads. Cached tokens are not evidence that corresponding content was or was not sent over the wire. Browser resource-transfer logs and packet counters were not available for this task.

The host log separately records 23 successful model-catalog GET responses in the same wall-clock window, with 9,685,001 bytes of declared `Content-Length`. They lack reliable attribution to this particular task, so they are a **process-window observation**, excluded from the task's transfer totals and elapsed-time allocation.

There was no PDF, office-document, or note-format conversion. The note was plain Markdown. There was substantial image conversion:

- The exact runtime's code path implies **70 `grim` PNG captures and 70 ImageMagick PNG-to-RGB crop conversions**: 37 returned screenshots plus 33 non-scroll input validations. These counts follow from successful observed paths and source inspection; subprocess spans were not recorded.
- The client delivered 37 resized image results, implying image decoding/resampling/re-encoding outside the original capture path. The latency of that work was not separately recorded.
- Thirteen rejections wrote two PPM crops and one JSON metrics file each, totaling 65.93 MB. Disk-write latency was not instrumented.
- Code-defined 200ms post-action/focus sleeps total **4.8 seconds** across 21 executed input calls and three focus calls. Conditional 400ms focus-restoration sleeps cannot be counted from the available telemetry.

Capture, conversion, pixel comparison, subprocess work, input, fixed sleeps, and debug writes all share the **64.4-second non-approval tool envelope**. Assigning precise seconds to individual stages would be fabricated. Existing repository benchmarks demonstrate faster capture paths in controlled fixtures; they cannot retroactively allocate this run's time or establish its end-to-end speedup.

## What should change

The most consequential findings are the 48 serial model responses, 13 guarded rejections, a 160-second app-launch path, a corrupted long text insertion, and delivery of 37 complete desktop screenshots. Reinstalling the older build also meant that newer observation and timing features were absent from the experiment.

The assistant's own choices contributed: overly broad discovery, unnecessary skill and installation investigation, a guessed launch shortcut, an unhelpful window-size action, and prolonged decision gaps. Those costs belong in the retrospective alongside plugin and host behavior.

The accompanying [proposal](proposal.md) prioritizes correctness and removal of avoidable interaction cycles, followed by smaller observations and measurable host improvements. It preserves fresh-frame, identity, focus, geometry, expiry, and approval boundaries.
