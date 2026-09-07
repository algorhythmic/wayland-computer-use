"""Versioned local observations and explicit, bounded outcome waits. No input."""
import base64
from collections import deque
import hashlib
import json
import math
import subprocess
import threading
import time
import uuid

from . import system as baseline
from .capture import Capturer
from .accessibility import Accessibility
from .events import Wakeup
from .pixels import png_rgb, crop, changed_box

MAX_PIXELS = 4_000_000
DEFAULT_CHANNELS = ('metadata', 'pixels', 'accessibility')


def command(args, data=None, timeout=3):
    return baseline.run(args, data=data, timeout=timeout)


def window_record(w):
    return {k: w.get(k) for k in ('address', 'pid', 'class', 'title', 'at', 'size',
                                  'workspace', 'monitor', 'mapped', 'initialClass')}


def capture_geometry(w, m):
    """Logical visible rectangle on one output, including window borders."""
    mw, mh = m['width'], m['height']
    if m['transform'] % 2:
        mw, mh = mh, mw
    left, top = max(w['at'][0], m['x']), max(w['at'][1], m['y'])
    right = min(w['at'][0]+w['size'][0], m['x']+mw/m['scale'])
    bottom = min(w['at'][1]+w['size'][1], m['y']+mh/m['scale'])
    left, top, right, bottom = math.ceil(left), math.ceil(top), math.floor(right), math.floor(bottom)
    if right <= left or bottom <= top:
        raise ValueError('Target has no visible area on its output')
    if (right-left)*(bottom-top) > MAX_PIXELS:
        raise ValueError('Target exceeds four-million-pixel capture budget')
    return [left, top, right-left, bottom-top]


class Collector:
    def __init__(self, capturer=None, accessibility=None):
        baseline.session_env()
        self.identities = {}
        self.capturer = capturer or Capturer(command=command)
        self.accessibility = accessibility or Accessibility()

    def hypr(self, name):
        return json.loads(command(['hyprctl', '-j', name]))

    def close(self):
        self.capturer.close()
        self.accessibility.close()

    def collect(self, window, channels=DEFAULT_CHANNELS, wait_damage_ms=0):
        started, started_ns = time.time(), time.monotonic_ns()
        timings = {}
        clients, monitors, active = self.hypr('clients'), self.hypr('monitors'), self.hypr('activewindow')
        present, windows = set(), []
        for w in clients:
            key = (w.get('address'), w.get('pid'))
            present.add(key)
            self.identities.setdefault(key, uuid.uuid4().hex[:12])
            windows.append(dict(window_record(w), id=self.identities[key]))
        self.identities = {k: v for k, v in self.identities.items() if k in present}
        windows.sort(key=lambda w: w['id'])
        state = {'source': 'hyprland', 'windows': windows, 'active_window': active.get('address'),
                 'monitors': [{k: m.get(k) for k in ('id', 'name', 'x', 'y', 'width', 'height', 'scale', 'transform')}
                              for m in monitors],
                 'visual': {'status': 'not_requested'}, 'accessibility': {'status': 'not_requested'}}
        timings['metadata_ms'] = (time.monotonic_ns()-started_ns)/1e6
        rgb = capture = geometry = monitor = None
        target = next((w for w in windows if w['address'] == window), None)
        requested = [('pixels', 'visual'), ('accessibility', 'accessibility')]
        reason = None
        if 'accessibility' not in channels:
            self.accessibility.close()
        if window and not target:
            reason = 'window_missing'
        elif target and (active.get('address') != window or not target['mapped']):
            reason = 'target_not_focused'
        elif target:
            tick = time.monotonic_ns()
            locked = subprocess.run(['pgrep', '-x', 'hyprlock'], stdout=subprocess.DEVNULL).returncode == 0
            timings['lock_check_ms'] = (time.monotonic_ns()-tick)/1e6
            if locked:
                reason = 'desktop_locked'
        if reason:
            self.accessibility.close()
            for channel, key in requested:
                if channel in channels:
                    state[key] = {'status': 'unavailable', 'reason': reason}
        elif target:
            if 'pixels' in channels:
                tick = time.monotonic_ns()
                try:
                    monitor = next(m for m in monitors if m['id'] == target['monitor'])
                    geometry = capture_geometry(target, monitor)
                    capture = self.capturer.capture(monitor, geometry, wait_damage_ms=wait_damage_ms)
                    rgb = capture.rgb
                    state['visual'] = {'status': 'available', 'source': capture.backend,
                        'geometry': geometry, 'sha256': hashlib.sha256(rgb).hexdigest(),
                        'visibility': 'composited region; may include overlays',
                        'coordinates': 'crop pixels at logical scale 1'}
                except Exception as exc:
                    state['visual'] = {'status': 'unavailable', 'reason': str(exc)[:240]}
                timings['capture_ms'] = (time.monotonic_ns()-tick)/1e6
            if 'accessibility' in channels:
                tick = time.monotonic_ns()
                state['accessibility'] = self.accessibility.probe(target['pid'], target['title'])
                timings['accessibility_ms'] = (time.monotonic_ns()-tick)/1e6
            tick = time.monotonic_ns()
            current = window_record(self.hypr('activewindow'))
            layout = [{k: m.get(k) for k in state['monitors'][0]} for m in self.hypr('monitors')] if monitors else []
            locked = subprocess.run(['pgrep', '-x', 'hyprlock'], stdout=subprocess.DEVNULL).returncode == 0
            if current != {k: v for k, v in target.items() if k != 'id'} or layout != state['monitors'] or locked:
                rgb = capture = None
                for channel, key in requested:
                    if channel in channels:
                        state[key] = {'status': 'unavailable', 'reason': 'desktop_changed_during_collection'}
            timings['validation_ms'] = (time.monotonic_ns()-tick)/1e6
        timings['collection_ms'] = (time.monotonic_ns()-started_ns)/1e6
        return {'state': state, 'rgb': rgb, 'capture': capture, 'monitor': monitor,
                'target': target, 'geometry': geometry,
                'started_at': started, 'observed_at': time.time(), 'started_ns': started_ns,
                'completed_ns': time.monotonic_ns(), 'timings_ms': timings}


