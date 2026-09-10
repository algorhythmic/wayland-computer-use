# Outcome-first feedback verification

The local `wcu-tools-3` implementation returns deferred target frames, compact
text/accessibility evidence and execution ledgers by default. New `view_frame`,
`read_text`, `open_uri` and optional `cdp_read` tools support on-demand inspection
and application surfaces. The skill and host instructions describe the new
contract. Existing installed plugin connections have not been updated.

The disposable browser-to-note fixture completed six steps in one `run_steps`
call: copy selection, focus the note fixture, paste with exact text verification,
read back, and save with outcome verification. The saved artifact was checked
independently through the fixture's control channel.

| Final trial measurement | Result |
|---|---:|
| Tool calls after setup | 1 |
| Images delivered | 0 |
| Model-visible image pixels at tool boundary | 0 |
| Compact returned bytes, including host metadata | 5,681 |
| Local task pipeline time | 913.7 ms |
| Guard rejections in note flow | 0 |
| Saved artifact | Verified |

This is one deterministic fixture trial, with source selection, launch and initial
focus already prepared. It excludes model inference and approval reviews. It
is not a rerun of the full Hacker News → Obsidian task and does not establish
8–10 turns, a weekly-quota reduction, or a production end-to-end speedup.

Validation: 149 input tests (7 optional Braid tests skipped), 31 observer tests,
28 frozen-baseline tests, Node adapter checks, skill validation and frozen
comparison verification passed. An isolated headless Chromium/CDP test extracted
the selected excerpt and source link using a disposable profile. Obsidian URI
validation/dispatch is covered by tests; no note was created in a user's vault.

The additional desktop interruption probes expose the existing limit of segment
guards: in the natural focus race, 40 characters reached the disposable decoy
before the next guard rejected the rest. In the synchronized between-segment
probe, zero reached the decoy. The fixture's own cursor animation was disabled
to isolate these focus races; no user app settings were changed.

With cursor animation enabled, GTK's empty text field produced an 18-pixel bar
change and did not expose usable AT-SPI character extents. Input was withheld
with a deferred target crop and diff bounds. The caret exception therefore
remains conditional on trustworthy bounds; it does not classify every small bar
as harmless. Do not interpret a passing ledger as atomic focus isolation.

[Machine-readable measurements](2026-09-10-feedback-results.json) ·
[Contract, usage and activation](../docs/feedback.md)
