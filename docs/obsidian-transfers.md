# Verified Obsidian note transfers

`obsidian_create_note` accepts a registered vault name or ID, a plain note name,
literal content, and a unique operation ID. It constructs the URI with percent
encoding, dispatches once, and verifies the saved file's exact UTF-8 bytes.
Spaces, literal plus signs, Unicode, ampersands and newlines remain distinct.

```json
{"vault":"My vault","name":"HN reflection","content":"Excerpt\n\nReflection.","operation_id":"hn-transfer-20260910-001","timeout_ms":5000}
```

The note is created at the vault root, using the URI `file` parameter so the
vault's default new-note folder cannot redirect it. Names cannot contain paths,
reserved filename/link characters or surrounding whitespace. The extension is
`.md`; an existing `.md` suffix is retained. Filenames are limited to 240 UTF-8
bytes and content to 32,000 bytes. No overwrite or append is requested.

Vaults are resolved through the existing Obsidian registry under
`$XDG_CONFIG_HOME/obsidian/obsidian.json` (normally `~/.config/obsidian/obsidian.json`).
Ambiguous names require a vault ID. The tool neither registers vaults nor changes
application settings.

## Outcomes and retries

- `verified`: exactly one eligible saved file matches the complete content hash.
  The result includes `note_path` and `application_accepted:verified_exact_content`.
- `unverified`: creation may still be pending, content may differ, or a file may
  have been moved outside the bounded search. Do not create another note.
- `ambiguous`: multiple newly appearing candidates have the expected content.
  Inspect them; the tool does not choose or delete one.
- `not_started`: the launcher could not spawn. The original operation remains a
  receipt rather than an instruction to retry. After fixing the launcher, a new
  operation may reserve this destination if it is still absent.

Read a pending operation without sending another URI:

```json
{"operation_id":"hn-transfer-20260910-001","timeout_ms":10000}
```

Send that request to `obsidian_note_status`. Repeating `obsidian_create_note`
with the **same operation ID and identical arguments** also only reconciles the
original attempt, including after an MCP reconnect or host restart. Reusing an ID
with different content is rejected. A different ID cannot reserve a destination
whose earlier operation might still be pending. Existing notes are never replaced.

Receipts live in a private, user-owned mode-0700 directory at
`$XDG_STATE_HOME/wayland-computer-use/notes` (normally
`~/.local/state/wayland-computer-use/notes`). `WCU_NOTE_STATE_DIR` can select a
private alternative for tests. All MCP instances must share this store for the
same guarantee. Receipts contain IDs, paths, hashes and launch metadata, not note
bodies. Destination reservations and receipts are persisted before dispatch,
under a cross-process lock. They do not expire automatically: removing or losing
them also loses the ability to reconcile uncertain operations safely.

Reconciliation reads only root-level candidates matching the requested basename
or Obsidian's numeric collision suffix, excludes preexisting candidates, rejects
symlinks/nonregular files, and bounds file bytes and directory entries. Arbitrary
renames, moves, post-save edits and modified receipt stores remain uncertain.
Verification proves matching saved content; it does not make asynchronous app
behavior atomic or prove which actor wrote the file.

## Launch acknowledgement

URI dispatch prefers the registered handler via `gio open`, with `xdg-open` as a
fallback when `gio` is unavailable. It waits at most 500 ms for the launcher to
exit and drains bounded, redacted diagnostics separately. A GUI child holding
stderr open does not delay the acknowledgement. A still-running launcher is not
killed or replayed. Exit code, process state and application acceptance are
separate fields; even a launcher error can coexist with a successfully saved note.

`open_uri` remains available for explicit URLs and bounded legacy Obsidian URIs.
It does not offer note receipts or saved-content verification. Ambiguous raw `+`
characters, literal whitespace and invalid percent escapes in Obsidian query
parameters are rejected. Prefer the structured action for note creation.

## Repeatable benchmarks

Prepare the harness before a measured run. The real-app integration test runs in
an isolated Obsidian profile and registered temporary vault, with a deliberately
different default new-note folder. It measures one cold creation and subsequent
warm creations, verifies special characters, then checks repeats and reconnects
without duplicate files. Only processes marked as belonging to its fixture are
stopped during cleanup; it does not close the user's Obsidian instance.

```bash
python3 scripts/benchmark_obsidian.py --trials 3 --output .dev/obsidian-benchmark/report.json
```

To test a published installation, add
`--server /path/to/plugin/scripts/dev_host.py`. Report setup time separately from
creation calls. Cold and warm timings are not interchangeable. These tests
exclude model inference and approval reviews.

The GUI-only fixture benchmark remains a separate route:

```bash
python3 scripts/benchmark_handoff_e2e.py --trials 3 --output .dev/gui-benchmark/report.json
```

A full model-driven HN → Obsidian comparison needs a fresh task with the updated
plugin, a fixed story/comment for repeated comparisons, recorded app starting
state, and the measurement client already prepared. Keep GUI-only and browser
connector/URI results separate. Record request-to-verified-artifact time, setup,
outer calls, images/pixels, returned bytes, failed outcomes and recovery; report
context/model differences rather than attributing all elapsed time to the plugin.
The live current-top-story task remains a separate smoke test.

The keyboard focus-race limitation remains unchanged. These app-specific actions
avoid keyboard injection; they do not weaken or replace GUI focus guards.

Encoding and `file`/`name` behavior follow the
[official Obsidian URI documentation](https://obsidian.md/help/uri).
