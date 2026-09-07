"""Persistent optional screencopy client with a raw-grim fallback. No input."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import subprocess
import threading
import time

from .pixels import parse_ppm, MAX_PIXELS
from .system import run


@dataclass(frozen=True)
class Capture:
    width: int
    height: int
    rgb: bytes
    started_ns: int
    completed_ns: int
    backend: str
    damage: object = None
    presentation_ns: object = None
    fallback_reason: object = None
    requested_ns: object = None


class Capturer:
    def __init__(self, command=None, helper=None):
        self.command = command or run
        self.helper = str(helper or os.environ.get('WAYLAND_CU_CAPTURE_HELPER') or
                          Path(__file__).with_name('capture-helper'))
        self.process = None
        self.lock = threading.Lock()
        self.retry_at = 0
        self.failure = None

    def _read(self, size, end):
        data = bytearray()
        while len(data) < size:
            if not select.select([self.process.stdout], [], [], max(0, end-time.monotonic()))[0]:
                raise TimeoutError('Capture helper timeout')
            block = os.read(self.process.stdout.fileno(), size-len(data))
            if not block:
                raise RuntimeError('Capture helper disconnected')
            data.extend(block)
        return bytes(data)

    def _stop(self):
        if self.process:
            self.process.kill() if self.process.poll() is None else None
            self.process.wait(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None

    def close(self):
        with self.lock:
            self._stop()

    def capture(self, monitor, geometry=None, timeout=3, wait_damage_ms=0):
        requested = time.monotonic_ns()  # Lock contention is reported, not hidden in capture time.
        with self.lock:
            started = time.monotonic_ns()
            # Initial helper implementation intentionally falls back for transformed
            # or scaled outputs. Never approximate an input coordinate transform.
            # Every fallback carries an explicit reason code for later attribution.
            if not (monitor.get('scale') == 1 and monitor.get('transform') == 0):
                reason = 'unsupported_output'
            elif not os.access(self.helper, os.X_OK):
                reason = 'helper_unavailable'
            elif time.monotonic() < self.retry_at:
                reason = 'helper_cooldown: '+(self.failure or '')
            else:
                reason = None
                try:
                    if self.process is None:
                        self.process = subprocess.Popen([self.helper], stdin=subprocess.PIPE,
                                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                        bufsize=0)
                    x, y, width, height = geometry or [monitor['x'], monitor['y'], monitor['width'], monitor['height']]
                    if not 0 < width*height <= MAX_PIXELS:
                        raise ValueError('Capture exceeds pixel budget')
                    name = monitor['name']
                    if not name or len(name) > 127 or any(c.isspace() for c in name):
                        raise ValueError('Unsupported output name')
                    request = f"{name} {x-monitor['x']} {y-monitor['y']} {width} {height} {wait_damage_ms}\n"
                    self.process.stdin.write(request.encode())
                    end = time.monotonic()+timeout
                    line = bytearray()
                    while not line.endswith(b'\n') and len(line) < 4096:
                        line.extend(self._read(1, end))
                    meta = json.loads(line)
                    if meta.get('error'):
                        raise RuntimeError(meta['error'])
                    if (meta.get('width'), meta.get('height')) != (width, height):
                        raise ValueError('Native capture dimensions changed')
                    rgb = self._read(width*height*3, end)
                    return Capture(width, height, rgb, started, time.monotonic_ns(), 'wlr-screencopy',
                                   meta.get('damage'), meta.get('presentation_ns'), requested_ns=requested)
                except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                    self.failure = str(exc)[:200]
                    reason = 'helper_failed: '+self.failure
                    self.retry_at = time.monotonic()+30
                    self._stop()
            # A failed read-only capture can be replaced safely; no action is replayed.
            args = ['grim', '-s', '1']
            if geometry:
                x, y, width, height = geometry
                args += ['-g', f'{x},{y} {width}x{height}']
            else:
                args += ['-o', monitor['name']]
            width, height, rgb = parse_ppm(self.command(args+['-t', 'ppm', '-'], timeout=timeout))
            if geometry and (width, height) != tuple(geometry[2:]):
                raise ValueError('Capture dimensions do not match logical region')
            return Capture(width, height, rgb, started, time.monotonic_ns(), 'grim-ppm',
                           fallback_reason=reason, requested_ns=requested)