class History:
    def __init__(self, capacity=6):
        self.epoch = uuid.uuid4().hex[:12]
        self.sequence = 0
        self.items = deque(maxlen=capacity)
        self.samples = deque(maxlen=60)

    def frozen(self):
        frozen = History()
        frozen.items, frozen.samples = deque(self.items), deque(self.samples)
        return frozen

    def add(self, sample):
        previous = self.items[-1] if self.items else None
        changed = previous is None or sample['state'] != previous['state']
        if changed:
            self.sequence += 1
        sample['revision'] = f'{self.epoch}:{self.sequence}'
        self.samples.append({'observed_at': sample['observed_at'], 'revision': sample['revision'],
            'visual_status': sample['state'].get('visual', {}).get('status'),
            'visual_changed': (sample['state'].get('visual', {}).get('sha256') !=
                               previous['state'].get('visual', {}).get('sha256')) if previous else None})
        if changed:
            self.items.append(sample)
        else:
            self.items[-1] = sample
        return sample

    def response(self, since=None, images=True):
        started = time.monotonic_ns()
        current = self.items[-1]
        old = next((s for s in self.items if s['revision'] == since), None)
        body = {'revision': current['revision'], 'since_revision': since,
            'mode': 'snapshot' if old is None else 'delta',
            'reset_reason': 'unknown_or_evicted_revision' if since and old is None else None,
            'collection_started_at': current['started_at'], 'observed_at': current['observed_at'],
            'collection_started_ns': current.get('started_ns'), 'completed_ns': current.get('completed_ns'),
            'age_ms': round((time.monotonic_ns()-current['completed_ns'])/1e6) if 'completed_ns' in current else 0,
            'atomic': False, 'actionable': False, 'state': current['state'] if old is None else None,
            'changes': {}, 'images': [], 'recent_samples': list(self.samples)[-12:],
            'timings_ms': dict(current.get('timings_ms', {}))}
        if old:
            for key in sorted(current['state'].keys() | old['state'].keys()):
                if current['state'].get(key) != old['state'].get(key):
                    body['changes'][key] = {'before': old['state'].get(key), 'after': current['state'].get(key)}
        blocks = []
        visual = current['state'].get('visual', {})
        if images and current['rgb'] is not None:
            x, y, width, height = visual['geometry']
            box, kind = [0, 0, width, height], 'overview'
            if old and old['rgb'] is not None and old['state']['visual'].get('geometry') == visual['geometry']:
                box = changed_box(old['rgb'], current['rgb'], width, height)
                kind = 'changed_region'
            if box:
                left, top, right, bottom = box
                pixels = crop(current['rgb'], width, box)
                blocks.append({'type': 'image', 'mimeType': 'image/png',
                    'data': base64.b64encode(png_rgb(right-left, bottom-top, pixels)).decode()})
                body['images'].append({'index': 0, 'kind': kind, 'box_in_window_crop': box,
                    'desktop_origin': [x+left, y+top], 'width': right-left, 'height': bottom-top,
                    'source_revision': current['revision']})
        body['timings_ms']['response_ms'] = (time.monotonic_ns()-started)/1e6
        return [{'type': 'text', 'text': json.dumps(body)}, *blocks]


