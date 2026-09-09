"""Versioned, immutable keyboard/action catalogs. No desktop input or focus changes."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

SCHEMA_VERSION = 1
NORMALIZER_VERSION = 'wcu-normalizer-1'
# Reviewed command meanings; source identifiers and notation remain untouched.
ALIASES = {
    ('chromium', 'IDC_FOCUS_LOCATION'): ['address bar', 'enter URL', 'navigate to website'],
    ('chromium', 'IDC_NEW_TAB'): ['open new browser tab'],
    ('chromium', 'IDC_COPY'): ['copy selection'],
    ('obsidian', 'file-explorer:new-file'): ['create new note'],
    ('herdr', 'split_vertical'): ['split terminal vertically', 'side by side panes'],
    ('herdr', 'help'): ['show keybindings', 'shortcut help'],
    ('obs', 'OBSBasic.StartRecording'): ['start recording', 'record screen'],
    ('obs', 'OBSBasic.StopRecording'): ['stop recording'],
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def stable_id(kind, *identity):
    return kind + ':' + digest(identity)[:24]


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalize(profiles, errors=None):
    records = {}
    manifest = {}
    for name, source_profile in sorted(profiles.items()):
        p = copy.deepcopy(source_profile)
        app = {'obsidian-editor': 'obsidian', 'lazyvim': 'neovim'}.get(name, name)
        sources = p.get('sources', [])
        fingerprint = digest({k: v for k, v in p.items() if k != 'collected_at'})
        scope = p.get('profile_id') or p.get('vault') or p.get('active_profile') or name
        manifest[name] = {'fingerprint': fingerprint, 'sources': sources,
                          'collected_at': p.get('collected_at'), 'coverage': p.get('coverage'),
                          'status': 'unavailable' if p.get('configuration_error') else 'available'}
        context_id = stable_id('context', name, scope)
        records[context_id] = {'id': context_id, 'record_type': 'context', 'schema_version': SCHEMA_VERSION,
            'app': app, 'scope': scope, 'prefix': p.get('prefix'),
            'leader': p.get('leader'), 'leader_default': p.get('leader_default'),
            'localleader': p.get('localleader'), 'localleader_default': p.get('localleader_default'),
            'config_fingerprint': fingerprint, 'coverage': p.get('coverage'),
            'required_checks': ['Verify intended focus, mode and shortcut applicability before input.',
                                'Inspect the result when no supported outcome predicate is known.'],
            'requires': []}
        bindings = list(p.get('bindings', [])) + list(p.get('disabled_bindings', []))
        assigned = {b['command'] for b in bindings}
        for field, assignment in (('disabled_commands', 'disabled'), ('unassigned_commands', 'unassigned'),
                                  ('configured_unassigned', 'unassigned'), ('configurable_commands', 'unassigned')):
            for item in p.get(field, []):
                b = {'command': item} if isinstance(item, str) else dict(item)
                if field == 'configurable_commands' and b['command'] in assigned:
                    continue
                bindings.append({**b, 'shortcut': None, 'assignment': assignment})
        for reference in p.get('references', []):
            identifier = stable_id('reference', name, scope, reference['source_id'])
            records[identifier] = {'id': identifier, 'record_type': 'reference', 'schema_version': SCHEMA_VERSION,
                'app': app, 'scope': scope, 'text': reference['text'], 'source_id': reference['source_id'],
                'evidence': reference.get('evidence'), 'executable': False, 'requires': [context_id]}
        for b in bindings:
            command = b['command']
            shortcut = b.get('shortcut')
            modes = b.get('modes')
            if modes is None and app == 'herdr':
                match = re.search(r'Herdr (\w+) mode', b.get('context', ''))
                modes = [match[1]] if match else None
            identity = (name, scope, b.get('context'), modes, command, shortcut, b.get('help_tag'), b.get('config_key'), b.get('extra'), b.get('trigger'), b.get('flags'), b.get('alternative_id'))
            identifier = stable_id('action', *identity)
            notation = b.get('notation', 'chord')
            strokes = b.get('strokes')
            if strokes is None and shortcut and notation == 'chord' and ' then ' not in shortcut and ' ' not in shortcut:
                strokes = [shortcut]
            # Vim sequences are parsed into ordered notation tokens, never uppercased.
            if strokes is None and shortcut and notation == 'vim' and not shortcut.startswith('CTRL-'):
                strokes = re.findall(r'<[^>]+>|\{[^}]+\}|.', shortcut)
            assignment = b.get('assignment', 'assigned' if shortcut else 'unassigned')
            if b.get('disabled') is True or b.get('enabled') is False:
                assignment = 'disabled'
            version_matches = p.get('version_matches')
            requirements = [{'fact': 'input_path', 'contains': app}]
            if modes:
                requirements.append({'fact': 'mode', 'one_of': modes})
            if b.get('extra'):
                requirements.append({'fact': 'plugin:'+b['extra'], 'equals': True})
            if b.get('required_control'):
                requirements.append({'fact': 'focused_control_role', 'equals': b['required_control']})
            requirements += [{'fact': 'config:'+name, 'equals': fingerprint},
                             {'fact': 'available:'+identifier, 'equals': True}]
            records[identifier] = {'id': identifier, 'record_type': 'action', 'schema_version': SCHEMA_VERSION,
                'app': app, 'catalog_app': name, 'scope': scope, 'mode': modes,
                'alternative_group': stable_id('alternatives', name, scope, command, b.get('context'), modes),
                'alternative_identity': list(identity), 'command_id': command, 'intent': b.get('description', command),
                'aliases': ALIASES.get((name, command), []), 'source_description': b.get('description', command),
                'source_record': b,
                'input': {'shortcut': shortcut, 'notation': notation, 'strokes': strokes,
                          'placeholders': re.findall(r'\{[^}]+\}|<count>|<leader>|<localleader>', shortcut or '', re.I),
                          'translation': b.get('translation')},
                'applicability': {'assignment': assignment, 'requirements': requirements,
                    'mode': modes, 'context': b.get('context'), 'condition': b.get('condition'),
                    'extra_installed': b.get('extra_listed_in_manifest'), 'version_matches': version_matches,
                    'collisions': b.get('same_sequence_candidates', []),
                    'interception': b.get('compositor_matches', []),
                    'unresolved': b.get('unresolved', []), 'configuration_error': p.get('configuration_error')},
                'evidence': {'kind': b.get('evidence', p.get('evidence')), 'source_identity': digest(sources),
                    'source_version': p.get('version'), 'installed_version': p.get('installed_version'),
                    'collected_at': p.get('collected_at'), 'config_fingerprint': fingerprint,
                    'coverage': p.get('coverage'), 'version_compatibility': version_matches},
                'entry_conditions': b.get('entry_conditions'), 'expected_effect': b.get('expected_effect'),
                'outcome_check': b.get('outcome_check'), 'requires_inspection': b.get('outcome_check') is None,
                'requires': [context_id]}
    snapshot = {'schema_version': SCHEMA_VERSION, 'normalizer_version': NORMALIZER_VERSION,
                'source_manifest': manifest, 'coverage_errors': errors or {},
                'records': dict(sorted(records.items()))}
    snapshot['revision'] = digest(snapshot)
    return snapshot


def validate_snapshot(snapshot):
    if snapshot.get('schema_version') != SCHEMA_VERSION or snapshot.get('normalizer_version') != NORMALIZER_VERSION:
        raise ValueError('context_schema_mismatch')
    if snapshot.get('revision') != digest({k: v for k, v in snapshot.items() if k != 'revision'}):
        raise ValueError('context_checksum_mismatch')
    for identifier, record in snapshot['records'].items():
        if record.get('id') != identifier or record.get('schema_version') != SCHEMA_VERSION:
            raise ValueError('invalid_context_record')
    return snapshot


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, pending = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


class SnapshotStore:
    def __init__(self, root):
        self.root = Path(root)

    def publish(self, snapshot):
        validate_snapshot(snapshot)
        revision = snapshot['revision']
        path = self.root/'snapshots'/f'{revision}.json'
        if path.exists():
            if path.read_bytes() != encode(snapshot):
                raise ValueError('immutable_snapshot_collision')
        else:
            atomic_json(path, snapshot)
        atomic_json(self.root/'current.json', {'revision': revision, 'status': 'available', 'published_at': utc_now()})
        return revision

    def failed(self, error_code):
        # Keep old immutable files for inspection; never call them a fresh refresh.
        atomic_json(self.root/'current.json', {'status': 'unavailable', 'error_code': error_code, 'attempted_at': utc_now()})

    def load(self):
        pointer = json.loads((self.root/'current.json').read_bytes())
        if pointer.get('status') != 'available':
            raise ValueError('context_refresh_unavailable')
        revision = pointer.get('revision', '')
        if not re.fullmatch('[0-9a-f]{64}', revision):
            raise ValueError('invalid_context_revision')
        result = validate_snapshot(json.loads((self.root/'snapshots'/f'{revision}.json').read_bytes()))
        if result['revision'] != revision:
            raise ValueError('mixed_context_revision')
        return result
