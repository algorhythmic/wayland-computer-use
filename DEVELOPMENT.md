# Same-chat development loop

The repository is now the consolidated development source for both servers.
Commands below describing `/home/david/plugins/` are this machine's existing
installation, which consolidation did not modify. For another checkout/install,
substitute its actual destination. Observer development lives in
`scripts/cu/` with a separate entry point in `wayland-desktop-observer/`.
That independent observer process is not hot-reloaded by the input host.
The root README is the current setup guide; historical benchmark reproduction
is documented in `benchmarks/PRESERVATION.md`.

## Activating the latency implementation

This change adds tool schemas and a bundle-aware host. Do not publish a new bundle
into an installation still running the old host: that host cannot load it, and an
existing tool contract cannot discover the added tools. Update the plugin files,
publish with the new host present, and reconnect both MCP servers through the
client's normal plugin flow. Existing approvals are unchanged. A source checkout
or a successful local publication does not update an already connected plugin.

For local verification without changing installed plugins:

```bash
python3 scripts/build_capture.py
python3 scripts/dev_publish.py --destination .
python3 scripts/benchmark_latency.py --focus-fixture
```

The last command explicitly focuses one disposable GTK fixture at startup and
aborts on later focus changes. It injects no keys/buttons and saves only metrics.
Omit `--focus-fixture` when allowing the fixture to receive focus naturally.
[Runtime design and tool examples](docs/latency.md).

## Lower-latency local-client operation

Prefer directly connected MCP tools when available: their input result already
contains the next screenshot. The local PTY client used in this development chat
instead saves each image to `/tmp/hush-mcp-*.png` and prints its path. For that
client, `scripts/wayland_call.js` is a function expression for `functions.exec`:
load its source into session storage, evaluate it, and invoke with the existing
PTY session ID and `{name, arguments}` request. It submits once, collects the
response, and displays its screenshot in the same execution. Keep exactly one
outstanding request per client; drain previous responses before switching to it.
On timeout or transport failure, stop and inspect—never resend input blindly.
Approval policy is unchanged. This adapter does not grant tools absent from a chat.

The previous implementation changed PNG compression level 6 to level 1. A local
six-capture interleaved benchmark measured 221–260 ms versus 975–1082 ms, with
roughly 15% larger images. Those are capture/encoding times, not total action or
model latency; network/image processing may offset some savings.

Do not batch speculative menu clicks. For a not-yet-observed dropdown, open it,
review its returned screenshot, then select the observed item. Semantic selection
via an accessibility tree would be a separate feature, not assumed support.

Run adapter tests with `node tests/test_wayland_call.js`.

This is local, agent-driven development within the user's requested task. It is
not an autonomous service that edits code, relaxes guards, or sends telemetry.

The MCP configuration starts `scripts/dev_host.py`. This stable stdio host loads
an explicitly published, SHA-256-identified runtime bundle. It checks
the published pointer before each request and switches implementations only
between requests. Host PID and the MCP connection stay intact; the implementation
and its in-memory screenshot frames are replaced, and old workers are closed.
Bundle manifests cover `server.py`, `cu/*.py`, and the optional capture binary.
Python dependencies execute from verified bytes with a fresh import namespace.
Legacy single-file releases remain readable by the new host. No signals or app restart.

## Iterate from this conversation

1. Inspect the reported failure and saved diagnostics; establish what actually
   changed. Add a regression test without committing private screen captures.
2. Make the smallest scoped implementation fix. Preserve approval, focus, target
   identity, geometry and frame-expiry checks. Don't automatically tune thresholds
   merely until a rejected action passes.
3. Run `python3 -m unittest discover -s tests -v` from the development repository.
4. Publish locally (this reruns the tests and refuses publication on failure):

   ```bash
   python3 /home/david/Work/wayland-computer-use/scripts/dev_publish.py \
     --destination /home/david/plugins/wayland-computer-use
   ```

   Writing the installed plugin may require the usual shell approval. This command
   does not modify approval policy, restart ChatGPT, or affect conversation history.
5. Call the connected plugin's `desktop_state`. Check that its appended
   `development_runtime.revision` matches `published_revision`. Until confirmed,
   do not claim the chat's connected plugin has loaded the update. An old server
   launched directly with `server.py` cannot reload and won't report this field.
6. Capture and review a new screenshot, then request the usual approval for the
   next action. Old frame IDs are deliberately invalid. Do not replay a failed
   click, key, or drag automatically. Stay in the same conversation.

All hosts pointing at this installation adopt the published implementation on
their next request. Finish any pending computer-use action before publication;
other chats' old screenshots will be invalidated too. Publication does not itself
inject input. Existing long-running actions finish before their host reloads.

## Boundaries and recovery

- Only runtime bundle implementation changes are hot-reloaded. Tool definitions
  (including descriptions/annotations), host changes, dependencies, `.mcp.json`,
  and agent instructions require normal installation/client rediscovery.
- The host verifies complete tool definitions against its startup contract and
  fails closed on mismatch. It does not silently keep using an old build.
- Failed tests leave the published pointer alone. Invalid code or checksum errors
  block calls; restore the intended tested source and republish to recover.
- `.dev/releases/` retains local builds, not screenshots. No automatic deletion.
  The hash verifies file identity, not that code is trustworthy. Only publish
  reviewed local source; importing Python can execute its top-level code.
- Initial activation requires launching this host once. Reinstalling a plugin
  cannot retrofit a running old Python process. A separate test chat can activate
  the installation without abandoning the original chat. Do not kill the app,
  edit its session database, or guess at private IPC protocols to force refresh.
- A developer in this session can run local regression/protocol tests and publish
  code with shell tools even if Wayland tools are absent from this chat. That is
  distinct from proving the app's existing MCP connection has adopted the build.

The official App Server API documents `config/mcpServer/reload` for refreshing
loaded threads, but this desktop installation did not expose the usual app-server
control socket during setup. No host-wide refresh was attempted.
Source: https://learn.chatgpt.com/docs/app-server
