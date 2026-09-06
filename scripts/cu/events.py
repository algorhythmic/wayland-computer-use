"""Interruptible local waits; compositor notifications are hints, not state."""
import os
from pathlib import Path
import select
import socket
import threading
import time

from .system import owned_socket

# Capture lifecycle events are deliberately excluded: our own captures emit them.
RELEVANT = {'activewindow', 'activewindowv2', 'focusedmon', 'openwindow', 'closewindow',
            'movewindow', 'movewindowv2', 'windowtitle', 'windowtitlev2', 'workspace',
            'workspacev2', 'monitoradded', 'monitoraddedv2', 'monitorremoved',
            'openlayer', 'closelayer', 'fullscreen', 'changefloatingmode', 'configreloaded'}


class Wakeup:
    def __init__(self):
        self.reader, self.writer = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        self.events = None
        self.buffer = b''
        self.retry_at = 0
        self.closed = False

    @property
    def source(self):
        return 'hyprland_events_with_polling' if self.events else 'polling'

    def notify(self):
        try:
            os.write(self.writer, b'!')
        except BlockingIOError:
            pass  # One queued byte already guarantees a wakeup.
        except OSError:
            if not self.closed:
                raise

    def connect(self):
        if self.events or time.monotonic() < self.retry_at:
            return
        self.retry_at = time.monotonic()+2
        path = (Path(os.environ.get('XDG_RUNTIME_DIR', '/nonexistent'))/'hypr'/
                os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')/'.socket2.sock')
        if not owned_socket(path):
            return
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(.1)
        try:
            sock.connect(str(path))
            sock.setblocking(False)
            self.events = sock
        except OSError:
            sock.close()

    def disconnect(self):
        if self.events:
            self.events.close()
            self.events = None
        self.buffer = b''

    def drain(self):
        try:
            while os.read(self.reader, 4096):
                pass
        except BlockingIOError:
            pass

    def wait(self, timeout):
        end = time.monotonic()+max(0, timeout)
        while not self.closed:
            sockets = [self.reader] + ([self.events] if self.events else [])
            ready, _, _ = select.select(sockets, [], [], max(0, end-time.monotonic()))
            if not ready:
                return 'deadline'
            if self.reader in ready:
                self.drain()
                return 'request'
            try:
                data = self.events.recv(65536)
                if not data:
                    raise OSError('Event socket closed')
                self.buffer += data
                lines = self.buffer.split(b'\n')
                self.buffer = lines.pop()
                if len(self.buffer) > 65536:
                    raise OSError('Oversized event')
                if any(line.split(b'>>', 1)[0].decode(errors='replace') in RELEVANT for line in lines):
                    return 'desktop'
            except OSError:
                self.disconnect()
                return 'disconnect'
            if time.monotonic() >= end:
                return 'deadline'
        return 'closed'

    def close(self):
        self.closed = True
        self.disconnect()
        os.close(self.reader)
        os.close(self.writer)
