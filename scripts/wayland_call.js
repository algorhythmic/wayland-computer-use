(async function waylandCall(sessionId, request) {
  // functions.exec adapter for the local PTY MCP client. One approved action,
  // one model round trip including its returned screenshot. Never replay input.
  // Outcome waits can last 30 seconds. Reserve transport/capture time in
  // addition, while retaining a hard bound even for invalid caller arguments.
  const args = request.arguments || {};
  const requestedWait = args.after?.timeout_ms ?? args.timeout_ms ?? 0;
  const waitMs = Number.isInteger(requestedWait) ? Math.min(30000, Math.max(0, requestedWait)) : 0;
  const deadline = Date.now() + 20000 + waitMs;
  let buffer = "";
  let chunk = await tools.write_stdin({
    session_id: sessionId, chars: JSON.stringify(request) + "\n",
    yield_time_ms: 1000, max_output_tokens: 10000
  });
  while (true) {
    buffer += chunk.output;
    if (buffer.length > 2000000) throw new Error("Oversized bridge response; inspect, do not retry input");
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, end).trim();
      buffer = buffer.slice(end + 1);
      if (!line.startsWith("{")) continue;
      let reply;
      try { reply = JSON.parse(line); } catch { continue; }
      if (reply.jsonrpc !== "2.0") continue; // Ignore PTY's echoed request.
      if (reply.error) { text(reply); return reply; }
      if (!reply.result || !Array.isArray(reply.result.content))
        throw new Error("Unexpected outstanding MCP reply; stop and inspect client state");
      for (const item of reply.result.content) {
        if (item.type === "image") {
          if (!/^\/tmp\/hush-mcp-[A-Za-z0-9_-]+\.png$/.test(item.saved_image || ""))
            throw new Error("Unexpected screenshot path; stop and inspect");
          const viewed = await tools.view_image({path: item.saved_image});
          image(viewed.image_url);
        } else if (item.type === "text") {
          text(item.text);
        }
      }
      text({isError: reply.result.isError, bridge: "single-round-trip"});
      return reply;
    }
    if (chunk.exit_code !== undefined || Date.now() >= deadline)
      throw new Error("No complete MCP result; input outcome unknown. Do not resend the action.");
    chunk = await tools.write_stdin({session_id: sessionId, chars: "",
                                    yield_time_ms: 1000, max_output_tokens: 10000});
  }
})
