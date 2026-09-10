# Application surfaces

Use these tools only within the user's chosen app/task. They dispatch or read
specific protocol operations, never arbitrary scripts or commands.

For an existing Obsidian vault, percent-encode each parameter independently:

```text
obsidian://new?vault=My%20vault&name=HN%20reflection&content=Excerpt%0A%0AMy%20comment
```

`open_uri` supports `obsidian://new` with `vault`, `name`, optional `content`,
and `obsidian://open` with `vault`, optional `file`. The URI handler must already
be registered. No overwrite/append, filesystem path, callback or arbitrary scheme
is accepted. Use a fresh unique name and verify the resulting note through the
app or an authorized file read; a successful dispatch is not a saved-note receipt.
[Obsidian URI documentation](https://obsidian.md/help/uri).

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