CONDITION_SCHEMA = {'type': 'object', 'properties': {
    'kind': {'type': 'string', 'enum': ['accessible', 'window', 'region_changed']},
    'name': {'type': 'string', 'maxLength': 160}, 'role': {'type': 'string', 'maxLength': 80},
    'state': {'type': 'string', 'enum': ['visible', 'showing', 'enabled', 'sensitive', 'focused', 'checked', 'selected', 'editable']},
    'value': {'type': 'number'}, 'title': {'type': 'string', 'maxLength': 256},
    'class': {'type': 'string', 'maxLength': 128},
    'box': {'type': 'array', 'items': {'type': 'integer', 'minimum': 0}, 'minItems': 4, 'maxItems': 4}},
    'required': ['kind'], 'additionalProperties': False,
    'description': 'Exact accessible name (optional role/state/value), unique mapped window title/class, or changed crop box [left,top,right,bottom] relative to since_revision. No expressions.'}


def validate_condition(condition):
    if not isinstance(condition, dict):
        raise ValueError('condition must be an object')
    kind = condition.get('kind')
    allowed = {'accessible': {'kind', 'name', 'role', 'state', 'value'},
               'window': {'kind', 'title', 'class'}, 'region_changed': {'kind', 'box'}}
    if type(kind) is not str or kind not in allowed or set(condition)-allowed[kind]:
        raise ValueError('Invalid condition fields')
    if kind == 'accessible' and 'name' not in condition:
        raise ValueError('Accessible condition requires exact name')
    if kind == 'window' and not ({'title', 'class'} & set(condition)):
        raise ValueError('Window condition requires title or class')
    if kind == 'region_changed':
        box = condition.get('box')
        if not isinstance(box, list) or len(box) != 4 or any(type(x) is not int or x < 0 for x in box) or box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError('Invalid region box')
    for key, value in condition.items():
        prop = CONDITION_SCHEMA['properties'][key]
        if prop['type'] == 'string' and (type(value) is not str or not value or len(value) > prop.get('maxLength', 80)):
            raise ValueError('Invalid condition string: '+key)
        if 'enum' in prop and value not in prop['enum']:
            raise ValueError('Invalid condition choice: '+key)
        if key == 'value' and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError('Invalid numeric value')


def matches(condition, sample, reference=None):
    state, kind = sample['state'], condition['kind']
    if kind == 'window':
        found = [w for w in state.get('windows', []) if w.get('mapped') and
                 all(w.get(k) == v for k, v in condition.items() if k != 'kind')]
        return len(found) == 1
    if kind == 'accessible':
        evidence = state.get('accessibility', {})
        if evidence.get('status') != 'available':
            return False
        found = [n for n in evidence.get('nodes', []) if n.get('name') == condition['name'] and
                 n.get('name') != '[protected]' and
                 all((v in n.get('states', [])) if k == 'state' else n.get(k) == v
                     for k, v in condition.items() if k not in ('kind', 'name'))]
        return len(found) == 1
    if not reference or sample.get('rgb') is None or reference.get('rgb') is None:
        return False
    visual = state.get('visual', {})
    if visual.get('geometry') != reference['state'].get('visual', {}).get('geometry'):
        return False
    width = visual['geometry'][2]
    return crop(sample['rgb'], width, condition['box']) != crop(reference['rgb'], width, condition['box'])


