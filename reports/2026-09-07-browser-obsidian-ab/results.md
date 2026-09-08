# Paired end-to-end pilot: old build versus current build, September 7, 2026

Four trials of the task in `../2026-09-07-e2e-protocol.md`, driven by this
Claude Code session through the development host, order B, A, A, B:

| Trial | Build | Revision | Note | Outcome |
|---|---|---|---|---|
| 1 | current (B) | `6abf769e…` | `ab-trial-1` | task complete, source line corrupted |
| 2 | old (A) | `d58ff2a6…` | `ab-trial-2` | task complete, source line corrupted |
| 3 | old (A) | `d58ff2a6…` | `ab-trial-3` | task complete, source line corrupted |
| 4 | current (B) | `6abf769e…` | `ab-trial-4` | task complete, source line corrupted |

Excerpt: the top comment by Animats on Hacker News item 49592375, selected by
one drag and copied with Ctrl+C. The pasted body was byte-identical across
all four notes. Nothing was repaired. The notes, the host and runtime trace
files, and `compare-output.json` are in this directory.

Same model, host, desktop, windows, article and wording for every trial. The
old build is the exact legacy release the September 6 run used, loaded by the
same host. Absolute times are for this session only and are not comparable
with the September 6 run, which used a different host and model.

## Per-tool host envelope (request received to response flushed)

| Tool | Old build median / p95 | Current build median / p95 | Ratio (median) |
|---|---:|---:|---:|
| `desktop_state` | 16.1 / 33.0 ms | 2.9 / 3.5 ms | 5.6× |
| `focus_window` | 433.5 / 434.5 ms | 106.1 / 119.5 ms | 4.1× |
| `press_key` | 489.0 / 507.0 ms | 81.0 / 103.2 ms | 6.0× |
| `drag` | 905.0 / 906.3 ms | 528.4 / 536.8 ms | 1.7× |
| `type_text` (10 and 156 chars) | 872.7 / 1206.5 ms | 488.2 / 832.0 ms | 1.8× |
| `screenshot` | not used | 72.9 / 74.1 ms | |

Counts: old build 21 calls over two trials, current build 28 calls over two
trials (see below). The old build's per-call floor is its fixed 200 ms
post-action sleep plus the process scans and `hyprctl` spawns removed today.
The current build's `drag` and long `type_text` are dominated by the input
backend itself: 451 ms of interpolated pointer moves and 750 ms of `wtype`.

## Per-trial totals

| Trial | Build | Calls | Screenshots | Response bytes | Tool time (sum of envelopes) | Wall, first to last call |
|---|---|---:|---:|---:|---:|---:|
| 1 | current | 14 | 10 | 27.2 MB | 2.28 s | 171 s |
| 2 | old | 11 | 9 | 22.5 MB | ≈5.5 s | 148 s |
| 3 | old | 10 | 9 | 22.5 MB | ≈5.5 s | 172 s |
| 4 | current | 14 | 10 | 27.3 MB | 2.25 s | 295 s |

Old-build tool time is 10.99 s across trials 2 and 3 combined; the split is
approximate because both ran in one host process.

Three conclusions follow.

1. **Local tool time fell by about 2.4× per task** (≈5.5 s to 2.3 s) with the
   same guards, matching the fixture measurements in `docs/latency.md`.
2. **Task wall time did not fall.** Tool time is 1 to 4% of wall time in every
   trial. The rest is the model deciding, reading screenshots, and writing
   the next call. Trial 4's 295 s includes extra verification turns by the
   operator and is not a plugin effect. This is the retrospective's finding
   again: model turns dominate, so removing a turn is worth more than any
   local millisecond.
3. **The current build cost two extra round trips per trial.** Removing the
   fixed 200 ms sleep exposed a race the sleep had been hiding: after Ctrl+N
   and after Enter, Obsidian changes its window title while the result
   capture is in flight, the capture is declined with "Target changed during
   capture", and the model must call `screenshot` again. Each such round trip
   is a model turn, roughly 10 s, which exceeds the local savings of the whole
   trial. Both key presses executed and both error responses carried full
   timing, so nothing was lost, but the protocol deliberately did not use the
   `after` wait condition that would have absorbed this. Two remedies, both
   cheap: retry the read-only result capture once when the target's title
   changed during it, and teach the agent to attach an `after` window
   condition to actions expected to retitle a window.

A related observation: after Ctrl+V the current build's result screenshot
showed an empty note although the file was already written, because the
capture (≈60 ms after input) preceded Electron's render. The old build's
200 ms sleep happened to cover it. Same remedy.

