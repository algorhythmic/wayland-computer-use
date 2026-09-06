"""Restartable scoped AT-SPI worker, with event wakeups and bounded requests."""
import json
from pathlib import Path
import subprocess
import sys
import threading
import time


class Accessibility:
    def __init__(self, wake=None):
        self.wake = wake or (lambda: None)
        self.cv = threading.Condition()
        self.request_lock = threading.Lock()
        self.process = None
        self.reader = None
        self.sequence = 0
        self.results = {}
        self.dead = False
        self.event_count = 0

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

    def close(self):
        with self.request_lock:
            self._stop()

    def probe(self, pid, title, timeout=2):
        with self.request_lock:
            try:
                if self.process is None:
                    self.dead = False
                    self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('accessibility_worker.py'))],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    self.reader = threading.Thread(target=self._receive, args=(self.process,), daemon=True)
                    self.reader.start()
                self.sequence += 1
                sequence = self.sequence
                self.process.stdin.write((json.dumps({'id': sequence, 'pid': pid, 'title': title})+'\n').encode())
                self.process.stdin.flush()
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