class Observer:
    """One scope, six revisions, and a renewable 120-second observation lease."""
    def __init__(self, collector=None, interval=1.0):
        self.collector = collector or Collector()
        self.interval = interval
        self.cv = threading.Condition()
        self.requests = threading.Lock()
        self.wakeup = Wakeup()
        if hasattr(self.collector, 'accessibility'):
            self.collector.accessibility.wake = self.wakeup.notify
        self.scope = None
        self.history = History()
        self.generation = self.sample_count = self.requested_ns = 0
        self.deadline = 0
        self.closed = self.waiting = False
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.thread.start()

    @property
    def event_source(self):
        return self.wakeup.source

    def connect_events(self):
        self.wakeup.connect()

    def worker(self):
        collecting = False
        try:
            while True:
                with self.cv:
                    if self.closed:
                        return
                    active = self.scope is not None and time.monotonic() < self.deadline
                    scope, generation = self.scope, self.generation
                if not active:
                    if collecting:
                        if hasattr(self.collector, 'close'):
                            self.collector.close()
                        self.wakeup.disconnect()
                        collecting = False
                    self.wakeup.wait(120)
                    continue
                collecting = True
                self.wakeup.drain()
                self.connect_events()  # Subscribe before sampling to close the startup gap.
                started, started_ns = time.time(), time.monotonic_ns()
                try:
                    options = {'channels': scope[1]}
                    if self.waiting and 'pixels' in scope[1] and 'accessibility' not in scope[1]:
                        options['wait_damage_ms'] = 50
                    sample = self.collector.collect(scope[0], **options)
                except Exception as exc:
                    sample = {'state': {'error': str(exc)[:240]}, 'rgb': None,
                              'started_at': started, 'observed_at': time.time()}
                sample['started_ns'] = started_ns
                sample['completed_ns'] = time.monotonic_ns()
                with self.cv:
                    valid = generation == self.generation and self.scope is not None and time.monotonic() < self.deadline
                    if valid:
                        self.history.add(sample)
                        self.sample_count += 1
                        self.cv.notify_all()
                    pending = self.requested_ns > started_ns or not valid
                    delay = min(self.interval, .1) if self.waiting else self.interval
                    delay = min(delay, max(0, self.deadline-time.monotonic()))
                if not pending:
                    self.wakeup.wait(delay)
        finally:
            if hasattr(self.collector, 'close'):
                self.collector.close()
            self.wakeup.close()

    def observe(self, window=None, since_revision=None, images=True, timeout_ms=15000, wait=False,
                channels=None, max_age_ms=0, after_action=None, condition=None, return_sample=False,
                reference_sample=None):
        if condition:
            validate_condition(condition)
        channels = tuple(sorted(set(channels if channels is not None else DEFAULT_CHANNELS)))
        if 'metadata' not in channels:
            channels = tuple(sorted((*channels, 'metadata')))
        if not window:
            channels = ('metadata',)
        if condition and condition['kind'] != 'window' and not window:
            raise ValueError('This condition requires an exact window')
        if condition and condition['kind'] == 'accessible' and 'accessibility' not in channels:
            raise ValueError('Accessible condition requires accessibility channel')
        if condition and condition['kind'] == 'region_changed' and 'pixels' not in channels:
            raise ValueError('Region condition requires pixels channel')
        now_ns = time.monotonic_ns()
        if after_action is not None and (type(after_action) is not int or not 0 <= after_action <= now_ns):
            raise ValueError('after_action must be a past local monotonic nanosecond watermark')
        requested_ns = max(now_ns-max_age_ms*1_000_000, after_action or 0)
        end = time.monotonic()+timeout_ms/1000
        with self.requests:
            with self.cv:
                if self.closed:
                    raise RuntimeError('Observer closed')
                if self.scope != (window, channels):
                    self.scope = (window, channels)
                    self.generation += 1
                    self.history = History()
                generation = self.generation
                reference = reference_sample or next((s for s in self.history.items if s['revision'] == since_revision), None)
                if condition and condition['kind'] == 'region_changed':
                    if reference is None or reference.get('rgb') is None:
                        raise ValueError('Region wait requires retained baseline pixels; observe again')
                    crop(reference['rgb'], reference['state']['visual']['geometry'][2], condition['box'])
                self.deadline = time.monotonic()+120
                self.waiting = wait or condition is not None
                latest = self.history.items[-1] if self.history.items else None
                cached = (latest is not None and latest['started_ns'] >= requested_ns and
                    (matches(condition, latest, reference) if condition else not wait or latest['revision'] != since_revision))
                if not cached:
                    self.requested_ns = max(self.requested_ns, requested_ns)
                    self.wakeup.notify()
                status = 'timeout'
                try:
                    while True:
                        if self.closed or generation != self.generation:
                            raise RuntimeError('Observation interrupted by stop or scope change')
                        sample = self.history.items[-1] if self.history.items else None
                        fresh = sample is not None and sample['started_ns'] >= requested_ns
                        if fresh:
                            satisfied = matches(condition, sample, reference) if condition else not wait or sample['revision'] != since_revision
                            if satisfied:
                                status = 'matched' if condition else 'changed' if wait else 'observed'
                                break
                            if condition and condition['kind'] != 'window':
                                reason = sample['state'].get('visual', {}).get('reason') or sample['state'].get('accessibility', {}).get('reason')
                                if reason in ('target_not_focused', 'window_missing', 'desktop_locked', 'desktop_changed_during_collection'):
                                    status = 'interrupted'
                                    break
                        remaining = end-time.monotonic()
                        if remaining <= 0:
                            break
                        self.cv.wait(remaining)
                    if not self.history.items:
                        raise TimeoutError('No observation completed before timeout')
                    frozen = self.history.frozen()
                    sample = frozen.items[-1]
                    fresh = sample['started_ns'] >= requested_ns
                    event_source = self.event_source
                finally:
                    self.waiting = False
            # Encoding and delta construction never hold the collection lock.
            result = frozen.response(since_revision, images)
            body = json.loads(result[0]['text'])
            body.update(fresh_sample=sample['started_ns'] >= now_ns,
                        freshness_satisfied=fresh, status=status,
                        wait_timed_out=status == 'timeout', condition_met=condition is not None and status == 'matched',
                        event_source=event_source, observation_lease_seconds=120)
            body['timings_ms']['request_ms'] = (time.monotonic_ns()-now_ns)/1e6
            result[0]['text'] = json.dumps(body)
            return (result, sample) if return_sample else result

    def stop(self):
        with self.cv:
            self.scope = None
            self.generation += 1
            self.history = History()
            self.cv.notify_all()
            self.wakeup.notify()
        return [{'type': 'text', 'text': json.dumps({'stopped': True, 'history_cleared': True})}]

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()
            self.wakeup.notify()
        self.thread.join(timeout=30)


