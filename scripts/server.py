#!/usr/bin/env python3
"""Dependency-free, newline-delimited stdio MCP server for a local Hyprland desktop."""
import base64
import hashlib
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

# Published builds load as unique packages so shared modules cannot survive a
# hot reload with stale state. Direct invocation/tests use the checkout package.
if __package__:
    from .cu.capture import Capturer
    from .cu.capture import Capture
    from .cu.pixels import crop, png_rgb, half_rgb
    from .cu.observation import Observer, Collector, CONDITION_SCHEMA, TIMEOUT, COMMON, validate_condition
    from .cu import observation
    from .cu import trace
    from .cu.execution import Deadline, Ledger, DEFAULT_BUDGET_MS
    from .cu.context_records import SnapshotStore, SCHEMA_VERSION
    from .cu.context_retrieval import context_for_task, validate_request, SessionEvidence, render, CONTRACT_VERSION
    from .cu.braid_client import BraidBackend
    from .cu.app_surfaces import cdp_read, validate_uri, surface_context, dispatch_uri
    from .cu.obsidian import NoteTransfers, validate_note, validate_operation
    from .cu.system import desktop_locked, hypr_query
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cu.capture import Capturer
    from cu.capture import Capture
    from cu.pixels import crop, png_rgb, half_rgb
    from cu.observation import Observer, Collector, CONDITION_SCHEMA, TIMEOUT, COMMON, validate_condition
    from cu import observation
    from cu import trace
    from cu.execution import Deadline, Ledger, DEFAULT_BUDGET_MS
    from cu.context_records import SnapshotStore, SCHEMA_VERSION
    from cu.context_retrieval import context_for_task, validate_request, SessionEvidence, render, CONTRACT_VERSION
    from cu.braid_client import BraidBackend
    from cu.app_surfaces import cdp_read, validate_uri, surface_context, dispatch_uri
    from cu.obsidian import NoteTransfers, validate_note, validate_operation
    from cu.system import desktop_locked, hypr_query


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
    return {"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(',', ':'))}


# Each wtype process extends its virtual-keyboard keymap when it meets a new
# character. Measured on Hyprland with GTK4 and Electron clients: after roughly
# 90 keystrokes in one process, every character introduced afterwards is
# dropped, regardless of timing, newlines or how many distinct keys exist
# (tests/live_text_entry.py). Bound each invocation well under that count.
TEXT_SEGMENT_CHARS = 40
TEXT_KEYSYM_LIMIT = TEXT_SEGMENT_CHARS  # Name kept for the live test's --no-split switch.


def text_segments(text, limit=None):
    """Split text into consecutive segments of at most ``limit`` characters."""
    limit = TEXT_KEYSYM_LIMIT if limit is None else limit
    if not text:
        return []
    if limit <= 0:
        return [text]
    return [text[i:i+limit] for i in range(0, len(text), limit)]


class ActionRejected(ValueError):
    def __init__(self, message, content):
        super().__init__(message)
        self.content = content


def argument_shape(name, a):
    """Numeric/categorical request attributes only: never text, keys, or titles."""
    if not isinstance(a, dict):
        return {}
    shape = {'restore_focus': a.get('restore_focus', False) is True, 'has_after': 'after' in a,
             'has_frame': 'frame_id' in a}
    if isinstance(a.get('text'), str):
        shape['text_len'] = len(a['text'])
    condition = a.get('condition') if isinstance(a.get('condition'), dict) else \
        (a['after'].get('condition') if isinstance(a.get('after'), dict) else None)
    if isinstance(condition, dict) and isinstance(condition.get('kind'), str):
        shape['condition_kind'] = condition['kind']
    for key in ('steps', 'count', 'timeout_ms', 'max_age_ms'):
        if type(a.get(key)) is int:
            shape[key] = a[key]
    if isinstance(a.get('channels'), list):
        shape['channel_count'] = len(a['channels'])
    return shape


def result_summary(content):
    """Sizes, frame provenance, guard metrics and wait outcomes from a response."""
    summary = {'model_visible_image_pixels': 0,
               'model_visible_bytes': len(json.dumps(content, ensure_ascii=False, separators=(',', ':')).encode()),
               'blocks': len(content), 'images': 0, 'image_base64_bytes': 0, 'text_bytes': 0, 'frames': []}
    for block in content:
        if block.get('type') == 'image':
            summary['images'] += 1
            summary['image_base64_bytes'] += len(block.get('data', ''))
            try:
                header = base64.b64decode(block.get('data', '')[:32])
                if header[:8] == b'\x89PNG\r\n\x1a\n':
                    w, h = struct.unpack('!II', header[16:24])
                    summary['model_visible_image_pixels'] += w*h
            except (ValueError, struct.error):
                pass
            continue
        text = block.get('text', '')
        summary['text_bytes'] += len(text.encode('utf-8'))
        if len(text) > 65536:
            continue
        try:
            body = json.loads(text)
        except ValueError:
            continue
        if not isinstance(body, dict):
            continue
        if 'frame_id' in body:
            summary['frames'].append({k: body.get(k) for k in ('capture_backend', 'fallback_reason', 'width',
                                                                'height', 'png_bytes', 'monitor', 'timings_ms') if k in body})
        if isinstance(body.get('visual_difference'), dict):
            summary['guard_metrics'] = body['visual_difference']
        for key in ('status', 'condition_met', 'wait_timed_out', 'fresh_sample', 'freshness_satisfied', 'age_ms',
                    'actionable', 'requires_review', 'screenshot_error', 'debug_error'):
            if key in body:
                summary.setdefault('observation', {})[key] = body[key] if key != 'status' else str(body[key])
    return summary


LANE_CONSTANTS = {}
LIGHT_PATH_PIXELS = 20000


def lane_constants(count):
    """Per-lane 0x8000 guard and 0x7fff mask for ``count`` 16-bit lanes, cached by size."""
    if count not in LANE_CONSTANTS:
        if len(LANE_CONSTANTS) >= 4:
            LANE_CONSTANTS.pop(next(iter(LANE_CONSTANTS)))
        LANE_CONSTANTS[count] = (int.from_bytes(b'\x80\x00'*count, 'big'), int.from_bytes(b'\x7f\xff'*count, 'big'))
    return LANE_CONSTANTS[count]


def max_channel_difference(first, second):
    """Exact max |a-b| over all bytes using 16-bit lanes in one big integer; no per-byte loop."""
    count = len(first)
    guard, low = lane_constants(count)
    lanes = bytearray(2*count)
    lanes[1::2] = first
    a = int.from_bytes(lanes, 'big')
    lanes[1::2] = second
    b = int.from_bytes(lanes, 'big')
    d1 = (a | guard) - b                      # 0x8000 + (a-b) per lane; the guard bit prevents borrows
    ge = ((d1 & guard) >> 15) * 0x7fff        # full-lane mask where a >= b
    magnitude = d1 & low
    absdiff = (magnitude & ge) | ((guard - magnitude) & ~ge & low)
    deltas = absdiff.to_bytes(2*count, 'big')[1::2]
    maximum, lo, hi = 0, 1, 255
    while lo <= hi:                           # binary search with C-speed scans
        mid = (lo+hi)//2
        if deltas.translate(None, bytes(range(mid))):
            maximum, lo = mid, mid+1
        else:
            hi = mid-1
    return maximum


def pixel_difference(before, after):
    """Exact changed-pixel count, max channel delta and bbox without a Python per-pixel loop.

    Change detection uses byte XOR; deltas are computed per changed pixel when few
    pixels changed (the only case that can still be accepted) and with lane
    arithmetic over the whole crop otherwise. Results equal the original loop.
    """
    if before[:2] != after[:2]:
        return {"dimensions_changed": True, "before_size": before[:2], "after_size": after[:2]}
    width, height, first = before
    second = after[2]
    if len(first) != width * height * 3 or len(second) != len(first):
        raise ValueError("Invalid RGB crop")
    pixels = width * height
    result = {"dimensions_changed": False, "total_pixels": pixels}
    if first == second:
        return {**result, "changed_pixels": 0, "max_channel_difference": 0, "changed_bbox_xyxy": None}
    xor = (int.from_bytes(first, 'big') ^ int.from_bytes(second, 'big')).to_bytes(len(first), 'big')
    changed = (int.from_bytes(xor[0::3], 'big') | int.from_bytes(xor[1::3], 'big')
               | int.from_bytes(xor[2::3], 'big')).to_bytes(pixels, 'big')
    count = pixels - changed.count(0)
    ymin = (pixels - len(changed.lstrip(b'\x00'))) // width
    ymax = (len(changed.rstrip(b'\x00')) - 1) // width
    xmin, xmax = width, -1
    for y in range(ymin, ymax+1):
        row = changed[y*width:(y+1)*width]
        stripped = row.lstrip(b'\x00')
        if stripped:
            xmin = min(xmin, width - len(stripped))
            xmax = max(xmax, len(row.rstrip(b'\x00')) - 1)
    if count <= LIGHT_PATH_PIXELS:
        maximum = 0
        for match in re.finditer(rb'[^\x00]', changed):
            offset = match.start()*3
            maximum = max(maximum, abs(first[offset]-second[offset]), abs(first[offset+1]-second[offset+1]),
                          abs(first[offset+2]-second[offset+2]))
    else:
        maximum = max_channel_difference(first, second)
    return {**result, "changed_pixels": count, "max_channel_difference": maximum,
            "changed_bbox_xyxy": [xmin, ymin, xmax+1, ymax+1]}


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
        self.capturer = Capturer(command=run)
        self.observer = None
        self.action_started = False
        self.action_completed_ns = None
        self.focus_restored = False
        self.timings = {}
        self.calls = 0
        self.trace = None
        self.ledger = self.deadline = None
        self.runtime_identity = {'kind': 'source', 'server_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        self.context_root = Path(os.environ.get('WCU_CONTEXT_ROOT', str(Path(__file__).resolve().parents[1]/'.dev/keyboard-context/normalized')))
        self.context_backend = None
        self.context_evidence = None
        self.context_diagnostics = None
        self.result_view = None
        self.result_target = None
        self.delivered_accessibility = None
        self.images = 'none'
        self.image_detail = 'half'
        self.result_failed = False
        self.settle_timeout_ms = 1500
        self.recorder = trace.Recorder(provenance=trace.provenance([globals().get('__file__')]))

    def close(self):
        if self.context_backend:
            self.context_backend.close()
        if self.observer:
            self.observer.close()
        self.capturer.close()
        self.recorder.close()
        self.frames.clear()

    def span(self, name, **attrs):
        return trace.span_or_null(self.trace, name, **attrs)

    def note(self, **attrs):
        if self.trace is not None:
            self.trace.note(**attrs)

    def observation_service(self):
        if self.observer is None:
            self.observer = Observer(Collector(capturer=self.capturer))
        return self.observer

    def warm(self):
        """Start the accessibility worker so its GI import precedes the first probe.

        It reads nothing until a probe and exits after 120 seconds without a scope.
        """
        self.observation_service().collector.accessibility.warm()

    def wait_focus(self, address, timeout=.4):
        end = time.monotonic()+timeout
        while self.hypr('activewindow').get('address') != address:
            remaining = end-time.monotonic()
            if remaining <= 0:
                raise ValueError('Focus changed again after restoration')
            time.sleep(min(.015, remaining))

    def hypr(self, command):
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            raise RuntimeError("No unambiguous Hyprland session could be discovered. "
                               "Set HYPRLAND_INSTANCE_SIGNATURE and WAYLAND_DISPLAY for the MCP server. "
                               "Session sockets must be accessible to this process.")
        return hypr_query(command, timeout=self.deadline.remaining(3) if self.deadline else 3)

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
        reply = run(["hyprctl", "dispatch", expr], timeout=self.deadline.remaining(3) if self.deadline else 3).decode().strip()
        if reply != "ok":
            raise RuntimeError(reply)

    def state(self):
        monitors = self.hypr("monitors")
        windows = self.hypr("clients")
        return {"monitors": monitors, "windows": [
            {k: w.get(k) for k in ("address", "class", "title", "monitor", "workspace", "at", "size", "mapped")}
            for w in windows], "active_window": self.hypr("activewindow"),
            "tools": {t: bool(shutil.which(t)) for t in ("hyprctl", "grim", "wtype", "ydotool")},
            "mouse_socket_available": os.access(os.environ["YDOTOOL_SOCKET"], os.W_OK),
            "trace": self.recorder.status(), 'runtime': self.capabilities()}

    def capabilities(self):
        return {'identity': self.runtime_identity, 'tool_contract_version': 'wcu-tools-4',
            'tool_contract_sha256': hashlib.sha256(json.dumps(TOOLS, sort_keys=True).encode()).hexdigest(),
            'context_schema': SCHEMA_VERSION, 'context_contract': CONTRACT_VERSION,
            'observer_version': hashlib.sha256(Path(observation.__file__).read_bytes()).hexdigest(),
            'client_loaded_skill_revision': None,
            'capabilities': ['guarded_sequences', 'segment_guards', 'execution_ledger', 'shared_deadline',
                             'context_for_task', 'explicit_window_transitions', 'scoped_accessibility',
                             'bounded_text_readback', 'readiness_conditions', 'result_views', 'original_image_detail', 'deferred_frames', 'image_delivery_policy',
                             'text_first_results', 'batch_readback', 'bounded_quiescence', 'application_surfaces', 'verified_obsidian_notes', 'durable_note_receipts'],
            'unsupported': ['focus_until', 'semantic_activation', 'arbitrary_code', 'asynchronous_input']}

    def task_context(self, args):
        if self.context_backend is None and os.environ.get('WCU_BRAID_EXECUTABLE') and os.environ.get('WCU_BRAID_SHA256'):
            self.context_backend = BraidBackend(os.environ['WCU_BRAID_EXECUTABLE'], self.context_root.parent/'braid', os.environ['WCU_BRAID_SHA256'])
        try:
            snapshot = SnapshotStore(self.context_root).load()
        except (OSError, ValueError):
            body = {'contract_version': CONTRACT_VERSION, 'status': 'catalog_unavailable',
                    'output': {'unit': 'utf8_bytes', 'renderer': 'canonical-json-utf8-v1', 'limit': args.get('max_bytes', 8192), 'bytes': 0}}
        else:
            body, self.context_diagnostics = context_for_task(snapshot, args, self.context_evidence, self.context_backend)
        surfaces = surface_context(args['intent'])
        if surfaces:
            body['app_surfaces'] = surfaces
            if len(render(body)) > args.get('max_bytes', 8192):
                del body['app_surfaces']
        return [{'type': 'text', 'text': render(body).decode()}]

    @staticmethod
    def layout(monitors):
        return [(m["name"], m["x"], m["y"], m["width"], m["height"], m["scale"], m["transform"])
                for m in monitors]

    def result_capture(self, monitor=None):
        """Post-action capture. Retry once when the target retitled or refocused mid-capture.

        Applications such as Obsidian change their window title right after an
        action; without a retry the agent must spend a round trip on a second
        screenshot. The retry is read-only and reported as ``result_retried``.
        """
        try:
            return self.capture_result_view(monitor)
        except RuntimeError as exc:
            if 'changed during capture' not in str(exc):
                raise
            self.result_retried = True
            self.note(result_retry=True)
            time.sleep(.05)
            return self.capture_result_view(monitor)

    def capture_result_view(self, monitor):
        policy = self.result_view or {'kind': 'target'}
        reason = None
        if policy['kind'] != 'monitor':
            active = self.hypr('activewindow')
            if not self.result_target or self.identity(active) != self.result_target:
                reason = 'target_changed_overview_required'
            else:
                # Layer-3 surfaces can extend outside a window crop. Unavailable
                # overlay evidence also requires an overview.
                try:
                    layers = self.hypr('layers')
                    if any(v.get('levels', {}).get('3') for v in layers.values()):
                        reason = 'overlay_overview_required'
                except Exception:
                    reason = 'overlay_coverage_unavailable'
            if reason is not None:
                return [text_content({'status': 'review_required', 'requires_review': True,
                    'overview_required': True, 'reason': reason, 'image_available': False,
                    'next_observation': 'Explicit screenshot for monitor overview', 'result_view': policy})]
            if reason is None:
                content, sample = self.observation_service().observe(active['address'], channels=['metadata', 'pixels', 'accessibility'],
                    images=False, return_sample=True, timeout_ms=self.deadline.milliseconds(3000) if self.deadline else 3000)
                if not sample.get('capture') or json.loads(content[0]['text']).get('freshness_satisfied') is not True:
                    raise RuntimeError('Result crop unavailable')
                geometry = sample['geometry']
                capture = sample['capture']
                if policy['kind'] == 'region':
                    left, top, right, bottom = policy['box']
                    rgb = crop(capture.rgb, capture.width, policy['box'])
                    capture = Capture(right-left, bottom-top, rgb, capture.started_ns,
                        capture.completed_ns, capture.backend, presentation_ns=capture.presentation_ns)
                    geometry = [geometry[0]+left, geometry[1]+top, right-left, bottom-top]
                result = self.capture_content(capture, sample['monitor'], self.target_record(sample['target']),
                    self.layout(sample['state']['monitors']), sample['target']['address'], geometry, True)
                meta = json.loads(result[0]['text'])
                meta['result_view'] = policy
                meta['accessibility'] = sample['state'].get('accessibility', {'status': 'unavailable'})
                meta['revision'] = sample.get('revision')
                self.frames[meta['frame_id']]['accessibility'] = meta['accessibility']
                previous = self.delivered_accessibility
                if previous and previous[0] == self.result_target and previous[1]:
                    meta['accessibility'] = {'mode': 'delta', 'since_revision': previous[1],
                        'changes': observation.accessibility_delta(previous[2], meta['accessibility'])}
                self.delivered_accessibility = (dict(self.result_target), sample.get('revision'),
                    sample['state'].get('accessibility', {'status': 'unavailable'}))
                result[0] = text_content(meta)
                return result
        result = self.screenshot(monitor)
        if result and reason:
            meta = json.loads(result[0]['text'])
            meta['result_view'] = {'kind': 'monitor', 'fallback_reason': reason}
            result[0] = text_content(meta)
        return result

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
        if desktop_locked():
            raise RuntimeError('Desktop is locked')
        capture = self.capturer.capture(m, timeout=self.deadline.remaining(3) if self.deadline else 3)
        current = self.hypr("activewindow")
        after = current.get("address")
        if before != after or self.layout(monitors) != self.layout(self.hypr("monitors")):
            raise RuntimeError("Desktop changed during capture; take another screenshot")
        if target and self.target_record(current) != target:
            raise RuntimeError("Target changed during capture; take another screenshot")
        if desktop_locked():
            raise RuntimeError('Desktop locked during capture')
        return self.capture_content(capture, m, target, self.layout(monitors), after)

    @staticmethod
    def mapping(monitor, geometry=None):
        if geometry is None:
            return monitor
        x, y, width, height = geometry
        return dict(monitor, x=x, y=y, width=width, height=height, scale=1, transform=0)

    def capture_content(self, capture, monitor, target, layout, active, geometry=None, shared=False):
        width, height = capture.width, capture.height
        visual = self.target_pixels(target, monitor, capture, geometry) if target else None
        token = uuid.uuid4().hex
        app_class = (target or {}).get('class', '').casefold()
        app = {'chromium': 'chromium', 'md.obsidian.obsidian': 'obsidian', 'obs': 'obs',
               'com.obsproject.studio': 'obs', 'com.mitchellh.ghostty': 'ghostty', 'org.gnome.nautilus': 'nautilus'}.get(app_class)
        self.context_evidence = SessionEvidence(token, {'focused_app': app, 'focused_window': active} if app else {'focused_window': active}, capture.started_ns)
        self.frames = {token: {"time": capture.started_ns/1e9, "monitor": monitor, "width": width,
                              "height": height, "layout": layout, "active": active,
                              "target": target, "visual": visual, "geometry": geometry,
                              "shared_observation": shared}}
        frame = self.frames[token]
        frame['capture'] = capture
        frame['image_delivered'] = False
        return self.deliver_frame(token, self.images in ('target', 'monitor') or
                                  self.images == 'on_failure' and (self.result_failed or
                                      bool(self.ledger and self.ledger.stop_reason)))

    def deliver_frame(self, token, deliver=True, detail=None):
        frame = self.frames.get(token)
        if not frame or time.monotonic()-frame['time'] > 120:
            raise ValueError('Frame missing, consumed, or expired; observe again. Viewing never refreshes its age.')
        capture = frame['capture']
        detail = detail or self.image_detail
        width, height, rgb = capture.width, capture.height, capture.rgb
        if detail != 'original':
            width, height, rgb = half_rgb(width, height, rgb) if deliver else ((width+1)//2, (height+1)//2, None)
        # A different presentation has a different coordinate capability; old IDs
        # cannot silently acquire a new scale.
        if frame.get('image_delivered') and (width, height) != (frame['view_width'], frame['view_height']):
            new_token = uuid.uuid4().hex
            self.frames = {new_token: frame}
            token = new_token
            if self.context_evidence:
                self.context_evidence = SessionEvidence(token, self.context_evidence.facts, capture.started_ns)
        frame.update(view_width=width, view_height=height, view_scale=1 if detail == 'original' else 2)
        if deliver:
            frame['image_delivered'] = True
        monitor, geometry = frame['monitor'], frame['geometry']
        meta = {'frame_id': token, 'monitor': monitor['name'], 'width': width, 'height': height,
                'source_size': [capture.width, capture.height], 'source_pixels_per_view_pixel': frame['view_scale'],
                'origin': geometry[:2] if geometry else [monitor['x'], monitor['y']],
                'coordinates': 'view pixels; mapped to retained full-resolution guard',
                'active_window': frame['active'], 'target_window': frame['target'],
                'capture_started_ns': capture.started_ns, 'capture_backend': capture.backend,
                'expires_at_ns': capture.started_ns+120_000_000_000,
                'remaining_ms': max(0, int((120-(time.monotonic()-frame['time']))*1000)),
                'image_available': True, 'image_delivered': deliver,
                'view_tool': {'name': 'view_frame', 'arguments': {'frame_id': token, 'detail': detail}},
                'timings_ms': {'capture_ms': (capture.completed_ns-capture.started_ns)/1e6}}
        if capture.presentation_ns is not None:
            meta['presentation_ns'] = capture.presentation_ns
        if getattr(capture, 'fallback_reason', None):
            meta['fallback_reason'] = capture.fallback_reason
        blocks = []
        if deliver:
            tick = time.monotonic_ns()
            png = png_rgb(width, height, rgb)
            meta['png_bytes'] = len(png)
            meta['timings_ms']['encode_ms'] = (time.monotonic_ns()-tick)/1e6
            blocks.append({'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(png).decode(),
                           '_meta': {'codex/imageDetail': 'original'}})
        return [text_content(meta), *blocks]

    def observation_content(self, args):
        args = {k: v for k, v in args.items() if k not in ('images', 'detail')}
        content, sample = self.observation_service().observe(**args, images=False, return_sample=True)
        meta = json.loads(content[0]['text'])
        capture = sample.get('capture')
        if capture and meta['freshness_satisfied'] and time.monotonic_ns()-capture.started_ns < 120_000_000_000:
            target = {k: v for k, v in sample['target'].items() if k != 'id'}
            if self.images == 'monitor':
                next_frame = self.screenshot(sample['monitor']['name'])
            else:
                next_frame = self.capture_content(capture, sample['monitor'], target,
                    self.layout(sample['state']['monitors']), sample['state']['active_window'], sample['geometry'], True)
            token = json.loads(next_frame[0]['text'])['frame_id']
            self.frames[token]['accessibility'] = sample['state'].get('accessibility', {})
            self.delivered_accessibility = (self.identity(target), sample.get('revision'), sample['state'].get('accessibility', {}))
            meta.update(actionable=True, input_frame_id=token)
            content[0] = text_content(meta)
            content += next_frame
        return content

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

    def target_pixels(self, target, monitor, capture=None, geometry=None):
        # Validation and delivered PNG derive from the SAME immutable RGB capture.
        capture = capture or self.capturer.capture(monitor, geometry, timeout=self.deadline.remaining(3) if self.deadline else 3)
        iw, ih = capture.width, capture.height
        left, top, right, bottom = self.target_bounds(target, self.mapping(monitor, geometry), iw, ih)
        cw, ch = right-left, bottom-top
        return cw, ch, crop(capture.rgb, iw, (left, top, right, bottom))

    def action_region(self, frame, args):
        if "x" not in args:
            return None
        left, top, right, bottom = self.target_bounds(
            frame["target"], self.mapping(frame["monitor"], frame.get('geometry')), frame["width"], frame["height"])
        points = [(args["x"], args["y"])]
        if "end_x" in args:
            points.append((args["end_x"], args["end_y"]))
        points = [(x*frame.get('view_scale', 1),
                   y*frame.get('view_scale', 1)) for x, y in points]
        if any(not (left <= x < right and top <= y < bottom) for x, y in points):
            raise ValueError("Action location outside validated window interior")
        # 32 screenshot pixels around a click; entire drag bounding box plus margin.
        return (max(left, min(x for x, y in points)-32)-left,
                max(top, min(y for x, y in points)-32)-top,
                min(right, max(x for x, y in points)+33)-left,
                min(bottom, max(y for x, y in points)+33)-top)

    def guarded_pixels(self, frame, after, region=None):
        metrics = visual_guard(frame['visual'], after, region)
        box = metrics.get('changed_bbox_xyxy')
        if metrics['accepted'] or not box or box[2]-box[0] > 3 or box[3]-box[1] > 64:
            return metrics
        before = frame.get('accessibility', {})
        focused = before.get('focused_control') or {}
        caret = focused.get('caret')
        if before.get('focus_status') != 'unique' or not caret or focused.get('protected'):
            return metrics
        # Tiny bars alone are not proof of a caret. Require unchanged, complete
        # AT-SPI focus/offset/extents on both sides, and no change outside them.
        evidence = self.observation_service().collector.accessibility.probe(
            frame['target']['pid'], frame['target']['title'])
        current = evidence.get('focused_control') or {}
        if evidence.get('focus_status') != 'unique' or any(current.get(k) != focused.get(k)
                for k in ('ref', 'role', 'name', 'states', 'protected', 'caret')):
            return metrics
        mapping = self.mapping(frame['monitor'], frame.get('geometry'))
        bounds = self.target_bounds(frame['target'], mapping, frame['width'], frame['height'])
        scale_x = frame['width']/(mapping['width']/mapping['scale'])
        scale_y = frame['height']/(mapping['height']/mapping['scale'])
        tx, ty = frame['target']['at']
        left, top, right, bottom = caret['box']
        expected = [math.floor((tx+left-mapping['x'])*scale_x)-bounds[0],
                    math.floor((ty+top-mapping['y'])*scale_y)-bounds[1],
                    math.ceil((tx+right-mapping['x'])*scale_x)-bounds[0],
                    math.ceil((ty+bottom-mapping['y'])*scale_y)-bounds[1]]
        if expected[0] <= box[0] < box[2] <= expected[2] and expected[1] <= box[1] < box[3] <= expected[3]:
            metrics.update(accepted=True, ignored_change='verified_focused_caret', caret_box_xyxy=expected)
        return metrics

    def reject(self, message, monitor, details=None):
        self.frames.clear()
        content = [text_content({"error": message, "action_performed": False,
                                 "requires_review": True, **(details or {})})]
        try:
            with self.span('recovery'):
                self.result_failed = True
                content += self.result_capture(monitor)
        except Exception as exc:
            content.append(text_content({"screenshot_error": str(exc)}))
        raise ActionRejected(message, content)

    def check_target(self, frame):
        if desktop_locked():
            raise ValueError("Desktop is locked")
        if self.layout(self.hypr("monitors")) != frame["layout"]:
            raise ValueError("Monitor layout changed")
        target = frame["target"]
        current = next((w for w in self.hypr("clients") if w["address"] == target["address"]), None)
        if not current or self.target_record(current) != target:
            raise ValueError("Target closed, moved, resized, or changed identity/title")

    def prepare(self, name, a):
        pending = self.frames.get(a['frame_id'])
        if pending and pending.get('target'):
            self.result_target = self.identity(pending['target'])
        if pending and 'x' in a and not pending.get('image_delivered', True):
            raise ValueError('Coordinate input requires viewing this frame with view_frame first')
        if not a.get("restore_focus", False):
            frame = self.guard(a["frame_id"])
            self.note(frame_age_ms=(time.monotonic()-frame["time"])*1000,
                      shared_observation=bool(frame.get('shared_observation')))
            if frame.get('shared_observation'):
                try:
                    self.check_target(frame)
                    with self.span('visual_check'):
                        after = self.target_pixels(frame['target'], frame['monitor'], geometry=frame.get('geometry'))
                        metrics = self.guarded_pixels(frame, after, self.action_region(frame, a))
                    if not metrics['accepted']:
                        self.reject('Target pixels changed; review before input', frame['monitor']['name'], {'visual_difference': metrics})
                    self.check_target(frame)
                    self.guard(a['frame_id'])
                except ValueError as exc:
                    if isinstance(exc, ActionRejected):
                        raise
                    self.reject(str(exc), frame['monitor']['name'])
            if frame.get('target') and 'at' in frame['target']:
                self.check_target(frame)
            return frame
        frame = self.frames.get(a["frame_id"])
        if not frame or not frame.get("target"):
            raise ValueError("A screenshot with a focused target window is required")
        target = frame["target"]
        if a.get("target_window") != target["address"] or a.get("target_title") != target["title"]:
            raise ValueError("Approved target address/title must match the screenshot target")
        self.note(frame_age_ms=(time.monotonic()-frame["time"])*1000, shared_observation=False)
        try:
            self.check_target(frame)
            if self.hypr("activewindow").get("address") != target["address"]:
                with self.span('focus_restore'):
                    self.dispatch("focuswindow", "address:" + target["address"])
                    self.focus_restored = True
                    self.wait_focus(target['address'])
            self.check_target(frame)
            if self.hypr("activewindow").get("address") != target["address"]:
                raise ValueError("Focus changed again after restoration")
            if time.monotonic() - frame["time"] > 120:
                raise ValueError("Screenshot expired during approval; review the new screenshot")
            if name != "scroll":
                with self.span('visual_check'):
                    after = self.target_pixels(target, frame["monitor"], geometry=frame.get('geometry'))
                    metrics = self.guarded_pixels(frame, after, self.action_region(frame, a))
                if not metrics["accepted"]:
                    details = {"visual_difference": metrics}
                    try:
                        with self.span('debug_write'):
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
        if desktop_locked():
            raise RuntimeError("Desktop is locked")
        return frame

    @staticmethod
    def point(frame, x, y):
        integer(x, 0, frame.get("view_width", frame["width"]) - 1)
        integer(y, 0, frame.get("view_height", frame["height"]) - 1)
        m = Desktop.mapping(frame["monitor"], frame.get('geometry'))
        w, h = m["width"], m["height"]
        if m["transform"] % 2:
            w, h = h, w
        return (m["x"] + math.floor(x * frame.get("view_scale", 1) * w / m["scale"] / frame["width"]),
                m["y"] + math.floor(y * frame.get("view_scale", 1) * h / m["scale"] / frame["height"]))

    def move(self, point):
        self.dispatch("movecursor", f"{point[0]} {point[1]}")

    def mouse(self, *args, release=False):
        if not os.access(os.environ["YDOTOOL_SOCKET"], os.W_OK):
            raise RuntimeError("Mouse daemon unavailable; see README setup")
        run(["ydotool", *args], timeout=2 if release else self.deadline.remaining(15) if self.deadline else 15)

    def call(self, name, a):
        self.calls += 1
        self.trace = trace.Trace('request', tool=str(name)[:40], seq=self.calls, **argument_shape(name, a))
        self.action_started, self.action_completed_ns, self.focus_restored, self.timings = False, None, False, {}
        self.text_segments = None
        self.result_retried = False
        self.input_started = False
        self.ledger = self.deadline = None
        self.result_view = a.get('result_view') if isinstance(a, dict) else None
        pending = self.frames.get(a.get('frame_id')) if isinstance(a, dict) else None
        self.result_target = self.identity(pending['target']) if pending and pending.get('target') else None
        self.images = a.get('images', 'monitor' if name == 'screenshot' else 'none') if isinstance(a, dict) else 'none'
        self.image_detail = a.get('detail', 'half') if isinstance(a, dict) else 'half'
        if self.images == 'monitor':
            self.result_view = {'kind': 'monitor'}
        self.result_failed = False
        self.settle_timeout_ms = a.get('settle_timeout_ms', 1500) if isinstance(a, dict) else 1500
        previous = trace.activate(self.trace)
        try:
            validate(name, a)
            if name == 'run_steps':
                self.validate_steps(a['steps'])
            if name in ('press_key', 'type_text', 'pointer', 'scroll', 'drag', 'focus_window', 'run_steps', 'open_uri'):
                self.deadline = Deadline(a.get('duration_ms', DEFAULT_BUDGET_MS))
                self.ledger = Ledger(a['steps'] if name == 'run_steps' else [{'action': name}])
                if name != 'run_steps':
                    self.ledger.begin(0)
            content = self._call(name, a)
        except ActionRejected as exc:
            self.finish(exc.content, 'rejected', exc)
            raise
        except Exception as exc:
            if self.ledger:
                if self.ledger.current and self.ledger.current['status'] in ('pending', 'verifying'):
                    self.ledger.current['status'] = 'interrupted'
                self.ledger.stop_reason = type(exc).__name__
            if self.action_started or self.ledger:
                # Input may have partially executed. Never suggest an automatic retry.
                message = type(exc).__name__ if self.input_started else str(exc)[:240]
                content = [text_content({'error': message, 'error_code': type(exc).__name__,
                    'action_performed': self.ledger.performed() if self.ledger else 'unknown',
                    'action_completed_ns': self.action_completed_ns, 'requires_review': True})]
                self.recover(content)
                self.finish(content, 'error', exc)
                raise ActionRejected(str(exc), content) from exc
            self.finish(None, 'error', exc)
            raise
        finally:
            trace.activate(previous)
        return self.finish(content, 'ok')

    def finish(self, content, status, error=None):
        """Close the request span on every path; keep timing in the response and sidecar."""
        current = self.trace
        current.close(error)
        self.timings = current.durations_ms()
        performed = self.ledger.performed() if self.ledger else (True if self.action_completed_ns else ('unknown' if self.action_started else False))
        if self.ledger and status != 'ok' and not self.ledger.stop_reason:
            self.ledger.stop_reason = type(error).__name__ if error else status
        if self.ledger and not content:
            content = [text_content({})]
        if content and content[0]['type'] == 'text' and current.root.get('attrs', {}).get('tool') != 'context_for_task':
            metadata = json.loads(content[0]['text'])
            if self.ledger:
                metadata.update(sequence=self.ledger.summary(), action_performed=performed,
                                action_completed_ns=self.action_completed_ns)
            if status == 'ok' and performed is True:
                metadata.update(action_performed=True, action_completed_ns=self.action_completed_ns)
                if self.text_segments:
                    metadata['text_segments'] = self.text_segments
                if self.result_retried:
                    metadata['result_retried'] = True
            metadata['timings_ms'] = {**metadata.get('timings_ms', {}), **self.timings}
            content[0] = text_content(metadata)
        elif content is not None and performed is True:
            content.insert(0, text_content({'action_performed': True, 'action_completed_ns': self.action_completed_ns,
                                            'timings_ms': self.timings}))
        if self.recorder.enabled:
            self.recorder.write({'kind': 'request', 'trace_id': current.id, 'wall_start_s': current.wall_started,
                'start_ns': current.root['start_ns'], 'end_ns': current.root['end_ns'], **current.root.get('attrs', {}),
                'outcome': {'status': status, 'action_performed': performed, 'action_started': self.action_started,
                            'action_completed_ns': self.action_completed_ns, 'focus_restored': self.focus_restored,
                            'error_type': type(error).__name__ if error is not None else None,
                            'reason': (type(error).__name__ if self.input_started else str(error)[:trace.REASON_LIMIT]) if error is not None else None},
                'timings_ms': self.timings, 'spans': current.spans,
                'result': result_summary(content) if content else None})
        return content

    def recover(self, content):
        """A fresh read never replays input. Capture failure preserves the ledger."""
        self.frames.clear()
        self.result_failed = True
        previous = self.deadline
        self.deadline = Deadline(5000)
        try:
            with self.span('recovery'):
                content += self.result_capture()
        except Exception as exc:
            content.append(text_content({'recovery_error': type(exc).__name__, 'image_available': False}))
        finally:
            self.deadline = previous

    @staticmethod
    def identity(window):
        return {k: window.get(k) for k in ('address', 'pid', 'class', 'initialClass') if window.get(k) is not None}

    def intended_target(self, frame):
        if frame.get('target'):
            return self.identity(frame['target'])
        found = [w for w in self.hypr('clients') if w.get('address') == frame['active'] and w.get('mapped')]
        if len(found) != 1:
            raise ValueError('Intended target unavailable')
        return self.identity(found[0])

    def recheck_target(self, target):
        if self.deadline:
            self.deadline.remaining()
        if desktop_locked():
            raise ValueError('Desktop is locked')
        found = [w for w in self.hypr('clients') if w.get('address') == target.get('address') and w.get('mapped')]
        if len(found) != 1 or any(found[0].get(k) != v for k, v in target.items()):
            raise ValueError('Intended target identity changed')
        if self.hypr('activewindow').get('address') != target.get('address'):
            raise ValueError('active_window_changed')

    def _call(self, name, a):
        if name == 'context_for_task':
            return self.task_context(a)
        if name == 'cdp_read':
            return [text_content(cdp_read(**a))]
        if name == 'obsidian_note_status':
            return [text_content(NoteTransfers().status(**a))]
        if name == 'obsidian_create_note':
            if desktop_locked():
                raise ValueError('Desktop is locked')
            def starting():
                if desktop_locked():
                    raise ValueError('Desktop locked before note dispatch')
                self.action_started = self.input_started = True
            result = NoteTransfers().create(**a, before_dispatch=starting)
            self.action_started = result['dispatched_this_call']
            if result['action_performed'] is True:
                self.action_completed_ns = time.monotonic_ns()
            return [text_content(result)]
        if name == 'open_uri':
            if desktop_locked():
                raise ValueError('Desktop is locked')
            self.frames.clear()
            self.input_started = self.action_started = True
            self.ledger.injecting()
            launch = dispatch_uri(a['uri'], wait_ms=min(500, self.deadline.milliseconds(500)))
            if not launch['spawned']:
                self.input_started = self.action_started = False
                self.ledger.current.update(injection='not_started', in_flight_unknown=False, status='interrupted')
                self.ledger.stop_reason = 'launcher_not_started'
            else:
                self.ledger.submitted()
                self.action_completed_ns = time.monotonic_ns()
                self.ledger.completed(self.action_completed_ns)
                self.ledger.current['status'] = 'done'
            return [text_content({'status': 'dispatched' if launch['status'] == 'acknowledged' else 'dispatch_uncertain',
                'application_accepted': 'unverified', 'launch': launch, 'retry_safe': False,
                'requires_review': launch['status'] != 'acknowledged'})]
        if name == 'view_frame':
            return self.deliver_frame(a['frame_id'], detail=a.get('detail', 'half'))
        if name == 'read_text':
            return self.observation_content({**a, 'channels': ['metadata', 'accessibility']})
        if name in ('observe_window', 'wait_for'):
            with self.span('wait' if name == 'wait_for' else 'observe'):
                return self.observation_content(a)
        if name == 'stop_observing':
            return self.observer.stop() if self.observer else [text_content({'stopped': True, 'history_cleared': True})]
        if name == "desktop_state":
            return [text_content(self.state())]
        if name == "screenshot":
            with self.span('result'):
                return self.screenshot(a.get("monitor"))
        if name == 'run_steps':
            return self.run_steps(a)
        if name == "focus_window":
            address = a["address"]
            if address not in [w["address"] for w in self.hypr("clients") if w.get("mapped")]:
                raise ValueError("Unknown window address")
            self.frames.clear()
            self.action_started = True
            self.ledger.injecting()
            self.dispatch("focuswindow", "address:" + address)
            self.wait_focus(address)
            self.result_target = self.identity(self.hypr('activewindow'))
            self.action_completed_ns = time.monotonic_ns()
            self.ledger.submitted()
            self.ledger.completed(self.action_completed_ns)
            self.ledger.current['status'] = 'done'
            self.settle(self.result_target, self.ledger.current)
            with self.span('result'):
                return self.result_capture()
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
        if 'after' in a:
            validate_condition(a['after']['condition'])
            if a['after']['condition']['kind'] not in ('window', 'accessible', 'accessible_absent', 'text_equals'):
                raise ValueError('Use a separate wait_for with scoped/baseline evidence for this condition')
            if a['after']['condition']['kind'] == 'accessible' and not (pending or {}).get('target'):
                raise ValueError('Accessible outcome wait requires a screenshot with a focused target')
        with self.span('guard'):
            frame = self.prepare(name, a)
        target = self.intended_target(frame)
        self.result_target = dict(target)
        if self.result_view is None and frame.get('geometry'):
            self.result_view = {'kind': 'target'}
        def recheck():
            self.recheck_target(target)
            if not self.input_started and (a.get('restore_focus', False) or frame.get('shared_observation')):
                self.check_target(frame)
        with self.span('input'):
            self.perform(name, a, frame, recheck)
        if 'after' in a:
            condition = a['after']['condition']
            window = None if condition['kind'] == 'window' else target.get('address')
            self.ledger.current['status'] = 'verifying'
            with self.span('wait'):
                result = self.observation_content({'window': window, 'condition': condition,
                    'timeout_ms': self.deadline.milliseconds(a['after'].get('timeout_ms', 5000)), 'after_action': self.action_completed_ns,
                    'channels': ['metadata'] if condition['kind'] == 'window' else ['metadata', 'accessibility']})
            outcome = json.loads(result[0]['text'])
            self.ledger.current['verification'] = 'matched' if outcome.get('condition_met') else 'failed'
            self.ledger.current['status'] = 'done' if outcome.get('condition_met') else 'after_timeout'
            if outcome.get('condition_met') and condition['kind'] == 'text_equals':
                self.ledger.current['application_accepted'] = 'verified_text'
            if not outcome.get('condition_met'):
                self.ledger.current['status'] = 'after_timeout'
                self.ledger.stop_reason = 'after_not_met'
            if not any('input_frame_id' in json.loads(block.get('text', '{}')) for block in result if block['type'] == 'text'):
                with self.span('result'):
                    result += self.result_capture(frame['monitor']['name'])
            return result
        self.settle(target, self.ledger.current)
        with self.span('result'):
            return self.result_capture(frame["monitor"]["name"])

    def perform(self, name, a, frame, recheck):
        """Check before every segment; submission is distinct from app acceptance."""
        self.input_started = False
        entry = self.ledger.current
        self.action_completed_ns = None
        def submit(callback):
            recheck()
            self.frames.clear()
            self.input_started = self.action_started = True
            self.ledger.injecting()
            callback()
            self.ledger.submitted()
        def backend(args, data=None):
            return run(args, data, timeout=self.deadline.remaining(15))
        if name == 'type_text':
            segments = text_segments(a['text'])
            entry['segments_total'] = len(segments)
            self.note(text_segments=len(segments))
            for segment in segments:
                submit(lambda: backend(['wtype', '-'], segment.encode()))
            self.text_segments = len(segments)
        elif name == 'press_key':
            submit(lambda: backend(['wtype', *key_args(a['key'])]))
        elif name in ('pointer', 'scroll', 'drag'):
            point = self.point(frame, a['x'], a['y'])
            submit(lambda: self.move(point))
            if name == 'pointer':
                code = {'left': '0xC0', 'right': '0xC1', 'middle': '0xC2', 'move': None}[a.get('button', 'left')]
                if code:
                    submit(lambda: self.mouse('click', '--repeat', str(a.get('count', 1)), '--next-delay', '100', code))
            elif name == 'scroll':
                dx, dy = (0, a['steps']) if a.get('axis', 'vertical') == 'vertical' else (a['steps'], 0)
                submit(lambda: self.mouse('mousemove', '--wheel', '--', str(dx), str(dy)))
            else:
                end = self.point(frame, a['end_x'], a['end_y'])
                pressed = False
                try:
                    # A failing press may have reached the backend; always release.
                    recheck()
                    pressed = True
                    submit(lambda: self.mouse('click', '0x40'))
                    for i in range(1, 21):
                        submit(lambda: self.move(tuple(round(x+(y-x)*i/20) for x, y in zip(point, end))))
                        time.sleep(min(.015, self.deadline.remaining()))
                finally:
                    if pressed:
                        self.mouse('click', '0x80', release=True)
        else:
            raise ValueError('Unknown input action')
        self.action_completed_ns = time.monotonic_ns()
        self.ledger.completed(self.action_completed_ns)
        entry['status'] = 'done'

    # ----- Deterministic sequences -------------------------------------------------
    STEP_ACTIONS = ('press_key', 'type_text', 'focus_window', 'wait', 'wait_for', 'read_text', 'pointer', 'scroll')
    CONTROL_KEYS = {'expect', 'expect_timeout_ms', 'after', 'transition', 'condition', 'timeout_ms', 'read_text', 'a11y_scope'}
    STEP_KEYS = {'transition', 'action', 'key', 'text', 'address', 'x', 'y', 'button', 'count', 'steps', 'axis',
                 'expect', 'expect_timeout_ms', 'after', 'condition', 'timeout_ms', 'read_text', 'a11y_scope'}

    def validate_steps(self, steps):
        if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
            raise ValueError('steps must be a list of 1 to 12 steps')
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or set(step)-self.STEP_KEYS or step.get('action') not in self.STEP_ACTIONS:
                raise ValueError(f'Invalid step {index}')
            action = step['action']
            payload = {k: v for k, v in step.items() if k not in self.CONTROL_KEYS | {'action'}}
            if action not in ('wait', 'wait_for', 'read_text'):
                validate(action, payload if action == 'focus_window' else {'frame_id': 'validation', **payload})
            elif payload:
                raise ValueError('Read steps have no input fields')
            if action == 'wait_for' and 'condition' not in step:
                raise ValueError('wait_for step requires condition')
            if 'condition' in step:
                if action != 'wait_for':
                    raise ValueError('Only wait_for steps accept condition')
                validate_condition(step['condition'])
                if step['condition']['kind'] not in ('window', 'accessible', 'accessible_absent', 'text_equals'):
                    raise ValueError('Batch wait_for requires window or accessibility evidence')
            if 'timeout_ms' in step:
                integer(step['timeout_ms'], 1, 30000)
            for selector in ('read_text', 'a11y_scope'):
                if selector in step:
                    observation.validate('observe', {'window': 'validation', selector: step[selector]})
            if 'transition' in step and (step['transition'] != 'matched_window' or
                    step.get('after', {}).get('condition', {}).get('kind') != 'window' or
                    step['after']['condition'].get('focused') is not True):
                raise ValueError('transition requires a focused window after condition')
            if action in ('pointer', 'scroll') and index != 0:
                raise ValueError('Coordinate actions are only allowed as the first step, on the reviewed frame')
            if action == 'press_key':
                key_args(step.get('key'))
            if action == 'type_text' and (not isinstance(step.get('text'), str) or not step['text'] or len(step['text']) > 8000 or '\x00' in step['text']):
                raise ValueError(f'Step {index}: text must be 1 to 8000 characters without NUL')
            if action == 'focus_window' and not re.fullmatch(r'0x[0-9a-fA-F]+', str(step.get('address', ''))):
                raise ValueError(f'Step {index}: focus_window requires a window address')
            if action == 'wait' and 'after' not in step:
                raise ValueError(f'Step {index}: wait requires after')
            if action == 'scroll':
                integer(step.get('steps'), -20, 20)
            if action == 'pointer':
                integer(step.get('count', 1), 1, 3)
            for key in ('expect',):
                if key in step:
                    validate_condition(step[key])
                    if step[key]['kind'] not in ('window', 'accessible', 'accessible_absent', 'text_equals'):
                        raise ValueError(f'Step {index}: expect requires window or accessible evidence')
            if 'expect_timeout_ms' in step:
                integer(step['expect_timeout_ms'], 1, 30000)
            if 'after' in step:
                if not isinstance(step['after'], dict) or set(step['after'])-{'condition', 'timeout_ms'} or 'condition' not in step['after']:
                    raise ValueError(f'Step {index}: invalid after')
                validate_condition(step['after']['condition'])
                if step['after']['condition']['kind'] not in ('window', 'accessible', 'accessible_absent', 'text_equals'):
                    raise ValueError(f'Step {index}: after requires window or accessible evidence')
                if 'timeout_ms' in step['after']:
                    integer(step['after']['timeout_ms'], 1, 30000)

    def await_condition(self, condition, timeout_ms, after_action=None, target=None, **selectors):
        args = {'condition': condition, 'timeout_ms': self.deadline.milliseconds(timeout_ms)}
        if after_action is not None:
            args['after_action'] = after_action
        if condition['kind'] != 'window':
            args['window'] = target['address'] if target else None
            args['channels'] = ['metadata', 'accessibility']
        else:
            args['channels'] = ['metadata']
        args.update(selectors)
        body = json.loads(self.observation_content(args)[0]['text'])
        return body

    def settle(self, target, entry):
        """Wait for bounded pixel quiescence; never replay input or claim acceptance."""
        if self.settle_timeout_ms == 0:
            entry['readiness'] = 'not_requested'
            return True
        self.recheck_target(target)
        observer = self.observation_service()
        timeout = self.deadline.milliseconds(self.settle_timeout_ms)
        end = time.monotonic()+timeout/1000
        previous = None
        stable_since = None
        delay = .025
        while True:
            remaining = end-time.monotonic()
            if remaining <= 0:
                entry.update(readiness='timeout', status='readiness_timeout')
                self.ledger.stop_reason = 'quiescence_timeout'
                return False
            content, sample = observer.observe(target['address'], channels=['metadata', 'pixels'],
                images=False, return_sample=True, after_action=self.action_completed_ns,
                timeout_ms=max(1, int(remaining*1000)))
            self.recheck_target(target)
            capture = sample.get('capture')
            if capture is None or json.loads(content[0]['text']).get('freshness_satisfied') is not True:
                entry.update(readiness='unavailable', status='readiness_unavailable')
                self.ledger.stop_reason = 'quiescence_unavailable'
                return False
            signature = (sample['geometry'], capture.rgb)
            now = time.monotonic()
            if previous == signature:
                if now-stable_since >= .1:
                    entry['readiness'] = 'quiescent'
                    return True
            else:
                previous, stable_since = signature, now
            time.sleep(min(delay, max(0, end-now), self.deadline.remaining()))
            delay = min(.1, delay*1.5)

    def checked_match(self, body, target, transition=None):
        if not body.get('condition_met'):
            return False
        evidence = body.get('condition_evidence') or {}
        matched = evidence.get('target')
        if not matched or not evidence.get('revision'):
            raise ValueError('Matched target evidence unavailable')
        identity = self.identity(matched)
        if transition == 'matched_window':
            target.clear()
            target.update(identity)
        elif identity != target:
            raise ValueError('Condition matched a different target; explicit transition required')
        return True

    def run_steps(self, a):
        steps = a['steps']
        frame = self.guard(a['frame_id']) if steps[0]['action'] in ('wait', 'wait_for', 'read_text', 'focus_window') else None
        if frame is None:
            first_args = {k: v for k, v in steps[0].items() if k not in self.CONTROL_KEYS | {'action'}}
            first_args.update({k: v for k, v in a.items() if k in ('frame_id', 'restore_focus', 'target_window', 'target_title')})
            with self.span('guard'):
                frame = self.prepare(steps[0]['action'], first_args)
        target = self.intended_target(frame)
        self.result_target = dict(target)
        if self.result_view is None and frame.get('geometry'):
            self.result_view = {'kind': 'target'}
        for index, step in enumerate(steps):
            entry = self.ledger.begin(index)
            action = step['action']
            self.input_started = False
            try:
                self.deadline.remaining()
                with self.span('step', index=index, action=action):
                    if 'expect' in step:
                        body = self.await_condition(step['expect'], step.get('expect_timeout_ms', 5000),
                                                    self.action_completed_ns, target,
                                                    **{k: step[k] for k in ('read_text', 'a11y_scope') if k in step})
                        entry['expect_status'] = body.get('status')
                        if not body.get('condition_met'):
                            entry['status'] = 'precondition_failed'
                            self.ledger.stop_reason = f'step {index}: expect not met ({body.get("status")})'
                            break
                        if action == 'focus_window':
                            evidence = body.get('condition_evidence') or {}
                            if not evidence.get('revision') or (evidence.get('target') or {}).get('address') != step['address']:
                                raise ValueError('Focus destination does not match existence evidence')
                        else:
                            self.checked_match(body, target)
                    self.recheck_target(target)
                    if action == 'focus_window':
                        found = [w for w in self.hypr('clients') if w.get('address') == step['address'] and w.get('mapped')]
                        if len(found) != 1:
                            raise ValueError('Unknown window address')
                        destination = self.identity(found[0])
                        if 'expect' in step and self.identity(body['condition_evidence']['target']) != destination:
                            raise ValueError('Focus destination identity changed after existence check')
                        self.frames.clear()
                        self.action_completed_ns = None
                        self.input_started = self.action_started = True
                        self.ledger.injecting()
                        self.dispatch('focuswindow', 'address:'+step['address'])
                        self.wait_focus(step['address'], self.deadline.remaining(.4))
                        self.recheck_target(destination)
                        target = destination
                        self.result_target = dict(target)
                        self.ledger.submitted()
                        self.action_completed_ns = time.monotonic_ns()
                        self.ledger.completed(self.action_completed_ns)
                    elif action == 'wait_for':
                        body = self.await_condition(step['condition'], step.get('timeout_ms', 5000),
                            self.action_completed_ns, target,
                            **{k: step[k] for k in ('read_text', 'a11y_scope') if k in step})
                        entry['observation'] = body
                        if not self.checked_match(body, target):
                            entry.update(status='after_timeout', verification='failed')
                            self.ledger.stop_reason = f'step {index}: wait_for not met'
                            break
                        entry['verification'] = 'matched'
                        if step['condition']['kind'] == 'text_equals':
                            entry['application_accepted'] = 'verified_text'
                    elif action == 'read_text':
                        args = {'window': target['address'], 'channels': ['metadata', 'accessibility'],
                                **{k: step[k] for k in ('read_text', 'a11y_scope') if k in step}}
                        if self.action_completed_ns is not None:
                            args['after_action'] = self.action_completed_ns
                        content, sample = self.observation_service().observe(**args, images=False,
                            return_sample=True, timeout_ms=self.deadline.milliseconds(step.get('timeout_ms', 3000)))
                        evidence = sample['state'].get('accessibility', {}).get('text_readback', {})
                        entry['readback'] = evidence
                        self.recheck_target(target)
                        if evidence.get('verifiable') is not True or json.loads(content[0]['text']).get('freshness_satisfied') is not True:
                            entry.update(status='readback_unavailable', verification='failed')
                            self.ledger.stop_reason = f'step {index}: complete text unavailable'
                            break
                        entry['verification'] = 'read'
                    elif action != 'wait':
                        if index == 0 and action in ('pointer', 'scroll'):
                            # The expect wait has not refreshed the reviewed frame.
                            self.guard(a['frame_id'])
                            if frame.get('target'):
                                self.check_target(frame)
                                if frame.get('visual') and action != 'scroll':
                                    pixels = self.target_pixels(frame['target'], frame['monitor'], geometry=frame.get('geometry'))
                                    if not self.guarded_pixels(frame, pixels, self.action_region(frame, step))['accepted']:
                                        raise ValueError('Coordinate evidence changed during wait')
                            self.guard(a['frame_id'])
                        self.perform(action, step, frame, lambda: self.recheck_target(target))
                    entry['status'] = 'done'
                    if 'after' in step:
                        entry.update(verification='pending', status='verifying')
                        body = self.await_condition(step['after']['condition'], step['after'].get('timeout_ms', 5000),
                                                    self.action_completed_ns, target,
                                                    **{k: step[k] for k in ('read_text', 'a11y_scope') if k in step})
                        entry['after_status'] = body.get('status')
                        if not self.checked_match(body, target, step.get('transition')):
                            entry.update(status='after_timeout', verification='failed')
                            self.ledger.stop_reason = f'step {index}: after condition not met ({body.get("status")})'
                            break
                        entry.update(verification='matched', status='done')
                        if step['after']['condition']['kind'] == 'text_equals':
                            entry['application_accepted'] = 'verified_text'
                        # Evidence authorizes only this exact destination, never a later active query.
                        self.recheck_target(target)
                        self.result_target = dict(target)
                    elif action not in ('wait', 'wait_for', 'read_text'):
                        if not self.settle(target, entry):
                            break
            except (ValueError, TimeoutError) as exc:
                if entry['in_flight_unknown'] or entry['injection'] == 'partial':
                    entry['status'] = 'interrupted'
                    raise
                entry.update(status='precondition_failed', expect_status=str(exc)[:200])
                self.ledger.stop_reason = f'step {index}: {exc}'
                break
        with self.span('result'):
            return self.result_capture(frame['monitor']['name'])


S = {"type": "string"}
I = {"type": "integer"}
DELIVERY = {
    'images': {'type': 'string', 'enum': ['none', 'on_failure', 'target', 'monitor'], 'default': 'none',
               'description': 'Image delivery, independent of guard capture. none returns a view_frame reference. Monitor is explicit overview.'},
    'detail': {'type': 'string', 'enum': ['half', 'original'], 'default': 'half'}}
FRAME = {"frame_id": S, **DELIVERY,
         'settle_timeout_ms': {'type': 'integer', 'minimum': 0, 'maximum': 5000, 'default': 1500,
                              'description': 'Bounded quiescence wait without after; 0 opts out. Does not prove application acceptance.'},
         'result_view': {'type': 'object', 'properties': {
             'kind': {'type': 'string', 'enum': ['monitor', 'target', 'region']},
             'box': {'type': 'array', 'items': I, 'minItems': 4, 'maxItems': 4}},
             'required': ['kind'], 'additionalProperties': False,
             'description': 'Target crop by default. Region box is relative to the current window crop. Overlays report overview_required without broadening capture.'},
         'duration_ms': {'type': 'integer', 'minimum': 1, 'maximum': 120000, 'default': 60000},
         'after': {'type': 'object', 'properties': {'condition': CONDITION_SCHEMA, 'timeout_ms': TIMEOUT},
                   'required': ['condition'], 'additionalProperties': False,
                   'description': 'Perform this one input, then wait locally for an accessible name or window. Timeout does not undo input; inspect before retrying.'},
         "restore_focus": {"type": "boolean", "default": False,
                           "description": "Explicitly approve restoring the named target's focus and performing this input in ONE execution. Default false rejects focus changes."},
         "target_window": {"type": "string", "description": "Required with restore_focus: screenshot target_window.address."},
         "target_title": {"type": "string", "description": "Required with restore_focus: screenshot target_window.title, displayed as the intended approval target."}}
XY = {"x": I, "y": I}
STEP = {'type': 'object', 'required': ['action'], 'additionalProperties': False, 'properties': {
    'transition': {'type': 'string', 'enum': ['matched_window']},
    'action': {'type': 'string', 'enum': ['press_key', 'type_text', 'focus_window', 'wait', 'pointer', 'scroll', 'wait_for', 'read_text']},
    'condition': CONDITION_SCHEMA, 'timeout_ms': TIMEOUT,
    'read_text': COMMON['read_text'], 'a11y_scope': COMMON['a11y_scope'],
    'key': S, 'text': S, 'address': S, **XY, 'button': {'type': 'string', 'enum': ['left', 'right', 'middle', 'move']},
    'count': I, 'steps': I, 'axis': {'type': 'string', 'enum': ['vertical', 'horizontal']},
    'expect': {**CONDITION_SCHEMA, 'description': 'Window or accessible condition that must hold before this step acts; waited for up to expect_timeout_ms.'},
    'expect_timeout_ms': TIMEOUT,
    'after': {'type': 'object', 'properties': {'condition': CONDITION_SCHEMA, 'timeout_ms': TIMEOUT}, 'required': ['condition'],
              'additionalProperties': False, 'description': 'Condition waited for after this step; the sequence stops if it times out.'}}}


def tool(name, description, props, required, read=False, idempotent=None):
    if "restore_focus" in props:
        description += " With restore_focus=true, this operation RESTORES FOCUS to target_title/target_window, revalidates, then performs the input; approve the whole operation."
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props,
                            "required": required, "additionalProperties": False},
            "annotations": {"readOnlyHint": read, "destructiveHint": not read,
                            "idempotentHint": read if idempotent is None else idempotent, "openWorldHint": True}}


TOOLS = [
    tool('context_for_task', 'Read bounded task context. Caller facts are claims; uncertain shortcuts are exploration references. Never injects input.',
         {'intent': S, 'observation_ref': S, 'facts': {'type': 'object'},
          'exact': {'type': 'object', 'properties': {k: S for k in ('id', 'app', 'scope', 'command_id', 'shortcut', 'mode')}, 'additionalProperties': False},
          'max_bytes': {'type': 'integer', 'minimum': 256, 'maximum': 65536, 'default': 8192},
          'timeout_ms': {'type': 'integer', 'minimum': 1, 'maximum': 10000, 'default': 1000},
          'backend': {'type': 'string', 'enum': ['local', 'substring', 'braid'], 'default': 'local'}}, ['intent'], True),
    tool("desktop_state", "Inspect Hyprland monitors, windows, and input backend readiness.", {}, [], True),
    tool("screenshot", "Capture one monitor at logical resolution. Returns image and frame_id required for input.",
         {"monitor": S, **DELIVERY, "images": {**DELIVERY["images"], "enum": ["none", "monitor"], "default": "monitor"}}, [], True),
    tool("focus_window", "Focus an existing window by its address from desktop_state and return scoped evidence and a deferred frame.",
         {"address": S, **DELIVERY, 'settle_timeout_ms': FRAME['settle_timeout_ms'], 'result_view': FRAME['result_view'], 'duration_ms': FRAME['duration_ms']}, ["address"]),
    tool("pointer", "Move/click at viewed frame pixel coordinates, then return outcome evidence and a deferred target frame.",
         {**FRAME, **XY, "button": {"type": "string", "enum": ["left", "right", "middle", "move"]}, "count": I},
         ["frame_id", "x", "y"]),
    tool("type_text", "Type literal UTF-8 text in focused app. Newlines may submit/execute. Does not append Enter.",
         {**FRAME, "text": S}, ["frame_id", "text"]),
    tool("press_key", "Press a key/chord, e.g. CTRL+A, Return, ALT+Tab. Returns outcome evidence and a deferred target frame.",
         {**FRAME, "key": S}, ["frame_id", "key"]),
    tool("scroll", "Scroll at screenshot coordinates. Signed wheel steps: positive up/right, negative down/left.",
         {**FRAME, **XY, "steps": I, "axis": {"type": "string", "enum": ["vertical", "horizontal"]}},
         ["frame_id", "x", "y", "steps"]),
    tool("drag", "Left-button drag between two points in one screenshot; release even on failure.",
         {**FRAME, **XY, "end_x": I, "end_y": I}, ["frame_id", "x", "y", "end_x", "end_y"]),
    tool('run_steps', 'Execute a deterministic sequence of inputs under one approval, verifying a window or accessible condition between steps. '
         'The reviewed target is pinned and checked before each input segment. Coordinate actions are allowed only as the first step, with a fresh guard after any wait. A new target requires explicit focus or a matched_window transition. '
         'Stops at the first unmet condition and reports every step. Returns a final ledger and deferred target frame; images are opt-in. Not for sequences whose next action depends on reading results.',
         {**{k: v for k, v in FRAME.items() if k != 'after'}, 'steps': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': STEP}}, ['frame_id', 'steps']),
    tool('open_uri', 'Launch an explicit http(s) or bounded Obsidian URI through the registered handler. Waits briefly for launch acknowledgement, never for the GUI to close. An uncertain dispatch must not be repeated blindly. Prefer obsidian_create_note for creation with duplicate prevention and exact readback. No overwrite or callbacks.',
         {'uri': S, 'duration_ms': FRAME['duration_ms']}, ['uri']),
    tool('obsidian_create_note', 'Create and verify one note in a registered Obsidian vault. Pass an exact vault name or ID, plain note name, literal content, and a unique operation_id. Encodes internally and targets the vault root. Reusing the same ID and content only reconciles the original attempt, across reconnects. Existing or reserved destinations are rejected. No overwrite. Returns exact saved-content verification or an uncertain receipt for obsidian_note_status.',
         {'vault': S, 'name': S, 'content': S, 'operation_id': S,
          'timeout_ms': {'type': 'integer', 'minimum': 0, 'maximum': 30000, 'default': 5000}},
         ['vault', 'name', 'content', 'operation_id'], idempotent=True),
    tool('obsidian_note_status', 'Read and reconcile a durable note operation in its registered vault, including an automatically suffixed filename. Verifies bounded saved-file content. Never dispatches, creates, overwrites, or replays input. Unverified or ambiguous outcomes require inspection, not a new operation ID.',
         {'operation_id': S, 'timeout_ms': {'type': 'integer', 'minimum': 0, 'maximum': 30000, 'default': 0}}, ['operation_id'], True),
    tool('cdp_read', 'Read an existing explicitly configured loopback Chromium CDP page. Omit target_id to list pages; otherwise provide its exact expected_url and a CSS selector. No script evaluation, navigation or input. Requires WCU_CDP_PORT and optional websockets>=15.',
         {'target_id': S, 'expected_url': S, 'selector': S, 'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 8192}}, [], True),
    tool('view_frame', 'View retained pixels on demand. Capture time and 120-second expiry never refresh. Half size by default; coordinate input requires viewing its frame.',
         {'frame_id': S, 'detail': DELIVERY['detail']}, ['frame_id'], True),
    tool('read_text', 'Read bounded non-protected text from the unique focused control or an explicit revision-scoped selector. Reports unavailable or incomplete evidence.',
         {'window': S, 'read_text': COMMON['read_text'], 'a11y_scope': COMMON['a11y_scope'], 'after_action': COMMON['after_action']}, ['window'], True),
    tool('observe_window', 'Observe an exact focused window. Text-first accessibility and revision deltas, with a deferred guarded crop when pixels are collected. images controls delivery separately; view_frame reads retained pixels on demand.',
         {**{k: v for k, v in COMMON.items() if k != 'images'}, **DELIVERY}, ['window'], True),
    tool('wait_for', 'Wait locally for scoped accessible states or disappearance, exact text readback, a unique or changed window, or changed/stable pixels. Returns outcome evidence and a guarded frame when pixels are available. No input.',
         {**{k: v for k, v in COMMON.items() if k != 'images'}, **DELIVERY, 'condition': CONDITION_SCHEMA, 'timeout_ms': TIMEOUT}, ['condition'], True),
    tool('stop_observing', 'Stop background observation and clear its retained history. No input.', {}, [], True),
]


def validate(name, args):
    if name == 'context_for_task':
        return validate_request(args)
    if name == 'read_text':
        observation.validate('observe', args)
        if not args.get('window') or set(args)-{'window', 'read_text', 'a11y_scope', 'after_action'}:
            raise ValueError('read_text requires window and optional scoped selector')
        return
    if name in ('observe_window', 'wait_for', 'stop_observing'):
        if not isinstance(args, dict):
            raise ValueError('Invalid arguments')
        for k, prop in DELIVERY.items():
            if k in args and args[k] not in prop['enum']:
                raise ValueError('Invalid delivery option')
        observation.validate('observe' if name == 'observe_window' else name, {k: v for k, v in args.items() if k not in DELIVERY})
        if name == 'observe_window' and not args.get('window'):
            raise ValueError('observe_window requires window')
        return
    if name == 'cdp_read' and isinstance(args, dict) and (('target_id' in args) != ('expected_url' in args)):
        raise ValueError('CDP read requires both target_id and expected_url')
    spec = next((t["inputSchema"] for t in TOOLS if t["name"] == name), None)
    if spec is None or not isinstance(args, dict):
        raise ValueError("Unknown tool or invalid arguments")
    if set(args) - spec["properties"].keys() or set(spec["required"]) - args.keys():
        raise ValueError("Unexpected or missing arguments")
    for k, v in args.items():
        p = spec["properties"][k]
        if (p["type"] == "string" and not isinstance(v, str)) or (p["type"] == "integer" and type(v) is not int):
            raise ValueError("Invalid argument type: " + k)
        if p["type"] == "array" and not isinstance(v, list):
            raise ValueError("Invalid argument type: " + k)
        if p["type"] == "boolean" and type(v) is not bool:
            raise ValueError("Invalid argument type: " + k)
        if name == 'open_uri' and k == 'uri':
            validate_uri(v)
        if name == 'cdp_read':
            if k == 'max_chars':
                integer(v, 1, 8192)
            elif not v or len(v) > (4096 if k == 'expected_url' else 512):
                raise ValueError('Invalid CDP selector or page identity')
        if k == 'settle_timeout_ms':
            integer(v, 0, 5000)
        if k == 'duration_ms':
            integer(v, 1, 120000)
        if k == 'result_view':
            if not isinstance(v, dict) or set(v)-{'kind', 'box'} or v.get('kind') not in ('monitor', 'target', 'region'):
                raise ValueError('Invalid result view')
            if v['kind'] == 'region':
                validate_condition({'kind': 'region_changed', 'box': v.get('box')})
            elif 'box' in v:
                raise ValueError('Only region views accept a box')
        if k == 'after':
            if not isinstance(v, dict) or set(v)-{'condition', 'timeout_ms'} or 'condition' not in v:
                raise ValueError('Invalid after wait')
            validate_condition(v['condition'])
            if 'timeout_ms' in v:
                integer(v['timeout_ms'], 1, 30000)
        if "enum" in p and v not in p["enum"]:
            raise ValueError("Invalid choice: " + k)

    if name == 'obsidian_create_note':
        validate_note(args['vault'], args['name'], args['content'], args['operation_id'])
    if name == 'obsidian_note_status':
        validate_operation(args['operation_id'])
    if name in ('obsidian_create_note', 'obsidian_note_status') and 'timeout_ms' in args:
        integer(args['timeout_ms'], 0, 30000)


def serve():
    session_env()
    desktop = Desktop()
    desktop.warm()
    try:
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
                              "instructions": "Use fresh scoped evidence before input; coordinate actions require view_frame. Input returns text evidence by default. Approval dialogs may steal focus: request ONE combined input with restore_focus=true and target_window/target_title from the screenshot. Describe it as Focus <title> and <action>. Do not loop separate refocus calls. Default false rejects focus changes. On action_performed=false, review returned evidence and use view_frame only when pixels are needed. Frames expire after 120 seconds. Shared live desktop; do not disable approval gates."}
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
    finally:
        desktop.close()


if __name__ == "__main__":
    serve()
