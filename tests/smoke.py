"""Read-only MCP integration check against the real desktop."""
import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

server = Path(__file__).resolve().parents[1] / "scripts" / "server.py"
environment = dict(os.environ)
if "--clean-session" in sys.argv:
    for key in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        environment.pop(key, None)
process = subprocess.Popen([sys.executable, str(server)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           text=True, env=environment)
counter = 0


def request(method, params=None):
    global counter
    counter += 1
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": counter, "method": method, "params": params or {}}) + "\n")
    process.stdin.flush()
    result = json.loads(process.stdout.readline())
    assert result["id"] == counter and "error" not in result, result
    return result["result"]


try:
    request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}})
    assert len(request("tools/list")["tools"]) == 8
    result = request("tools/call", {"name": "desktop_state"})
    assert not result["isError"], result
    state = json.loads(result["content"][0]["text"])
    print(json.dumps({"windows": len(state["windows"]), "tools": state["tools"],
                      "mouse_socket_available": state["mouse_socket_available"]}))
    for monitor in state["monitors"]:
        result = request("tools/call", {"name": "screenshot", "arguments": {"monitor": monitor["name"]}})
        assert not result["isError"], result
        meta = json.loads(result["content"][0]["text"])
        png = base64.b64decode(result["content"][1]["data"])
        assert struct.unpack(">II", png[16:24]) == (meta["width"], meta["height"])
        print(json.dumps({"monitor": monitor["name"], "image": [meta["width"], meta["height"]], "png_bytes": len(png)}))
    result = request("tools/call", {"name": "pointer", "arguments": {"frame_id": "invalid", "x": 0, "y": 0}})
    assert result["isError"]
    print("PASS: MCP handshake, tools, live screenshots, invalid-frame rejection")
finally:
    process.stdin.close()
    process.wait(timeout=10)
