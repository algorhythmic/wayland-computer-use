#!/usr/bin/env python3
"""Dependency-free, newline-delimited stdio MCP server for a local Hyprland desktop."""
import base64
import json
import math
import os
from pathlib import Path
import re
import stat
import shutil
import struct
import subprocess
import sys
import time
import tempfile
import uuid


def run(args, data=None, timeout=15):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode:
        detail = result.stderr or result.stdout
        raise RuntimeError(detail.decode(errors="replace")[:1200] or "Command failed")
    return result.stdout


def owned_socket(path):
    try:
        info = path.stat()
        return info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode)
    except OSError:
        return False


def select_session(instances, signature=None, display=None):
    """Never guess between sessions or override an explicitly selected session."""
    candidates = [i for i in instances if isinstance(i, dict)
                  and re.fullmatch(r"[A-Za-z0-9_-]+", str(i.get("instance", "")))
                  and re.fullmatch(r"[A-Za-z0-9_-]+", str(i.get("wl_socket", "")))
                  and (not signature or i["instance"] == signature)
                  and (not display or i["wl_socket"] == display)]
    return candidates[0] if len(candidates) == 1 else None


def session_env():
    """Recover only desktop connection variables if the app lacks them."""
    keys = {"WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS"}
    # GUI-launched MCP processes can lack even the variables systemctl itself
    # needs to reach the user manager. Establish that connection first.
    if not os.environ.get("XDG_RUNTIME_DIR"):
        runtime = Path(f"/run/user/{os.getuid()}")
        try:
            info = runtime.stat()
            if info.st_uid == os.getuid() and stat.S_ISDIR(info.st_mode):
                os.environ["XDG_RUNTIME_DIR"] = str(runtime)
        except OSError:
            pass
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and owned_socket(runtime / "bus"):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + str(runtime / "bus")
    if not all(os.environ.get(k) for k in keys):
        try:
            for line in run(["systemctl", "--user", "show-environment"]).decode().splitlines():
                key, sep, value = line.partition("=")
                if sep and key in keys and not os.environ.get(key):
                    os.environ[key] = value
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass
    if not all(os.environ.get(k) for k in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY")):
        try:
            instances = json.loads(run(["hyprctl", "-j", "instances"]))
            if isinstance(instances, list):
                selected = select_session(instances, os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"),
                                          os.environ.get("WAYLAND_DISPLAY"))
                if selected and owned_socket(runtime / "hypr" / selected["instance"] / ".socket.sock") \
                        and owned_socket(runtime / selected["wl_socket"]):
                    for key, value in (("HYPRLAND_INSTANCE_SIGNATURE", selected["instance"]),
                                       ("WAYLAND_DISPLAY", selected["wl_socket"])):
                        if not os.environ.get(key):
                            os.environ[key] = value
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            pass
    os.environ.setdefault("YDOTOOL_SOCKET", f"/run/wayland-computer-use-{os.getuid()}/mouse.sock")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Expected integer between {low} and {high}")
    return value


def key_args(chord):
    aliases = {"CTRL": "ctrl", "CONTROL": "ctrl", "ALT": "alt", "SHIFT": "shift",
               "SUPER": "logo", "WIN": "logo", "LOGO": "logo"}
    if not isinstance(chord, str) or len(chord) > 100:
        raise ValueError("Invalid key chord")
    parts = chord.split("+")
    mods = [aliases[p.upper()] for p in parts[:-1]]
    key = parts[-1]
    key = {"ENTER": "Return", "ESC": "Escape", "SPACE": "space"}.get(key.upper(), key)
    if not re.fullmatch(r"[A-Za-z0-9_]+", key):
        raise ValueError("Use an XKB key name, such as Return, Tab, or Left")
    return [v for m in mods for v in ("-M", m)] + ["-k", key] + [v for m in reversed(mods) for v in ("-m", m)]


def text_content(value):
    return {"type": "text", "text": json.dumps(value, ensure_ascii=False)}


class ActionRejected(ValueError):
    def __init__(self, message, content):
        super().__init__(message)
        self.content = content


def pixel_difference(before, after):
    if before[:2] != after[:2]:
        return {"dimensions_changed": True, "before_size": before[:2], "after_size": after[:2]}
    width, height, first = before
    second = after[2]
    if len(first) != width * height * 3 or len(second) != len(first):
        raise ValueError("Invalid RGB crop")
    count = maximum = 0
    xmin, ymin, xmax, ymax = width, height, -1, -1
    for offset in range(0, len(first), 3):
        delta = max(abs(first[offset+c] - second[offset+c]) for c in range(3))
        if delta:
            count += 1
            maximum = max(maximum, delta)
            y, x = divmod(offset // 3, width)
            xmin, ymin, xmax, ymax = min(xmin, x), min(ymin, y), max(xmax, x), max(ymax, y)
    return {"dimensions_changed": False, "total_pixels": width * height,
            "changed_pixels": count, "max_channel_difference": maximum,
            "changed_bbox_xyxy": [xmin, ymin, xmax+1, ymax+1] if count else None}


def visual_guard(before, after, region=None):
    """Allow bounded render noise, never average away a high-contrast change.

    Region is a crop-relative exclusive rectangle covering the action location.
    Without a known location (keyboard input), apply the stricter rule everywhere.
    These are heuristic limits, not proof that UI meaning is unchanged.
    """
    metrics = pixel_difference(before, after)
    metrics["policy"] = {"global_channel_limit": 6 if region else 2,
                         "local_channel_limit": 2, "changed_fraction_limit": .02}
    if metrics["dimensions_changed"]:
        metrics["accepted"] = False
        return metrics
    width, height, first = before
    accepted = (metrics["max_channel_difference"] <= (6 if region else 2)
                and metrics["changed_pixels"] * 50 <= width * height)
    if region is not None:
        left, top, right, bottom = region
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("Action region outside validated interior; take a fresh screenshot")
        def crop(rgb):
            return (right-left, bottom-top, b"".join(
                rgb[(y*width+left)*3:(y*width+right)*3] for y in range(top, bottom)))
        local = pixel_difference(crop(first), crop(after[2]))
        metrics["action_region_xyxy"] = list(region)
        metrics["action_region_difference"] = local
        accepted = accepted and local["max_channel_difference"] <= 2 \
            and local["changed_pixels"] * 50 <= local["total_pixels"]
    metrics["accepted"] = accepted
    return metrics


def save_visual_debug(before, after, metrics):
    """Opt-in, private, bounded retention. Diagnostic failures never allow input."""
    location = os.environ.get("WAYLAND_CU_DEBUG_DIR")
    if not location:
        return None
    root = Path(location)
    if not root.is_absolute():
        raise ValueError("Debug directory must be absolute")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Debug directory must be owned by this user with mode 0700")
    if len(list(root.glob("rejection-*"))) >= 20:
        raise ValueError("Debug retention limit reached (20 captures); archive/remove them explicitly")
    folder = Path(tempfile.mkdtemp(prefix="rejection-", dir=root))
    for label, crop in (("before", before), ("after", after)):
        width, height, rgb = crop
        with os.fdopen(os.open(folder / (label + ".ppm"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
            file.write(f"P6\n{width} {height}\n255\n".encode() + rgb)
    with os.fdopen(os.open(folder / "metrics.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
        json.dump(metrics, file, indent=2)
    return str(folder)


class Desktop:
    def __init__(self):
        self.frames = {}

    def hypr(self, command):
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            raise RuntimeError("No unambiguous Hyprland session could be discovered. "
                               "Set HYPRLAND_INSTANCE_SIGNATURE and WAYLAND_DISPLAY for the MCP server. "
                               "Session sockets must be accessible to this process.")
        return json.loads(run(["hyprctl", "-j", command]))

    def dispatch(self, command, arg):
        # Hyprland's Lua config mode requires a dispatcher expression, not
        # the pre-0.55 text syntax. Only these two fixed templates are exposed.
        if command == "focuswindow" and re.fullmatch(r"address:0x[0-9a-fA-F]+", arg):
            expr = 'hl.dsp.focus({window="' + arg + '"})'
        elif command == "movecursor" and re.fullmatch(r"-?\d+ -?\d+", arg):
            x, y = map(int, arg.split())
            expr = f"hl.dsp.cursor.move({{x={x},y={y}}})"
        else:
            raise ValueError("Unsupported dispatcher or argument")
        reply = run(["hyprctl", "dispatch", expr]).decode().strip()
        if reply != "ok":
            raise RuntimeError(reply)

    def state(self):
        monitors = self.hypr("monitors")
        windows = self.hypr("clients")
        return {"monitors": monitors, "windows": [
            {k: w.get(k) for k in ("address", "class", "title", "monitor", "workspace", "at", "size", "mapped")}
            for w in windows], "active_window": self.hypr("activewindow"),
            "tools": {t: bool(shutil.which(t)) for t in ("hyprctl", "grim", "wtype", "ydotool")},
            "mouse_socket_available": os.access(os.environ["YDOTOOL_SOCKET"], os.W_OK)}

    @staticmethod
    def layout(monitors):
        return [(m["name"], m["x"], m["y"], m["width"], m["height"], m["scale"], m["transform"])
                for m in monitors]

    def screenshot(self, monitor=None):
        monitors = self.hypr("monitors")
        if not monitors:
            raise RuntimeError("No active monitors")
        m = next((m for m in monitors if m["name"] == monitor), None) if monitor else next(
            (m for m in monitors if m.get("focused")), monitors[0])
        if m is None:
            raise ValueError("Unknown monitor; use desktop_state")
        active = self.hypr("activewindow")
        before = active.get("address")
        target = self.target_record(active) if active.get("mapped") and active.get("monitor") == m.get("id") else None
        png = run(["grim", "-s", "1", "-o", m["name"], "-t", "png", "-l", "1", "-"])
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError("Invalid screenshot")
        width, height = struct.unpack(">II", png[16:24])
        visual = self.target_pixels(target, m, png) if target else None
        current = self.hypr("activewindow")
        after = current.get("address")
        if before != after or self.layout(monitors) != self.layout(self.hypr("monitors")):
            raise RuntimeError("Desktop changed during capture; take another screenshot")
        if target and self.target_record(current) != target:
            raise RuntimeError("Target changed during capture; take another screenshot")
        token = uuid.uuid4().hex
        self.frames = {token: {"time": time.monotonic(), "monitor": m, "width": width,
                              "height": height, "layout": self.layout(monitors), "active": after,
                              "target": target, "visual": visual}}
        return [text_content({"frame_id": token, "monitor": m["name"], "width": width,
                              "height": height, "origin": [m["x"], m["y"]],
                              "coordinates": "screenshot pixels", "active_window": after,
                              "target_window": target}),
                {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png).decode()}]

    @staticmethod
    def target_record(window):
        return {k: window.get(k) for k in ("address", "pid", "class", "initialClass", "title",
                                           "at", "size", "monitor", "workspace", "mapped")}

    @staticmethod
    def target_bounds(target, monitor, iw, ih):
        x, y = target["at"]
        w, h = target["size"]
        if w <= 16 or h <= 16:
            raise ValueError("Target too small for visual validation")
        mw, mh = monitor["width"], monitor["height"]
        if monitor["transform"] % 2:
            mw, mh = mh, mw
        sx, sy = iw / (mw / monitor["scale"]), ih / (mh / monitor["scale"])
        left = math.floor((x - monitor["x"] + 8) * sx)
        top = math.floor((y - monitor["y"] + 8) * sy)
        right = math.ceil((x - monitor["x"] + w - 8) * sx)
        bottom = math.ceil((y - monitor["y"] + h - 8) * sy)
        # Clip to the exact visible screenshot; never include padded offscreen pixels.
        left, top, right, bottom = max(0, left), max(0, top), min(iw, right), min(ih, bottom)
        if left >= right or top >= bottom:
            raise ValueError("Target has no visible interior on this monitor")
        return left, top, right, bottom

    def target_pixels(self, target, monitor, png=None):
        # Baseline pixels come from the SAME PNG returned to the agent.
        if png is None:
            png = run(["grim", "-s", "1", "-o", monitor["name"], "-t", "png", "-l", "1", "-"])
        iw, ih = struct.unpack(">II", png[16:24])
        left, top, right, bottom = self.target_bounds(target, monitor, iw, ih)
        cw, ch = right-left, bottom-top
        rgb = run(["magick", "png:-", "-crop", f"{cw}x{ch}+{left}+{top}",
                   "+repage", "-alpha", "off", "-depth", "8", "rgb:-"], png)
        if len(rgb) != cw * ch * 3:
            raise ValueError("Invalid RGB crop size")
        return cw, ch, rgb

    def action_region(self, frame, args):
        if "x" not in args:
            return None
        left, top, right, bottom = self.target_bounds(
            frame["target"], frame["monitor"], frame["width"], frame["height"])
        points = [(args["x"], args["y"])]
        if "end_x" in args:
            points.append((args["end_x"], args["end_y"]))
        if any(not (left <= x < right and top <= y < bottom) for x, y in points):
            raise ValueError("Action location outside validated window interior")
        # 32 screenshot pixels around a click; entire drag bounding box plus margin.
        return (max(left, min(x for x, y in points)-32)-left,
                max(top, min(y for x, y in points)-32)-top,
                min(right, max(x for x, y in points)+33)-left,
                min(bottom, max(y for x, y in points)+33)-top)

    def reject(self, message, monitor, details=None):
        self.frames.clear()
        content = [text_content({"error": message, "action_performed": False,
                                 "requires_review": True, **(details or {})})]
        try:
            content += self.screenshot(monitor)
        except Exception as exc:
            content.append(text_content({"screenshot_error": str(exc)}))
        raise ActionRejected(message, content)

    def check_target(self, frame):
        if subprocess.run(["pgrep", "-x", "hyprlock"], stdout=subprocess.DEVNULL).returncode == 0:
            raise ValueError("Desktop is locked")
        if self.layout(self.hypr("monitors")) != frame["layout"]:
            raise ValueError("Monitor layout changed")
        target = frame["target"]
        current = next((w for w in self.hypr("clients") if w["address"] == target["address"]), None)
        if not current or self.target_record(current) != target:
            raise ValueError("Target closed, moved, resized, or changed identity/title")

    def prepare(self, name, a):
        if not a.get("restore_focus", False):
            return self.guard(a["frame_id"])
        frame = self.frames.get(a["frame_id"])
        if not frame or not frame.get("target"):
            raise ValueError("A screenshot with a focused target window is required")
        target = frame["target"]
        if a.get("target_window") != target["address"] or a.get("target_title") != target["title"]:
            raise ValueError("Approved target address/title must match the screenshot target")
        try:
            self.check_target(frame)
            if self.hypr("activewindow").get("address") != target["address"]:
                self.dispatch("focuswindow", "address:" + target["address"])
                time.sleep(.4)
            self.check_target(frame)
            if self.hypr("activewindow").get("address") != target["address"]:
                raise ValueError("Focus changed again after restoration")
            if time.monotonic() - frame["time"] > 120:
                raise ValueError("Screenshot expired during approval; review the new screenshot")
            if name != "scroll":
                after = self.target_pixels(target, frame["monitor"])
                metrics = visual_guard(frame["visual"], after, self.action_region(frame, a))
                if not metrics["accepted"]:
                    details = {"visual_difference": metrics}
                    try:
                        folder = save_visual_debug(frame["visual"], after, metrics)
                        if folder:
                            details["debug_directory"] = folder
                    except Exception as exc:
                        details["debug_error"] = str(exc)
                    self.reject("Target pixels changed; review the new screenshot before input",
                                frame["monitor"]["name"], details)
            # Even scroll coordinates must stay inside the approved window.
            for xkey, ykey in (("x", "y"), ("end_x", "end_y")):
                if xkey in a:
                    x, y = self.point(frame, a[xkey], a[ykey])
                    tx, ty = target["at"]
                    w, h = target["size"]
                    if not (tx <= x < tx+w and ty <= y < ty+h):
                        raise ValueError("Input coordinates are outside the approved target")
            self.check_target(frame)
            self.guard(a["frame_id"])
        except ActionRejected:
            raise
        except ValueError as exc:
            self.reject(str(exc), frame["monitor"]["name"])
        return frame

    def guard(self, token):
        frame = self.frames.get(token)
        if not frame or time.monotonic() - frame["time"] > 120:
            raise ValueError("Frame missing, consumed, or expired; take a screenshot first")
        if self.layout(self.hypr("monitors")) != frame["layout"]:
            raise ValueError("Monitor layout changed; take a fresh screenshot")
        if self.hypr("activewindow").get("address") != frame["active"]:
            raise ValueError("Active window changed; take a fresh screenshot")
        # Omarchy uses hyprlock. This check supplements, not replaces, compositor isolation.
        if subprocess.run(["pgrep", "-x", "hyprlock"], stdout=subprocess.DEVNULL).returncode == 0:
            raise RuntimeError("Desktop is locked")
        return frame

    @staticmethod
    def point(frame, x, y):
        integer(x, 0, frame["width"] - 1)
        integer(y, 0, frame["height"] - 1)
        m = frame["monitor"]
        w, h = m["width"], m["height"]
        if m["transform"] % 2:
            w, h = h, w
        return (m["x"] + math.floor(x * w / m["scale"] / frame["width"]),
                m["y"] + math.floor(y * h / m["scale"] / frame["height"]))

    def move(self, point):
        self.dispatch("movecursor", f"{point[0]} {point[1]}")

    def mouse(self, *args):
        if not os.access(os.environ["YDOTOOL_SOCKET"], os.W_OK):
            raise RuntimeError("Mouse daemon unavailable; see README setup")
        run(["ydotool", *args])

    def call(self, name, a):
        if name == "desktop_state":
            return [text_content(self.state())]
        if name == "screenshot":
            return self.screenshot(a.get("monitor"))
        if name == "focus_window":
            address = a["address"]
            if address not in [w["address"] for w in self.hypr("clients") if w.get("mapped")]:
                raise ValueError("Unknown window address")
            self.frames.clear()
            self.dispatch("focuswindow", "address:" + address)
            time.sleep(.2)
            return self.screenshot()
        # Validate all action arguments before restoration, which is itself a mutation.
        validate(name, a)
        if name == "press_key":
            key_args(a["key"])
        if name == "type_text" and (len(a["text"]) > 8000 or "\x00" in a["text"]):
            raise ValueError("Text must contain at most 8000 characters and no NUL")
        if name == "scroll":
            integer(a["steps"], -20, 20)
        if name == "pointer":
            integer(a.get("count", 1), 1, 3)
        pending = self.frames.get(a["frame_id"])
        if pending:
            for xkey, ykey in (("x", "y"), ("end_x", "end_y")):
                if xkey in a:
                    self.point(pending, a[xkey], a[ykey])
        frame = self.prepare(name, a)
        started = False
        def recheck():
            if a.get("restore_focus", False):
                try:
                    self.check_target(frame)
                    if self.hypr("activewindow").get("address") != frame["active"]:
                        raise ValueError("Focus changed before input; action stopped")
                except ValueError as exc:
                    if not started:
                        self.reject(str(exc), frame["monitor"]["name"])
                    raise ValueError("Input interrupted after partial execution; inspect before retrying: " + str(exc))
        # Validate every argument before the first input event.
        if name == "pointer":
            point = self.point(frame, a["x"], a["y"])
            button = a.get("button", "left")
            code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2", "move": None}[button]
            count = integer(a.get("count", 1), 1, 3)
            self.frames.clear()
            self.move(point)
            recheck()
            if code:
                started = True
                self.mouse("click", "--repeat", str(count), "--next-delay", "100", code)
        elif name == "type_text":
            value = a["text"]
            if not isinstance(value, str) or len(value) > 8000 or "\x00" in value:
                raise ValueError("Text must contain at most 8000 characters and no NUL")
            self.frames.clear()
            recheck()
            started = True
            run(["wtype", "-"], value.encode())
        elif name == "press_key":
            args = key_args(a["key"])
            self.frames.clear()
            recheck()
            started = True
            run(["wtype", *args])
        elif name == "scroll":
            point = self.point(frame, a["x"], a["y"])
            steps = integer(a["steps"], -20, 20)
            axis = a.get("axis", "vertical")
            dx, dy = (0, steps) if axis == "vertical" else (steps, 0)
            self.frames.clear()
            self.move(point)
            recheck()
            started = True
            self.mouse("mousemove", "--wheel", "--", str(dx), str(dy))
        elif name == "drag":
            start = self.point(frame, a["x"], a["y"])
            end = self.point(frame, a["end_x"], a["end_y"])
            self.frames.clear()
            self.move(start)
            try:
                recheck()
                started = True
                self.mouse("click", "0x40")
                for i in range(1, 21):
                    recheck()
                    self.move(tuple(round(s + (e-s)*i/20) for s, e in zip(start, end)))
                    time.sleep(.015)
            finally:
                self.mouse("click", "0x80")
        else:
            raise ValueError("Unknown tool")
        time.sleep(.2)
        return self.screenshot(frame["monitor"]["name"])


S = {"type": "string"}
I = {"type": "integer"}
FRAME = {"frame_id": S,
         "restore_focus": {"type": "boolean", "default": False,
                           "description": "Explicitly approve restoring the named target's focus and performing this input in ONE execution. Default false rejects focus changes."},
         "target_window": {"type": "string", "description": "Required with restore_focus: screenshot target_window.address."},
         "target_title": {"type": "string", "description": "Required with restore_focus: screenshot target_window.title, displayed as the intended approval target."}}
XY = {"x": I, "y": I}


def tool(name, description, props, required, read=False):
    if "restore_focus" in props:
        description += " With restore_focus=true, this operation RESTORES FOCUS to target_title/target_window, revalidates, then performs the input; approve the whole operation."
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props,
                            "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read, "destructiveHint": not read,
                            "idempotentHint": read, "openWorldHint": True}}


TOOLS = [
    tool("desktop_state", "Inspect Hyprland monitors, windows, and input backend readiness.", {}, [], True),
    tool("screenshot", "Capture one monitor at logical resolution. Returns image and frame_id required for input.",
         {"monitor": S}, [], True),
    tool("focus_window", "Focus an existing window by its address from desktop_state and return a screenshot.",
         {"address": S}, ["address"]),
    tool("pointer", "Move/click at screenshot pixel coordinates, then return the updated screenshot.",
         {**FRAME, **XY, "button": {"type": "string", "enum": ["left", "right", "middle", "move"]}, "count": I},
         ["frame_id", "x", "y"]),
    tool("type_text", "Type literal UTF-8 text in focused app. Newlines may submit/execute. Does not append Enter.",
         {**FRAME, "text": S}, ["frame_id", "text"]),
    tool("press_key", "Press a key/chord, e.g. CTRL+A, Return, ALT+Tab. Returns updated screenshot.",
         {**FRAME, "key": S}, ["frame_id", "key"]),
    tool("scroll", "Scroll at screenshot coordinates. Signed wheel steps: positive up/right, negative down/left.",
         {**FRAME, **XY, "steps": I, "axis": {"type": "string", "enum": ["vertical", "horizontal"]}},
         ["frame_id", "x", "y", "steps"]),
    tool("drag", "Left-button drag between two points in one screenshot; release even on failure.",
         {**FRAME, **XY, "end_x": I, "end_y": I}, ["frame_id", "x", "y", "end_x", "end_y"]),
]


def validate(name, args):
    spec = next((t["inputSchema"] for t in TOOLS if t["name"] == name), None)
    if spec is None or not isinstance(args, dict):
        raise ValueError("Unknown tool or invalid arguments")
    if set(args) - spec["properties"].keys() or set(spec["required"]) - args.keys():
        raise ValueError("Unexpected or missing arguments")
    for k, v in args.items():
        p = spec["properties"][k]
        if (p["type"] == "string" and not isinstance(v, str)) or (p["type"] == "integer" and type(v) is not int):
            raise ValueError("Invalid argument type: " + k)
        if p["type"] == "boolean" and type(v) is not bool:
            raise ValueError("Invalid argument type: " + k)
        if "enum" in p and v not in p["enum"]:
            raise ValueError("Invalid choice: " + k)


def serve():
    session_env()
    desktop = Desktop()
    for line in sys.stdin.buffer:
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Expected JSON-RPC object")
            if "id" not in request:
                continue
            method = request.get("method")
            params = request.get("params", {})
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "wayland-computer-use", "version": "0.1.0"},
                          "instructions": "Use screenshots before input. Approval dialogs may steal focus: request ONE combined input with restore_focus=true and target_window/target_title from the screenshot. Describe it as Focus <title> and <action>. Do not loop separate refocus calls. Default false rejects focus changes. On action_performed=false, review the returned screenshot before a new approval. Frames expire after 120 seconds. Shared live desktop; do not disable approval gates."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                try:
                    name, args = params["name"], params.get("arguments", {})
                    validate(name, args)
                    result = {"content": desktop.call(name, args), "isError": False}
                except ActionRejected as exc:
                    result = {"content": exc.content, "isError": True}
                except Exception as exc:
                    result = {"content": [text_content({"error": str(exc)[:1400]})], "isError": True}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
        except Exception:
            rid = request.get("id") if isinstance(request, dict) else None
            print(json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32700, "message": "Invalid JSON-RPC request"}}), flush=True)


if __name__ == "__main__":
    serve()