COMMON = {'window': {'type': 'string', 'description': 'Exact Hyprland window address; omit for desktop metadata.'},
          'since_revision': {'type': 'string'}, 'images': {'type': 'boolean'},
          'channels': {'type': 'array', 'items': {'type': 'string', 'enum': ['metadata', 'pixels', 'accessibility']}, 'uniqueItems': True, 'minItems': 1, 'maxItems': 3},
          'max_age_ms': {'type': 'integer', 'minimum': 0, 'maximum': 5000, 'default': 0},
          'after_action': {'type': 'integer', 'minimum': 0, 'description': 'Only use collection started after this local action_completed_ns watermark.'}}
TIMEOUT = {'type': 'integer', 'minimum': 1, 'maximum': 30000}
TOOLS = [baseline.tool('observe', 'Read versioned desktop evidence. max_age_ms=0 requires collection started after the request. images controls delivery; channels controls collection.', COMMON, [], True),
         baseline.tool('wait_for_change', 'Wait for any revision change, up to 30 seconds. Dynamic pixels may wake immediately.', {**COMMON, 'timeout_ms': TIMEOUT}, ['since_revision'], True),
         baseline.tool('wait_for', 'Wait for an exact accessible name, unique mapped window, or changed region. Returns explicit match, timeout or interruption; never performs input.', {**COMMON, 'timeout_ms': TIMEOUT, 'condition': CONDITION_SCHEMA}, ['condition'], True),
         baseline.tool('stop_observing', 'Stop collection and clear retained history. In-flight reads are discarded.', {}, [], True)]


def validate(name, args):
    tool = next((t for t in TOOLS if t['name'] == name), None)
    if not tool or not isinstance(args, dict):
        raise ValueError('Unknown tool or invalid arguments')
    schema = tool['inputSchema']
    if set(args)-set(schema['properties']) or set(schema['required'])-set(args):
        raise ValueError('Unexpected or missing arguments')
    for key, value in args.items():
        prop = schema['properties'][key]
        kind = prop['type']
        if type(value) is not {'string': str, 'integer': int, 'boolean': bool, 'array': list, 'object': dict}[kind]:
            raise ValueError('Invalid type: '+key)
        if kind == 'string' and (not value or len(value) > 128):
            raise ValueError('Invalid string length: '+key)
        if kind == 'integer' and not prop.get('minimum', 0) <= value <= prop.get('maximum', 2**63-1):
            raise ValueError('Out of range: '+key)
    if 'channels' in args:
        values = args['channels']
        if not 1 <= len(values) <= 3 or any(type(v) is not str or v not in DEFAULT_CHANNELS for v in values) or len(set(values)) != len(values):
            raise ValueError('Invalid channels')
    if 'condition' in args:
        validate_condition(args['condition'])