## Text corruption: reproduced four times, build-independent, deterministic

Every trial's typed 156-character source line lost characters; the pasted
body never did. Dropped characters by trial (0-based index in the line):

| Trial | Dropped | Distinct-character rank of each |
|---|---|---|
| 1 | `g`@116, `l`@129, `1`@131, `l`@153 | 37th, 38th, 39th, 38th |
| 2 | `g`@116, `l`@129, `l`@143, `l`@149 | 37th, 38th, 38th, 38th |
| 3 | `g`@116, `l`@129, `l`@143, `l`@149 | 37th, 38th, 38th, 38th |
| 4 | `g`@116, `l`@129, `l`@153 | 37th, 38th, 38th |

At the time of the pilot the pattern read as a cap on distinct characters:
counting the two leading newlines, every character introduced as the 37th or
later distinct character was dropped at every occurrence. The controlled
fixture (`tests/live_text_entry.py`, run afterwards) showed that reading was
wrong: 62 distinct alphanumerics and 32 long-named punctuation characters
typed exactly, while 120 × `a` followed by ` bcdefghij` lost every character
after the run. The cutoff is a keystroke count between 86 and 100 within one
`wtype` process, independent of time, newlines and key count; the pilot's
source lines simply introduced their last new characters after that point
(index 116 of the line, 118 keystrokes with the two newlines). `type_text` now
splits text into invocations of at most 40 characters; 24 of 24 corpus trials
and 100 of 100 consecutive proposal-corpus insertions were exact afterwards.
Details in `docs/latency.md`, "Typed text: the keystroke cutoff and its fix".

## What this pilot does and does not establish

- Establishes: per-operation local latency of the current build against the
  old one under identical conditions; the extra-round-trip regression when
  the agent does not use wait conditions; a deterministic, build-independent
  text corruption, since traced to a keystroke-count cutoff and fixed.
- Does not establish: any change in end-to-end task time (two pairs, model
  time dominates); a general false-positive rate for guards (no rejections
  occurred in any trial); anything about approval latency (none in this
  host).

Trial notes remain in the vault at `/home/david/Documents/Omacron/ab-trial-*.md`
and were copied here; they can be deleted from the vault.

## Batched trials 5 and 6 (current build, `run_steps`)

Same task, same session, same wording. The seven mechanical steps after the
copy ran as one `run_steps` call with window-title conditions between steps
(protocol, "Batched variant"). Chromium had been navigated away in the
meantime, so the article was restored first as setup, itself a three-step
sequence, and excluded from the trial counts.

| Trial | Calls | Screenshots | Response bytes | Tool time | Wall, first to last call | Note |
|---|---:|---:|---:|---:|---:|---|
| 1, unbatched | 14 | 10 | 27.2 MB | 2.28 s | 171 s | corrupted line |
| 4, unbatched | 14 | 10 | 27.3 MB | 2.25 s | 295 s | corrupted line |
| 5, batched | 5 | 3 | 7.7 MB | 1.88 s | 71 s | exact |
| 6, batched | 4 | 3 | 7.8 MB | 1.85 s | 68 s | exact |

Trial 6 shares its start marker with trial 5's end, as trials 2 and 3 did.
The sequence itself took 1.27 s and 1.23 s for all seven steps; every `after`
condition matched, the Obsidian title changes arriving 50 to 92 ms after the
key. No step was retried and no screenshot was taken between steps. Both
notes are byte-identical in body to trial 1 and exact in the source line:
the first fully correct notes of the day, since the 40-character `wtype`
segmentation is also in this build.

What changed and what did not:

- **Calls per task fell from 14 to 4 or 5**, and wall time by 2.5 to 4×.
  Every removed call was a model turn spent reading a screenshot of a state
  the sequence could verify itself.
- **Response payload fell from 27 MB to under 8 MB per task**, three images
  instead of ten, which also shrinks every later turn's context.
- **Tool time barely moved** (2.3 s to 1.9 s). The plugin was already fast;
  the turns were the cost. This is the pilot's first conclusion, now acted on.
- **The title-change race disappeared** because the title change became the
  condition between steps instead of an obstacle to the result capture.
- **Decision turns are unchanged.** Placing the drag still needed a
  screenshot, and the article restoration showed the limit of title
  conditions: the tab's title matched about 390 ms after Enter while the page
  was still painting, so a title is readiness of the window, not of its
  content. The drag waited for a fresh screenshot for that reason.

The setup sequence (new tab, type URL, Enter with a title condition) ran in
836 ms and is the shape of a browser-navigation recipe.
