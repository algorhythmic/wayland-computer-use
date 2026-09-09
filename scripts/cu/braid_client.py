"""Optional supervised lexical Braid adapter. It never owns desktop input."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time

from .context_records import atomic_json, encode

REQUIRED = ['query', 'strict_requests', 'structured_errors', 'dataset_binding', 'dataset',
            'replace_snapshot', 'hard_filters', 'allowed_ids', 'get_many', 'read_revision']
POLICY = {'weights': {'lexical': 1, 'dense': 0, 'graph': 0, 'temporal': 0}, 'mmr_lambda': 1}


class BraidError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def no_duplicates(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise BraidError('duplicate_protocol_key')
        result[k] = v
    return result


class Connection:
    def __init__(self, command, max_response_bytes=4_000_000):
        self.command, self.limit = command, max_response_bytes
        self.process = None
        self.lock = threading.Lock()
        self.sequence = 0
        self.hello = None

    def close(self):
        process, self.process = self.process, None
        self.hello = None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdin.close()
            process.stdout.close()

    def start(self, end):
        if self.process is None:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, bufsize=0)
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            try:
                self.hello = self.request('hello', end, versions=[1], required_capabilities=REQUIRED)
                if self.hello.get('protocol_version') != 1 or not set(REQUIRED) <= set(self.hello.get('capabilities', [])):
                    raise BraidError('capability_mismatch')
            except BaseException:
                self.close()
                raise
        return self.hello

    def request(self, method, end, **payload):
        remaining = end-time.monotonic()
        if remaining <= 0 or not self.lock.acquire(timeout=max(0, remaining)):
            raise BraidError('deadline')
        try:
            if self.process is None:
                raise BraidError('not_connected')
            self.sequence += 1
            identifier = 'wcu-'+str(self.sequence)
            data = encode({'id': identifier, 'method': method, **payload})+b'\n'
            if len(data) > 16*1024*1024:
                raise BraidError('request_too_large')
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdin, selectors.EVENT_WRITE)
                sent = 0
                while sent < len(data):
                    remaining = end-time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise BraidError('deadline')
                    sent += os.write(self.process.stdin.fileno(), data[sent:sent+65536])
                selector.unregister(self.process.stdin)
                selector.register(self.process.stdout, selectors.EVENT_READ)
                buffer = bytearray()
                while b'\n' not in buffer:
                    remaining = end-time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise BraidError('deadline')
                    part = os.read(self.process.stdout.fileno(), min(65536, self.limit+1-len(buffer)))
                    if not part:
                        raise BraidError('process_exited')
                    buffer.extend(part)
                    if len(buffer) > self.limit:
                        raise BraidError('response_too_large')
            if bytes(buffer).count(b'\n') != 1 or not buffer.endswith(b'\n'):
                raise BraidError('unexpected_protocol_data')
            reply = json.loads(buffer, object_pairs_hook=no_duplicates)
            if not isinstance(reply, dict) or reply.get('protocol_version') != 1 or reply.get('id') != identifier or ('result' in reply) == ('error' in reply):
                raise BraidError('invalid_protocol_response')
            if 'error' in reply:
                raise BraidError(reply['error']['code'])
            if not isinstance(reply['result'], dict):
                raise BraidError('invalid_protocol_result')
            return reply['result']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.close()
            raise BraidError('protocol_failure') from exc
        except BraidError as exc:
            if exc.code not in ('revision_conflict', 'dataset_mismatch', 'cancelled', 'operation_failed'):
                self.close()
            raise
        finally:
            self.lock.release()


class BraidBackend:
    """One WCU-owned database/config per immutable catalog generation."""
    def __init__(self, executable, root, expected_sha256):
        self.executable = Path(executable).resolve()
        self.root = Path(root)
        self.expected_sha256 = expected_sha256
        self.connection = None
        self.catalog_revision = None
        self.dataset = None
        self.lock = threading.Lock()

    def close(self):
        if self.connection:
            self.connection.close()
        self.connection = None
        self.catalog_revision = self.dataset = None

    def publish(self, snapshot, end):
        binary_hash = hashlib.sha256(self.executable.read_bytes()).hexdigest()
        if binary_hash != self.expected_sha256:
            raise BraidError('binary_identity_mismatch')
        revision = snapshot['revision']
        if self.catalog_revision == revision and self.connection and self.connection.process:
            return
        self.close()
        folder = self.root/revision
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        dataset_id = 'wcu:'+revision
        config = {'version': 1, 'dataset_id': dataset_id,
            'store': {'backend': 'sqlite', 'path': str((folder/'catalog.db').resolve())},
            'embed': {'provider': 'none'}, 'candidate_limit': 200,
            'defaults': {**POLICY, 'budget': {'max': 100000000}}}
        config_path = folder/'config.json'
        # JSON is a YAML subset; no external parser dependency in the adapter.
        atomic_json(config_path, config)
        self.connection = Connection([str(self.executable), 'serve', '--stdio', '--config', str(config_path)])
        hello = self.connection.start(end)
        current = hello['dataset']
        if current['id'] != dataset_id:
            raise BraidError('dataset_mismatch')
        nodes = []
        for record in snapshot['records'].values():
            text = ' '.join([record.get('intent', record['id']), *record.get('aliases', [])])
            nodes.append({'id': record['id'], 'type': record['record_type'], 'text': text,
                          'ts': record.get('evidence', {}).get('collected_at') or '1970-01-01T00:00:00Z', 'cost': 1, 'attrs': record})
        # Even an earlier uncertain mutation is reconciled using exact content,
        # before any revision-checked replacement is attempted.
        existing = self.read_exact(snapshot, list(snapshot['records']), current, end)
        if current['revision'] and existing != snapshot['records']:
            raise BraidError('publication_reconciliation_failed')
        if not current['revision']:
            current = self.connection.request('replace_snapshot', end, snapshot={'dataset_id': dataset_id,
                'expected_revision': current['revision'], 'batch': {'nodes': nodes, 'edges': [], 'embeddings': []}})
        self.dataset, self.catalog_revision = current, revision
        atomic_json(folder/'mapping.json', {'wcu_revision': revision, 'dataset': current,
            'binary_sha256': binary_hash, 'ranking_version': hello['ranking_version'], 'policy': POLICY})

    def read_exact(self, snapshot, identifiers, dataset, end):
        """Bound each read without weakening the connection's response cap.

        Every page has the same expected revision. A change aborts the complete
        read; callers never see partial closure or mixed-generation records.
        """
        result, page, estimated_bytes = {}, [], 0
        def read_page():
            reply = self.connection.request('get_many', end, get={'dataset_id': dataset['id'],
                'expected_revision': dataset['revision'], 'ids': page}, **self.timeout(end))
            nodes = reply['nodes']
            found = {n['id']: n['attrs'] for n in nodes}
            if (reply['dataset'] != dataset or len(found) != len(nodes) or
                    not set(found) <= set(page) or set(reply.get('missing_ids') or []) != set(page)-set(found)):
                raise BraidError('exact_read_mismatch')
            result.update(found)
        for identifier in identifiers:
            size = len(encode(snapshot['records'][identifier]))
            if page and (len(page) >= 64 or estimated_bytes+size > self.connection.limit//4):
                read_page()
                page, estimated_bytes = [], 0
            page.append(identifier)
            estimated_bytes += size
        if page:
            read_page()
        return result

    def timeout(self, end):
        remaining = end-time.monotonic()
        if remaining <= 0:
            raise BraidError('deadline')
        if self.connection and 'query_timeout' in self.connection.hello.get('capabilities', []):
            return {'timeout_ms': max(1, int(remaining*1000))}
        return {}

    def rank(self, snapshot, intent, allowed, end):
        remaining = end-time.monotonic()
        if remaining <= 0 or not self.lock.acquire(timeout=max(0, remaining)):
            raise BraidError('deadline')
        try:
            self.publish(snapshot, end)
            for attempt in range(2):
                try:
                    result = self.connection.request('query', end, query={'text': intent,
                        'dataset_id': self.dataset['id'], 'expected_revision': self.dataset['revision'],
                        'weights': POLICY['weights'], 'filters': {'allowed_ids': allowed},
                        'budget': {'max': 100000000}}, **self.timeout(end))
                    if result['dataset'] != self.dataset:
                        raise BraidError('mixed_revision')
                    identifiers = [item['id'] for item in result.get('items') or []]
                    if len(set(identifiers)) != len(identifiers) or not set(identifiers) <= set(allowed):
                        raise BraidError('eligibility_violation')
                    # Exact closure is read at the same generation. WCU renders only
                    # the immutable local records after confirming their identity.
                    required = set(identifiers)
                    pending = list(identifiers)
                    while pending:
                        for dep in snapshot['records'][pending.pop()].get('requires', []):
                            if dep not in required:
                                required.add(dep)
                                pending.append(dep)
                    exact = self.read_exact(snapshot, sorted(required), self.dataset, end)
                    if exact != {i: snapshot['records'][i] for i in required}:
                        raise BraidError('exact_closure_mismatch')
                    return identifiers, {'backend': 'braid', 'binary_sha256': self.expected_sha256,
                        'dataset': self.dataset, 'ranking_version': result['ranking_version'], 'policy': POLICY}
                except BraidError as exc:
                    if exc.code != 'revision_conflict' or attempt:
                        raise
                    current = self.connection.request('dataset', end)
                    # Dedicated immutable generation changed: don't combine it with
                    # the local snapshot. Reconcile the complete read or abstain.
                    if current != self.dataset:
                        raise BraidError('publication_changed')
            raise BraidError('revision_conflict')
        finally:
            self.lock.release()
