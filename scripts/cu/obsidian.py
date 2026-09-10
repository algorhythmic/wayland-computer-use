"""At-most-once Obsidian URI transfers with durable, content-free receipts.

Only registered vaults are readable. No note files are written here: Obsidian
creates them. Receipts reserve a destination before dispatch, including across
MCP processes/restarts. An uncertain operation is reconciled, never replayed.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import unicodedata
from urllib.parse import quote, urlencode

from .app_surfaces import dispatch_uri, validate_uri

MAX_ENTRIES = 50000
MAX_CONTENT_BYTES = 32000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_operation(operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{7,79}', operation_id):
        raise ValueError('operation_id must be 8-80 letters, digits, hyphens or underscores; reuse it after uncertainty')


def validate_note(vault, name, content, operation_id):
    validate_operation(operation_id)
    if not isinstance(vault, str) or not vault or len(vault) > 512 or any(ord(c) < 32 for c in vault):
        raise ValueError('Provide an exact registered vault name or ID')
    if not isinstance(name, str) or not name or name != name.strip() or name.startswith('.') or name.endswith('.') or \
            any(ord(c) < 32 or c in '/\\:*?"<>|#[]^' for c in name):
        raise ValueError('Provide a plain note name without paths, reserved characters or surrounding whitespace')
    filename = name if name.endswith('.md') else name+'.md'
    if len(filename.encode()) > 240:
        raise ValueError('Note filename must fit in 240 UTF-8 bytes')
    if not isinstance(content, str) or '\x00' in content or len(content.encode()) > MAX_CONTENT_BYTES:
        raise ValueError('Content must be UTF-8 text, at most 32000 bytes, without NUL')
    return filename


def build_uri(vault_id, filename, content):
    # file pins the vault-root destination, independent of new-note preferences.
    uri = 'obsidian://new?'+urlencode({'vault': vault_id, 'file': filename, 'content': content}, quote_via=quote, safe='')
    return validate_uri(uri)


class NoteTransfers:
    def __init__(self, state_root=None, config_path=None, launcher=None):
        state = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))
        config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))
        self.root = Path(state_root or os.environ.get('WCU_NOTE_STATE_DIR', str(state/'wayland-computer-use/notes')))
        self.config = Path(config_path or config/'obsidian/obsidian.json')
        self.launcher = launcher or dispatch_uri

    def vault(self, requested):
        with self.config.open('rb') as source:
            data = source.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError('Obsidian registry is oversized')
        records = json.loads(data).get('vaults', {})
        matches = []
        for identifier, item in records.items():
            path = Path(item.get('path', ''))
            if requested == identifier or requested == path.name:
                if path.is_absolute() and path.is_dir():
                    matches.append((identifier, path.resolve()))
        if len(matches) != 1:
            raise ValueError('Vault unavailable or ambiguous; use its registered ID')
        return matches[0]

    def state_ready(self, create=False):
        if not self.root.is_absolute():
            raise ValueError('Note receipt directory must be absolute')
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Note receipt directory must be user-owned and mode 0700')

    @contextmanager
    def locked(self, timeout_s=2):
        self.state_ready(create=True)
        fd = os.open(self.root/'.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Invalid receipt lock')
            end = time.monotonic()+timeout_s
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        raise TimeoutError('Note receipt store is busy; no dispatch attempted')
                    time.sleep(min(.02, max(0, end-time.monotonic())))
            yield
        finally:
            os.close(fd)

    def load(self, name):
        self.state_ready()
        fd = os.open(self.root/name, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 2_000_000:
                raise ValueError('Invalid note receipt')
            return json.load(source)

    def write(self, name, value):
        fd, temporary = tempfile.mkstemp(prefix='.receipt-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(value, output, separators=(',', ':'))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.root/name)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def candidates(self, vault, filename):
        # Handle Obsidian's collision suffix without reading unrelated note text.
        stem = filename[:-3]
        normalized = lambda s: unicodedata.normalize('NFC', s).casefold()
        pattern = re.compile(re.escape(normalized(stem))+r'(?: [1-9][0-9]{0,5})?\.md\Z')
        names = []
        with os.scandir(vault) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_ENTRIES:
                    raise ValueError('Vault root exceeds bounded reconciliation scope')
                if pattern.fullmatch(normalized(entry.name)):
                    if len(names) >= 1024:
                        raise ValueError('Too many matching note candidates for bounded reconciliation')
                    names.append(entry.name)
        return names

    def matching_file(self, path, receipt):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != receipt['content_bytes']:
                    return False
                data = source.read(MAX_CONTENT_BYTES+1)
                after = os.fstat(source.fileno())
                return info.st_mtime_ns == after.st_mtime_ns and len(data) == receipt['content_bytes'] and digest(data) == receipt['content_sha256']
        except (FileNotFoundError, OSError):
            return False

    def reconcile(self, receipt, timeout_ms=0):
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 30000:
            raise ValueError('timeout_ms must be 0-30000')
        identifier, vault = self.vault(receipt['vault_id'])
        if str(vault) != receipt['vault_path']:
            raise ValueError('Registered vault moved; cannot reconcile the old operation')
        end = time.monotonic()+timeout_ms/1000
        while True:
            candidates = [n for n in self.candidates(vault, receipt['filename']) if n not in receipt['preexisting']]
            matches = [n for n in candidates if self.matching_file(vault/n, receipt)]
            status = 'verified' if len(matches) == 1 else 'ambiguous' if len(matches) > 1 else 'unverified'
            if matches or time.monotonic() >= end:
                break
            time.sleep(min(.05, max(0, end-time.monotonic())))
        launch = receipt.get('launch', {'status': 'unknown', 'spawned': None})
        if status == 'unverified' and launch.get('spawned') is False:
            status = 'not_started'
        return {'operation_id': receipt['operation_id'], 'status': status,
                'application_accepted': 'verified_exact_content' if status == 'verified' else 'unverified',
                'note_path': str(vault/matches[0]) if status == 'verified' else None,
                'requested_path': str(vault/receipt['filename']),
                'candidate_count': len(candidates), 'matching_count': len(matches),
                'content_sha256': receipt['content_sha256'], 'launch': launch,
                'requires_review': status != 'verified', 'retry_safe': False,
                'next_action': 'complete' if status == 'verified' else 'Call obsidian_note_status with this operation_id; do not create another note'}

    def create(self, vault, name, content, operation_id, timeout_ms=5000, before_dispatch=None):
        filename = validate_note(vault, name, content, operation_id)
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= 30000:
            raise ValueError('timeout_ms must be 0-30000')
        identifier, directory = self.vault(vault)
        uri = build_uri(identifier, filename, content)  # Validate fully before reserving/launching.
        fingerprint = digest(json.dumps([str(directory), filename, content], ensure_ascii=False).encode())
        key = 'op-'+operation_id+'.json'
        destination = 'target-'+digest(unicodedata.normalize('NFC', str(directory/filename)).casefold().encode())+'.json'
        first = False
        with self.locked():
            try:
                receipt = self.load(key)
            except FileNotFoundError:
                preexisting = self.candidates(directory, filename)
                if any(unicodedata.normalize('NFC', n).casefold() == unicodedata.normalize('NFC', filename).casefold() for n in preexisting):
                    raise ValueError('Destination already exists; choose a fresh note name')
                try:
                    reservation = self.load(destination)
                except FileNotFoundError:
                    reservation = None
                if reservation is not None:
                    try:
                        previous = self.load('op-'+reservation['operation_id']+'.json')
                    except FileNotFoundError:
                        raise ValueError('Incomplete destination reservation; do not redispatch') from None
                    if previous.get('launch', {}).get('spawned') is not False:
                        raise ValueError('Destination reserved by operation '+reservation['operation_id']+'; reconcile it before using a new ID')
                receipt = {'format': 1, 'operation_id': operation_id, 'request_sha256': fingerprint,
                           'vault_id': identifier, 'vault_path': str(directory), 'filename': filename,
                           'content_bytes': len(content.encode()), 'content_sha256': digest(content.encode()),
                           'preexisting': preexisting, 'created_at': time.time(), 'phase': 'reserved'}
                # Reservation first. A crash between writes leaves a fail-closed target.
                self.write(destination, {'operation_id': operation_id})
                self.write(key, receipt)
                first = True
            if receipt['request_sha256'] != fingerprint:
                raise ValueError('operation_id already belongs to different vault, name or content')
        diagnostic = None
        if first:
            if before_dispatch:
                before_dispatch()
            try:
                launch = self.launcher(uri)
            except Exception as exc:
                # Even unexpected launcher faults leave a durable uncertain receipt.
                launch = {'status': 'unknown', 'spawned': None, 'error_type': type(exc).__name__}
            # Diagnostic text may contain app-echoed content even after redaction.
            # Return it once, but do not persist it alongside content-free receipts.
            diagnostic = launch.pop('diagnostic', None)
            launch['diagnostic_available'] = bool(diagnostic)
            receipt['launch'], receipt['phase'] = launch, 'attempted'
            with self.locked():
                self.write(key, receipt)
        result = self.reconcile(receipt, timeout_ms)
        if diagnostic:
            result['launch'] = {**result['launch'], 'diagnostic': diagnostic}
        result['dispatched_this_call'] = first and receipt.get('launch', {}).get('spawned') is not False
        result['action_performed'] = False if not first or receipt.get('launch', {}).get('spawned') is False else True if receipt.get('launch', {}).get('spawned') is True else 'unknown'
        result['reused_operation'] = not first
        return result

    def status(self, operation_id, timeout_ms=0):
        validate_operation(operation_id)
        try:
            receipt = self.load('op-'+operation_id+'.json')
        except FileNotFoundError:
            raise ValueError('Unknown operation_id; no note was dispatched by this receipt store') from None
        result = self.reconcile(receipt, timeout_ms)
        result.update(action_performed=False, dispatched_this_call=False)
        return result
