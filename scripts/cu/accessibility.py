"""Restartable scoped AT-SPI worker, with event wakeups and bounded requests."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time


WORKER = [sys.executable, str(Path(__file__).with_name('accessibility_worker.py'))]


class Accessibility:
    def __init__(self, wake=None, command=None):
        self.wake = wake or (lambda: None)
        self.command = command or WORKER
        self.cv = threading.Condition()
        self.request_lock = threading.Lock()
        self.process = None
        self.reader = None
        self.sequence = 0
        self.results = {}
        self.dead = False
        self.watching = False
        self.event_count = 0

    def _start(self):
        if self.process is None:
            self.dead = False
            self.watching = False
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.reader = threading.Thread(target=self._receive, args=(self.process,), daemon=True)
            self.reader.start()

    def warm(self):
        """Start the worker so its GI import precedes the first probe. It reads nothing until asked."""
        with self.request_lock:
            try:
                self._start()
            except OSError:
                self._stop()

    def unwatch(self):
        """Drop the application subscription but keep the imported worker for later probes."""
        with self.request_lock:
            if self.process is None or not self.watching:
                return
            try:
                self.process.stdin.write(b'{"unwatch": true}\n')
                self.process.stdin.flush()
                self.watching = False
            except OSError:
                self._stop()

    def _receive(self, process):
        try:
            for line in iter(lambda: process.stdout.readline(262145), b''):
                if len(line) > 262144:
                    break
                value = json.loads(line)
                if value.get('event') == 'accessibility_changed':
                    self.event_count += 1
                    self.wake()
                elif 'id' in value:
                    with self.cv:
                        if process is self.process:
                            self.results[value['id']] = value['result']
                            self.cv.notify_all()
        except (OSError, ValueError, KeyError):
            pass
        finally:
            with self.cv:
                if process is self.process:
                    self.dead = True
                    self.cv.notify_all()

    def _stop(self):
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdin.close()
            if self.reader:
                self.reader.join(timeout=2)
            process.stdout.close()
        self.results.clear()
        self.watching = False

    def close(self):
        with self.request_lock:
            self._stop()

    def probe(self, pid, title, timeout=2, scope=None, read_text=None):
        with self.request_lock:
            try:
                self._start()
                self.sequence += 1
                sequence = self.sequence
                self.process.stdin.write((json.dumps({'id': sequence, 'pid': pid, 'title': title, 'scope': scope, 'read_text': read_text})+'\n').encode())
                self.process.stdin.flush()
                self.watching = True
                end = time.monotonic()+timeout
                with self.cv:
                    while sequence not in self.results and not self.dead:
                        remaining = end-time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('probe_timeout')
                        self.cv.wait(remaining)
                    if self.dead:
                        raise RuntimeError('probe_disconnected')
                    return self.results.pop(sequence)
            except (OSError, RuntimeError, TimeoutError) as exc:
                self._stop()
                return {'status': 'unavailable', 'reason': str(exc)[:160]}
