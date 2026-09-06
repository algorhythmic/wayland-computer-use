"""Opt-in live MCP input test in a disposable native Wayland GTK4 window.

Run only with user authorization: this moves the real pointer and keyboard focus.
Requires python-gobject and GTK4 in addition to the plugin dependencies.
"""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib
    Gtk.init()
    window = Gtk.Window(title="Wayland Computer Use — disposable input test")
    window.set_default_size(800, 650)
    fixed = Gtk.Fixed()
    window.set_child(fixed)
    entry = Gtk.Entry()
    entry.set_size_request(600, 60)
    fixed.put(entry, 30, 30)
    canvas = Gtk.DrawingArea()
    canvas.set_size_request(1500, 1500)
    canvas.set_draw_func(lambda area, cr, w, h: (cr.set_source_rgb(.15, .4, .6), cr.paint()))
    viewport = Gtk.ScrolledWindow()
    viewport.set_size_request(600, 350)
    viewport.set_child(canvas)
    fixed.put(viewport, 30, 130)
    label = Gtk.Label(label="Disposable input test — no files or network actions")
    fixed.put(label, 30, 520)

    def emit(kind, *args):
        print(json.dumps([kind, *args]), flush=True)

    entry.connect("changed", lambda e: emit("text", e.get_text()))
    entry.connect("activate", lambda e: emit("enter", e.get_text()))
    click = Gtk.GestureClick()
    click.set_button(0)
    click.connect("pressed", lambda g, n, x, y: emit("click", g.get_current_button(), n))
    canvas.add_controller(click)
    positions = [0, 0]
    def scrolled(adjustment, axis):
        value = adjustment.get_value()
        delta = value - positions[axis]
        positions[axis] = value
        emit("scroll", delta if axis == 0 else 0, delta if axis == 1 else 0)
    viewport.get_hadjustment().connect("value-changed", scrolled, 0)
    viewport.get_vadjustment().connect("value-changed", scrolled, 1)
    drag = Gtk.GestureDrag()
    drag.connect("drag-end", lambda g, x, y: emit("drag", x, y))
    canvas.add_controller(drag)
    window.present()

    def ready():
        bounds = {}
        viewport.get_hadjustment().set_value(300)
        viewport.get_vadjustment().set_value(300)
        for name, widget in (("entry", entry), ("canvas", viewport)):
            ok, rect = widget.compute_bounds(window)
            assert ok
            bounds[name] = [rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()]
        emit("ready", bounds)
        return False

    GLib.timeout_add(800, ready)
    GLib.MainLoop().run()


