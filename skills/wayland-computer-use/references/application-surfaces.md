# Application surfaces

Use these tools only within the user's chosen app/task. They dispatch or read
specific protocol operations, never arbitrary scripts or commands.

For note creation use `obsidian_create_note` with `vault` (registered name or ID),
`name` (plain note name), literal `content`, and a unique `operation_id`. It
percent-encodes internally, creates at the vault root, and verifies the exact
saved content. Do not manually construct a creation URI when this tool is available.

```json
{"vault":"My vault","name":"HN reflection","content":"Excerpt\n\nMy comment","operation_id":"hn-reflection-20260910-001","timeout_ms":5000}
```

A `verified` result includes the saved `note_path`. For `unverified` or `ambiguous`
outcomes, call `obsidian_note_status` with the same `operation_id`. Repeating the
same creation request only reconciles; it never dispatches again. Do not invent a
new ID to bypass an uncertain result. Durable receipts survive reconnects and
reserve the target before launch. Existing notes are never overwritten. Status
readback covers the requested root-level name and new numeric collision suffixes;
arbitrary moves/renames remain uncertain. Receipts store paths/hashes, not bodies.

`open_uri` still handles explicit http(s) and bounded Obsidian open/new URIs.
It reports launch acknowledgement separately from application completion; a
pending launcher is never killed or replayed. Raw Obsidian parameters must use
`%20` for spaces and `%2B` for literal plus signs. This legacy dispatch-only route
cannot verify a saved note or prevent duplicates across retries.

[Full transfer contract and repeatable benchmark](../../../docs/obsidian-transfers.md) ·
[Official Obsidian URI documentation](https://obsidian.md/help/uri).

For Chromium, prefer an available browser connector. WCU's optional `cdp_read`
uses an already running debugging browser at numeric loopback and the port in
`WCU_CDP_PORT`. Install `websockets>=15` in that MCP server's Python environment
when using CDP. Omit `target_id` to list page IDs/URLs. To read, supply its exact
`target_id`, `expected_url`, and a CSS `selector` obtained from page evidence.
The result contains bounded DOM text and links, not rendered-visibility proof.
It excludes script/style/template text and does not return form field values.
A page navigation during collection invalidates the read. No Runtime evaluation,
navigation, input, cookie inspection or remote debugging endpoint is exposed.
[DOM protocol](https://chromedevtools.github.io/devtools-protocol/tot/DOM/).

Neither tool enables debugging or rewrites launch configuration. If configuring
a browser for a requested test, use a separate profile and loopback-only debugging.
Chrome requires a non-default data directory for remote debugging of regular
Chrome from version 136.
[Chrome debugging changes](https://developer.chrome.com/blog/remote-debugging-port).

Chromium/Electron accessibility may require `--force-renderer-accessibility` at
launch. An existing process may ignore flags passed to a second launcher. Report
missing AT-SPI evidence; do not infer complete accessibility or restart the user's
working app just to populate its tree. Reconfigure launch flags only as part of
authorized desktop setup, and verify the tree after launch.
