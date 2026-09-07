"""Per-request spans and an opt-in, bounded local trace recorder. Never on MCP stdout.

Spans keep parent links so exclusive time can be derived later from the union of
child intervals. The flat ``*_ms`` dictionary returned to callers is inclusive and
must not be summed. Records are allowlisted timings, counters, and enums: no
screen contents, typed text, titles, URLs, key chords, or approval reasoning.
"""
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import stat
import sys
import threading
import time
import uuid

SCHEMA = 1
DEFAULT_LIMIT_BYTES = 64*1024*1024
ENVIRONMENT = 'WAYLAND_CU_TRACE_DIR'
# Only these keys may carry strings; everything else must be numeric/boolean/None.
TEXT_KEYS = {'kind', 'trace_id', 'tool', 'status', 'action_performed', 'error_type', 'reason',
             'name', 'argv0', 'backend', 'capture_backend', 'fallback_reason', 'condition_kind', 'python', 'boot_id',
             'source_sha256', 'clock', 'path', 'monitor'}
REASON_LIMIT = 200
_local = threading.local()


def current():
    return getattr(_local, 'trace', None)


def activate(trace):
    """Bind ``trace`` to this thread; returns the previous binding for restoration."""
    previous = current()
    _local.trace = trace
    return previous


class Trace:
    """Spans for one request. Not thread-safe; the observer thread stays separate."""
    def __init__(self, name, **attrs):
        self.id = uuid.uuid4().hex
        self.spans, self.stack = [], []
        self.wall_started = time.time()
        self.root = self.begin(name, **attrs)

    def begin(self, name, **attrs):
        span = {'id': len(self.spans)+1, 'name': name,
                'parent': self.stack[-1]['id'] if self.stack else None,
                'start_ns': time.monotonic_ns(), 'end_ns': None}
        if attrs:
            span['attrs'] = attrs
        self.spans.append(span)
        self.stack.append(span)
        return span

    def end(self, span, error=None):
        if span['end_ns'] is None:
            span['end_ns'] = time.monotonic_ns()
        if error is not None:
            span['error'] = type(error).__name__
        if span in self.stack:
            del self.stack[self.stack.index(span):]

    @contextmanager
    def span(self, name, **attrs):
        span = self.begin(name, **attrs)
        try:
            yield span
        except BaseException as exc:
            self.end(span, exc)
            raise
        else:
            self.end(span)

    def note(self, **attrs):
        """Attach attributes to the innermost open span."""
        if self.stack:
            self.stack[-1].setdefault('attrs', {}).update(attrs)

    def record(self, name, start_ns, **attrs):
        """A completed leaf span under the innermost open span (e.g. a subprocess)."""
        self.spans.append({'id': len(self.spans)+1, 'name': name,
                           'parent': self.stack[-1]['id'] if self.stack else None,
                           'start_ns': start_ns, 'end_ns': time.monotonic_ns(), 'attrs': attrs})

    def close(self, error=None):
        for span in reversed(self.spans):
            if span['end_ns'] is None:
                span['incomplete'] = True
                self.end(span, error if span is self.root else None)
        self.stack.clear()

    def durations_ms(self, exclude=('exec',)):
        """Inclusive per-name totals for callers. ``total_ms`` is the root span."""
        result = {}
        for span in self.spans:
            if span['end_ns'] is None or span['name'] in exclude or span is self.root:
                continue
            key = span['name']+'_ms'
            result[key] = result.get(key, 0)+(span['end_ns']-span['start_ns'])/1e6
        if self.root['end_ns'] is not None:
            result['total_ms'] = (self.root['end_ns']-self.root['start_ns'])/1e6
        return result


def record_exec(argv0, start_ns, exit_code=None, error=None):
    trace = current()
    if trace is not None:
        attrs = {'argv0': os.path.basename(str(argv0))[:40], 'exit_code': exit_code}
        if error is not None:
            attrs['error_type'] = type(error).__name__
        trace.record('exec', start_ns, **attrs)


def scrub(value, key=None):
    """Enforce the allowlist structurally: strings survive only under TEXT_KEYS."""
    if isinstance(value, dict):
        return {str(k)[:40]: scrub(v, key if key == 'source_sha256' else str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v, key) for v in value]
    if isinstance(value, str):
        return value[:REASON_LIMIT] if key in TEXT_KEYS else None
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return None


def span_or_null(trace, name, **attrs):
    return trace.span(name, **attrs) if trace is not None else nullcontext()


class Recorder:
    """Append allowlisted JSON lines to one bounded, private file. Loss is counted."""
    def __init__(self, directory=None, limit_bytes=DEFAULT_LIMIT_BYTES, provenance=None):
        self.file = self.path = self.error = None
        self.limit = limit_bytes
        self.written = self.records = self.dropped = 0
        location = os.environ.get(ENVIRONMENT) if directory is None else directory
        if not location:
            return
        try:
            root = Path(location)
            if not root.is_absolute():
                raise ValueError('Trace directory must be absolute')
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Trace directory must be owned by this user with mode 0700')
            self.path = root/f"trace-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.jsonl"
            self.file = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
        except (OSError, ValueError) as exc:
            self.error, self.file = str(exc)[:REASON_LIMIT], None
            return
        self.write({'kind': 'process', **(provenance or {})})

    @property
    def enabled(self):
        return self.file is not None

    def write(self, record):
        if self.file is None:
            return False
        record = scrub({'schema': SCHEMA, 'pid': os.getpid(), 'wall_s': time.time(),
                        'monotonic_ns': time.monotonic_ns(), 'clock': 'CLOCK_MONOTONIC', **record})
        line = json.dumps(record, separators=(',', ':'))+'\n'
        if self.written+len(line) > self.limit:
            self.dropped += 1
            return False
        try:
            self.file.write(line)
            self.file.flush()
        except OSError as exc:
            self.dropped += 1
            self.error = str(exc)[:REASON_LIMIT]
            return False
        self.written += len(line)
        self.records += 1
        return True

    def status(self):
        return {'enabled': self.enabled, 'path': str(self.path) if self.path else None,
                'records': self.records, 'dropped': self.dropped, 'bytes': self.written,
                'limit_bytes': self.limit, 'error': self.error, 'complete': self.dropped == 0 and not self.error}

    def close(self):
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
            self.file = None


def provenance(paths=()):
    """Clock-domain sync point plus identifiers; hashes only named source files."""
    import hashlib
    info = {'python': sys.version.split()[0], 'wall_s': time.time(), 'monotonic_ns': time.monotonic_ns(),
            'clock': 'CLOCK_MONOTONIC', 'source_sha256': {}}
    try:
        info['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        pass
    for path in paths:
        try:
            info['source_sha256'][Path(path).name] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except (OSError, TypeError):
            pass
    return info
