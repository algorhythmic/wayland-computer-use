#!/usr/bin/env python3
"""Session discovery and bounded command execution, with no input operations."""
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

from . import trace


def run(args, data=None, timeout=15):
    started_ns = time.monotonic_ns()
    try:
        result = subprocess.run(args, input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except BaseException as exc:
        trace.record_exec(args[0], started_ns, error=exc)
        raise
    trace.record_exec(args[0], started_ns, result.returncode)
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



def tool(name, description, props, required, read=False):
    if "restore_focus" in props:
        description += " With restore_focus=true, this operation RESTORES FOCUS to target_title/target_window, revalidates, then performs the input; approve the whole operation."
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props,
                            "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read, "destructiveHint": not read,
                            "idempotentHint": read, "openWorldHint": True}}
