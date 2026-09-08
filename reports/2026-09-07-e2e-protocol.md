# Paired end-to-end trial protocol: old build versus current build

Purpose: measure what the plugin changes of September 7 do to a real
model-driven computer-use task, with the model, host, desktop, and task held
constant. This is the evidence step the retrospective, proposal and
observability evaluation all deferred to.

## What is compared

| | Build A (old) | Build B (current) |
|---|---|---|
| Runtime revision | `d58ff2a6…` legacy single-file release, the exact runtime the September 6 run used | latest bundle published from this checkout |
| Tools | `desktop_state`, `screenshot`, `focus_window`, `pointer`, `type_text`, `press_key`, `scroll`, `drag` | the same eight plus `observe_window`, `wait_for`, `stop_observing` |
| Server-side timing | none | `timings_ms` on every response, sidecar records |
| Host-side timing | host envelope per call | host envelope per call |

Both builds are loaded by the same `scripts/dev_host.py` from this checkout's
`.dev/releases`. `scripts/select_runtime.py <revision>` switches the pointer;
`--list` shows what is published. The host pins its tool contract at first
load, so switching between A and B requires reconnecting the MCP server.
This is a safety feature, not a limitation to work around.

The model and host are this Claude Code session, not the Codex host of
September 6. Absolute task times are therefore not comparable with the
retrospective; only A-versus-B differences within this session are.

## Recording

`WAYLAND_CU_TRACE_DIR=/home/david/.local/state/wayland-computer-use-trace`
is set in both local `.mcp.json` files (uncommitted). Each host process writes
`host-<time>-<pid>.jsonl` with one record per `tools/call`: tool, revision,
received and flushed monotonic nanoseconds, error flag, image count, response
bytes. Build B additionally writes `trace-<time>-<pid>.jsonl` from the runtime
with full spans and outcomes. Nothing in either file contains screen content,
typed text, titles or URLs.

`reports/2026-09-07-browser-obsidian-ab/compare.py <dir>` groups host records
by revision and reports per-tool call counts, error counts, median and p95
envelopes, image counts and response bytes, and joins runtime records where
present. Trial boundaries are marked by `desktop_state` calls at start and end.

## The task

Identical wording for every trial, given to the model verbatim:

> Using the Wayland computer-use tools: in the already open Chromium window,
> open Hacker News (news.ycombinator.com), open the article titled "<fixed
> title>", select and copy its first paragraph, switch to the already open
> Obsidian window, create a new note named `ab-trial-<n>`, paste the
> paragraph, then type a two-sentence source line and stop. Do not repair
> anything through the shell.

Fixed conditions per trial:

- Chromium and Obsidian already running, one window each, on the same monitor.
- The article is pinned to one URL for the whole experiment so page content
  does not change between trials.
- Vault `/home/david/Documents/Omacron`; notes named `ab-trial-<n>`, deleted
  after the experiment.
- Trials alternate A, B, A, B; ten pairs is the pilot size from the proposal.
- Each trial starts and ends with a `desktop_state` call.
- Any trial that needs shell repair is recorded as failed, not excluded.

## Reported per build

- Task completion, verified by reading the note file, not by screenshot.
- Wall time from first to last tool call.
- Tool calls, input attempts, guard rejections, screenshots delivered, image
  bytes.
- Median and p95 host envelope per tool.
- For B only: server total versus host envelope, giving the host's own share.

## Preconditions before the first trial

1. Reconnect the `wayland` MCP server in this session (`/mcp`), which starts
   the patched host with tracing enabled.
2. Confirm the selected revision with `scripts/select_runtime.py --list`.
3. Confirm the operator is present: the desktop is live and shared, input
   goes to whatever window is focused, and a stray click lands on real
   applications.