def main():
    sys.path.insert(0, str(ROOT / "scripts"))
    import server
    server.session_env()
    original = json.loads(server.run(["hyprctl", "-j", "activewindow"]))
    env = dict(os.environ, GDK_BACKEND="wayland")
    gui = subprocess.Popen([sys.executable, __file__, "--fixture"], env=env,
                           stdout=subprocess.PIPE, text=True)
    transport = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["wayland"]
    mcp = subprocess.Popen([transport["command"], *transport["args"]],
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    events = queue.Queue()
    responses = queue.Queue()

    def read_lines(stream, target):
        for line in stream:
            target.put(json.loads(line))

    threading.Thread(target=read_lines, args=(gui.stdout, events), daemon=True).start()
    threading.Thread(target=read_lines, args=(mcp.stdout, responses), daemon=True).start()
    counter = 0

    def request(method, params):
        nonlocal counter
        counter += 1
        mcp.stdin.write(json.dumps({"jsonrpc": "2.0", "id": counter,
                                   "method": method, "params": params}) + "\n")
        mcp.stdin.flush()
        response = responses.get(timeout=20)
        assert response["id"] == counter and "error" not in response, response
        return response["result"]

    def call(name, **args):
        result = request("tools/call", {"name": name, "arguments": args})
        # An action may finish but its following capture can race focus changes.
        # Retry only the read-only capture, never repeat an input action.
        if result.get("isError") and "Desktop changed during capture" in str(result):
            time.sleep(.5)
            result = request("tools/call", {"name": "screenshot", "arguments": {}})
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    def expect(predicate):
        deadline = time.monotonic() + 5
        seen = []
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            seen.append(event)
            if predicate(event):
                print("PASS:", json.dumps(event), flush=True)
                return event
        raise AssertionError(f"Expected event not received; saw {seen}")

    try:
        bounds = expect(lambda e: e[0] == "ready")[1]
        request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "live-input-test", "version": "1"}})
        state = call("desktop_state")
        target = next(w for w in json.loads(server.run(["hyprctl", "-j", "clients"]))
                      if w["pid"] == gui.pid)
        address = target["address"]
        assert re.fullmatch(r"0x[0-9a-fA-F]+", address)
        selector = 'window="address:' + address + '"'
        server.run(["hyprctl", "dispatch", "hl.dsp.window.float({" + selector + ',action="set"})'])
        if "--monitor" in sys.argv:
            output = sys.argv[sys.argv.index("--monitor") + 1]
            assert output in [m["name"] for m in state["monitors"]]
            assert re.fullmatch(r"[A-Za-z0-9_-]+", output)
            server.run(["hyprctl", "dispatch", "hl.dsp.window.move({" + selector +
                        ',monitor="' + output + '",follow=true})'])
        time.sleep(.8)
        meta = call("focus_window", address=target["address"])
        time.sleep(.5)
        target = next(w for w in json.loads(server.run(["hyprctl", "-j", "clients"]))
                      if w["pid"] == gui.pid)
        print("Fixture:", json.dumps({k: target[k] for k in ("at", "size", "monitor")}), flush=True)
        monitor = next(m for m in state["monitors"] if m["name"] == meta["monitor"])
        assert monitor["scale"] == 1, "Live fixture currently expects scale 1"

        def point(name, dx=0, dy=0):
            x, y, w, h = bounds[name]
            return {"x": int(target["at"][0] - monitor["x"] + x + w/2 + dx),
                    "y": int(target["at"][1] - monitor["y"] + y + h/2 + dy)}

        def action(name, **args):
            nonlocal meta
            assert meta["active_window"] == target["address"], "Test window lost focus; stopping"
            meta = call(name, frame_id=meta["frame_id"], **args)

        if "--mouse-only" not in sys.argv and "--wheel-only" not in sys.argv:
            action("pointer", **point("entry"))
            action("press_key", key="CTRL+A")
            action("type_text", text="Wayland test — café")
            expect(lambda e: e == ["text", "Wayland test — café"])
            action("press_key", key="CTRL+A")
            action("type_text", text="Replacement verified")
            expect(lambda e: e == ["text", "Replacement verified"])
            action("press_key", key="Return")
            expect(lambda e: e == ["enter", "Replacement verified"])
        if "--wheel-only" not in sys.argv:
            for button, number in (("left", 1), ("right", 3), ("middle", 2)):
                action("pointer", **point("canvas"), button=button)
                expect(lambda e: e[0] == "click" and e[1] == number)
            action("pointer", **point("canvas"), count=2)
            expect(lambda e: e[0] == "click" and e[2] >= 2)
        end = point("canvas", 120, 70)
        action("drag", **point("canvas"), end_x=end["x"], end_y=end["y"])
        expect(lambda e: e[0] == "drag" and abs(e[1] - 120) < 5 and abs(e[2] - 70) < 5)
        for axis in (() if "--skip-wheel" in sys.argv else ("vertical", "horizontal")):
            signs = []
            for steps in (-3, 3):
                while not events.empty():
                    events.get_nowait()
                action("scroll", **point("canvas"), axis=axis, steps=steps)
                event = expect(lambda e: e[0] == "scroll" and e[2 if axis == "vertical" else 1] != 0)
                signs.append(event[2 if axis == "vertical" else 1])
            assert signs[0] * signs[1] < 0, "Wheel directions must be opposite"
        print("PASS: selected live input tests through configured MCP server; wheel skipped=" +
              str("--skip-wheel" in sys.argv), flush=True)
    finally:
        gui.terminate()
        gui.wait(timeout=5)
        mcp.terminate()
        mcp.wait(timeout=5)
        if original.get("address"):
            server.Desktop().dispatch("focuswindow", "address:" + original["address"])


if __name__ == "__main__":
    fixture() if "--fixture" in sys.argv else main()
