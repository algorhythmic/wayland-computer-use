# Verified Obsidian transfer results

The installed `wcu-tools-4` MCP implementation created and verified notes in a
real, isolated Obsidian profile and vault. The structured tool percent-encodes
spaces, plus signs, Unicode, ampersands, newlines and other reserved characters.
Durable operation receipts prevent repeated dispatch across retries and reconnects.

Installed plugin: `0.1.0+codex.20260910081553`.
Runtime bundle: `1d1d404b9d33485abd81ac6063e768ed1ba5ac3ba0f023ebf7a9431223dfd926`.

| Installed-host measurement | Result |
|---|---:|
| Cold creation and exact saved-file verification (one trial) | 1071.1 ms |
| Warm creation and verification (median of two trials) | 255.3 ms |
| Creation tool calls per trial | 1 |
| Additional repeat/reconnect validation calls per trial | 3 |
| Images per creation | 0 |
| Duplicate notes | 0 |

Each note was verified byte-for-byte independently. The vault's default new-note
folder was set to a different directory; all notes were created at the requested
vault-root destination. Repeating the request, reading status after reconnect,
and repeating creation after reconnect all returned verification without another
dispatch. The temporary app/profile/vault were cleaned up after the test.

The launcher now acknowledges dispatch without waiting for inherited GUI pipes to
close, reports uncertain outcomes separately, and never kills or replays a pending
launcher. Known encoding errors are rejected before dispatch. Tests cover late
creation after timeout, content changes, collision suffixes, concurrent callers,
interrupted receipt writes, a busy receipt store, existing files, and symlinks.

Validation passed: 167 input tests (7 optional Braid tests skipped), 31 observer
tests, Node adapter checks, skill/plugin validation, and frozen comparison checks.

These are application pipeline timings, excluding model inference and approval
reviews. They do not establish the duration of a full Hacker News task. GUI focus
races remain unchanged. Receipt-store loss, arbitrary note moves/edits, and
ambiguous results require inspection; the tool does not retry uncertain creation.

[Transfer contract and reproduction](../docs/obsidian-transfers.md) ·
[Machine-readable measurements](2026-09-10-obsidian-transfer-results.json)
