#!/usr/bin/env python3
"""Read-only keyboard catalogs for planning. Never dispatches a shortcut.

Refresh writes machine-specific snapshots to ignored .dev/keyboard-context.
Source catalogs are references, not proof that an app command is currently usable.
"""
import argparse
import copy
import configparser
import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / 'skills/wayland-computer-use/references/keyboard'
DEFAULT_OUTPUT = ROOT / '.dev/keyboard-context'
PACKAGES = ('omarchy', 'hyprland', 'chromium', 'obsidian', 'ghostty', 'nautilus',
            'herdr', 'obs-studio', 'neovim', 'omarchy-nvim')
MODIFIERS = ((64, 'SUPER'), (4, 'CTRL'), (8, 'ALT'), (1, 'SHIFT'))


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:300] or result.stdout.strip()[:300]
                           or 'Query failed: ' + argv[0])
    return result.stdout


def canonical(shortcut):
    """Normalize known spelling only. Never infer a physical key's layout."""
    aliases = {'PRIMARY': 'CTRL', 'CONTROL': 'CTRL', 'MOD': 'CTRL',
               'META': 'SUPER', 'LOGO': 'SUPER', 'ENTER': 'RETURN',
               'ESC': 'ESCAPE', 'ARROW_LEFT': 'LEFT', 'ARROW_RIGHT': 'RIGHT',
               'ARROW_UP': 'UP', 'ARROW_DOWN': 'DOWN', 'PAGEDOWN': 'PAGE_DOWN',
               'PAGEUP': 'PAGE_UP', 'NEXT': 'PAGE_DOWN', 'PRIOR': 'PAGE_UP',
               ',': 'COMMA', '.': 'PERIOD', '/': 'SLASH', ';': 'SEMICOLON',
               '[': 'BRACKETLEFT', ']': 'BRACKETRIGHT', '=': 'EQUAL', '-': 'MINUS'}
    text = re.sub(r'<([^>]+)>', r'\1+', shortcut).upper().strip()
    if text.endswith('++'):
        text = text[:-1] + 'PLUS'
    parts = [aliases.get(p.strip(), p.strip()) for p in text.split('+')]
    mods = [m for m in ('SUPER', 'CTRL', 'ALT', 'ALTGR', 'SHIFT') if m in parts]
    keys = [p for p in parts if p not in mods]
    return '+'.join(mods + keys)


def hyprland_bindings(text):
    # Plain output is used because Lua dispatcher arguments can break JSON output
    # on the installed compositor. Retain duplicate, release and modal bindings.
    records = []
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = block.splitlines()
        if not lines or not lines[0].startswith('bind'):
            raise ValueError('Unexpected Hyprland binding record')
        fields = dict((key.strip(), value.strip()) for line in lines[1:]
                      if ':' in line for key, value in [line.split(':', 1)])
        mask = int(fields['modmask'])
        mods = [name for bit, name in MODIFIERS if mask & bit]
        if mask & ~77:
            mods.append('MODMASK:' + str(mask & ~77))
        key = fields.get('key') or 'code:' + fields.get('keycode', 'unknown')
        flags = lines[0][4:]
        records.append({'shortcut': '+'.join(mods + [key]),
                        'command': fields.get('description') or 'Unlabelled compositor binding',
                        'context': fields.get('submap') or 'global',
                        'trigger': 'release' if 'r' in flags else 'press',
                        'repeat': 'e' in flags, 'non_consuming': 'n' in flags,
                        'flags': flags, 'catchall': fields.get('catchall') == 'true',
                        'keycode': fields.get('keycode'),
                        'condition': 'Binding scope and trigger must match; presence does not authorize its effect.'})
    if not records:
        raise ValueError('Hyprland returned no bindings')
    return records


def ghostty_bindings(text):
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'keybind\s*=\s*(.+?)=([a-z_]+(?::.*)?)', line)
        if not match:
            raise ValueError('Unrecognized Ghostty keybind syntax')
        records.append({'shortcut': match[1], 'command': match[2],
                        'context': 'Ghostty terminal',
                        'condition': 'Effective config on disk; an already running terminal may need config reload.'})
    if not records:
        raise ValueError('Ghostty returned no keybindings')
    return records


def apply_obsidian_overrides(profile, overrides):
    if not isinstance(overrides, dict):
        raise ValueError('Obsidian hotkeys must be an object')
    profile = copy.deepcopy(profile)
    profile['disabled_bindings'] = [dict(b, assignment='disabled') for b in profile['bindings']
                                    if b['command'] in overrides and overrides[b['command']] == []]
    profile['bindings'] = [b for b in profile['bindings'] if b['command'] not in overrides]
    profile['disabled_commands'] = []
    for identifier, keys in overrides.items():
        if not isinstance(keys, list):
            raise ValueError('Invalid Obsidian hotkey entry')
        if not keys:
            profile['disabled_commands'].append(identifier)
        for key in keys:
            if not isinstance(key, dict) or not isinstance(key.get('modifiers'), list):
                raise ValueError('Invalid Obsidian hotkey')
            symbol = key.get('code') or key.get('key')
            if not isinstance(symbol, str) or not all(isinstance(m, str) for m in key['modifiers']):
                raise ValueError('Invalid Obsidian key or modifiers')
            if key.get('code'):
                symbol = 'physical:' + symbol
            profile['bindings'].append({'shortcut': '+'.join(key['modifiers'] + [symbol]),
                'command': identifier, 'context': 'Obsidian vault override',
                'condition': 'Command must still be registered and applicable.', 'evidence': 'vault_override'})
    return profile


def conflicts(profile, compositor):
    """Annotate possible interception; absence of a match is not proof of delivery."""
    for binding in profile['bindings']:
        if binding.get('notation') in ('vim', 'gesture'):
            binding['compositor_matches'] = []
            binding['compositor_check'] = 'Translate the mode-specific sequence or gesture before checking interception.'
            continue
        # GTK shortcuts use spaces between alternatives; other catalogs use one chord per row.
        choices = binding.get('strokes') or (binding['shortcut'].split() if '<' in binding['shortcut'] else [binding['shortcut']])
        keys = {canonical(k) for k in choices}
        binding['compositor_matches'] = [
            {'shortcut': b['shortcut'], 'command': b['command'], 'context': b['context'],
             'trigger': b['trigger'], 'non_consuming': b['non_consuming']}
            for b in compositor if canonical(b['shortcut']) in keys or b['catchall']]


def source(path):
    path = Path(path)
    return {'path': str(path), 'exists': path.exists(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}


def herdr_profile(profile, config_path):
    """Merge declared keys without executing commands or pretending to validate Herdr's registry."""
    profile = copy.deepcopy(profile)
    config_path = Path(config_path)
    overrides = tomllib.loads(config_path.read_text()).get('keys', {}) if config_path.exists() else {}
    defaults = profile['default_keys']
    values = defaults | overrides
    prefix = values['prefix']
    if not isinstance(prefix, str) or not prefix:
        raise ValueError('Herdr prefix must be a nonempty string')
    profile['prefix'] = prefix
    profile['sources'].append(source(config_path))
    profile['evidence'] = 'source_defaults_and_config'
    profile['unassigned_commands'] = []
    profile['bindings'] = [b for b in profile['bindings'] if not b.get('config_key')]
    profile['unresolved_config_fields'] = sorted(set(overrides) - set(defaults) - {'command', 'indexed', 'fullscreen'})
    if 'fullscreen' in overrides and 'zoom' not in overrides:
        values['zoom'] = overrides['fullscreen']

    def append(identifier, raw, context, evidence):
        alternatives = [raw] if isinstance(raw, str) else raw
        if not isinstance(alternatives, list) or not all(isinstance(x, str) for x in alternatives):
            raise ValueError('Invalid Herdr binding: ' + identifier)
        if not any(x.strip() for x in alternatives):
            profile['unassigned_commands'].append(identifier)
        for shortcut in alternatives:
            if not shortcut.strip():
                continue
            expanded = [shortcut.replace('1..9', str(i)) for i in range(1, 10)] if '1..9' in shortcut else [shortcut]
            for chord in expanded:
                strokes = [prefix, chord[7:]] if chord.lower().startswith('prefix+') else [chord]
                profile['bindings'].append({'shortcut': ' then '.join(strokes), 'strokes': strokes,
                    'command': identifier, 'config_key': identifier, 'context': context, 'evidence': evidence,
                    'condition': 'Declared configuration; current Herdr help resolves validation, collisions and remote-client overrides.'})

    for identifier in defaults:
        context = 'Herdr navigate mode' if identifier.startswith('navigate_') else 'Herdr terminal mode'
        if identifier == 'remote_image_paste':
            context = 'Herdr remote client with clipboard image'
        append(identifier, values[identifier], context, 'config_override' if identifier in overrides else 'source_default')
    for i, item in enumerate(overrides.get('command', [])):
        append('custom-command-' + str(i + 1), item['key'], 'Herdr custom command (' + item.get('type', 'shell') + ')', 'config_override')
    for identifier, modifier in overrides.get('indexed', {}).items():
        if modifier:
            append('legacy-indexed-' + identifier, modifier + '+1..9', 'Herdr legacy indexed configuration', 'config_override')
    for b in profile['bindings']:
        if b.get('config_key'):
            # Herdr validates aliases and resolves conflicts itself; expose candidates
            # instead of reproducing only part of that registry algorithm.
            b['same_sequence_candidates'] = [other['command'] for other in profile['bindings']
                if other is not b and other['context'] == b['context']
                and other.get('strokes') == b.get('strokes')]
    return profile


def obs_profile(profile, config_dir):
    profile = copy.deepcopy(profile)
    root = Path(config_dir)
    profile['configuration_found'] = root.is_dir()
    if not root.is_dir():
        return profile
    settings = configparser.ConfigParser(interpolation=None)
    settings.optionxform = str
    for name in ('global.ini', 'user.ini'):
        settings.read(root / name)
        profile['sources'].append(source(root / name))
    basic = settings['Basic'] if settings.has_section('Basic') else {}
    profile['active_profile'] = basic.get('Profile')
    profile['active_scene_collection'] = basic.get('SceneCollection')
    profile['configured_unassigned'] = []

    def child(folder, name):
        if not name or Path(name).name != name or name in ('.', '..'):
            raise ValueError('Missing or invalid OBS active profile/collection path')
        return root / folder / name

    def append(identifier, bindings, scope):
        if not isinstance(bindings, list):
            raise ValueError('Invalid OBS hotkey list')
        if not bindings:
            profile['configured_unassigned'].append({'command': identifier, 'context': scope})
        for binding in bindings:
            key = binding.get('key')
            if not key or key == 'OBS_KEY_NONE':
                continue
            modifiers = [value for field, value in (('control', 'CTRL'), ('alt', 'ALT'), ('shift', 'SHIFT'), ('command', 'SUPER')) if binding.get(field)]
            key = key.removeprefix('OBS_KEY_')
            aliases = {'RETURN': 'Return', 'ESCAPE': 'Escape', 'SPACE': 'space', 'DELETE': 'Delete', 'BACKSPACE': 'BackSpace', 'UP': 'Up', 'DOWN': 'Down', 'LEFT': 'Left', 'RIGHT': 'Right'}
            profile['bindings'].append({'shortcut': '+'.join(modifiers + [aliases.get(key, key)]),
                'command': identifier, 'context': scope, 'evidence': 'config_override',
                'condition': 'Configured hotkey; source identity, active collection, focus settings and backend support determine effect.'})

    if basic.get('ProfileDir'):
        path = child('basic/profiles', basic['ProfileDir']) / 'basic.ini'
        data = configparser.ConfigParser(interpolation=None)
        data.optionxform = str
        data.read(path)
        profile['sources'].append(source(path))
        if data.has_section('Hotkeys'):
            for identifier, value in data['Hotkeys'].items():
                append(identifier, json.loads(value).get('bindings', []), 'OBS active profile')
    if basic.get('SceneCollectionFile'):
        name = basic['SceneCollectionFile']
        path = child('basic/scenes', name if name.endswith('.json') else name + '.json')
        profile['sources'].append(source(path))
        if path.exists():
            def visit(value, scope):
                if isinstance(value, dict):
                    scope = scope + '/' + str(value.get('name', value.get('id', 'object')))
                    hotkeys = value.get('hotkeys', {})
                    if isinstance(hotkeys, list):
                        append('OBSBasic.QuickTransition.' + str(value.get('id', 'unknown')), hotkeys, scope)
                    elif isinstance(hotkeys, dict):
                        for identifier, bindings in hotkeys.items():
                            append(identifier, bindings, scope)
                    else:
                        raise ValueError('Invalid OBS scene hotkeys')
                    for key, item in value.items():
                        if key != 'hotkeys' and isinstance(item, (dict, list)):
                            visit(item, scope + '/' + key)
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        visit(item, scope + '[' + str(i) + ']')
            visit(json.loads(path.read_text()), 'OBS active scene collection')
    profile['evidence'] = 'source_defaults_and_config'
    return profile


def lazyvim_context(profile, config_dir):
    profile = copy.deepcopy(profile)
    root = Path(config_dir)
    manifest = root / 'lazyvim.json'
    lock = root / 'lazy-lock.json'
    extras = json.loads(manifest.read_text()).get('extras', []) if manifest.exists() else None
    profile['configured_extras'] = extras
    profile['installed_revision'] = json.loads(lock.read_text()).get('LazyVim', {}).get('commit') if lock.exists() else None
    # Lua is executable configuration. Record freshness without running startup,
    # loading plugins, installing anything, or claiming to resolve arbitrary Lua.
    paths = [manifest, lock, root / 'init.lua', *sorted(root.rglob('*.lua'))]
    profile['sources'] += [source(path) for path in dict.fromkeys(paths)]
    profile['custom_mappings_resolved'] = False
    for binding in profile['bindings']:
        if binding.get('extra'):
            binding['extra_listed_in_manifest'] = binding['extra'] in extras if extras is not None else None
    return profile


def _refresh(output, vault=None, hypr_file=None, ghostty_file=None, herdr_config=None, obs_config=None, nvim_config=None):
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    collected_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    versions = {}
    errors = {}
    try:
        # Query individually so one absent optional app does not hide all versions.
        for package in PACKAGES:
            result = subprocess.run(['pacman', '-Q', package], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                name, version = result.stdout.strip().split(maxsplit=1)
                versions[name] = version
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        errors['versions'] = str(exc)[:300]
    profiles = {}
    for path in sorted(REFERENCES.glob('*.json')):
        profile = json.loads(path.read_text())
        name = profile['environment']
        package = profile.get('package', 'obsidian' if name == 'obsidian-editor' else name)
        profile['installed_version'] = versions.get(package)
        profile['version_matches'] = (versions[package].rsplit('-', 1)[0] == profile['version']) if package in versions and profile.get('version_kind') != 'documentation_snapshot' else None
        profiles[name] = profile
    for name, parser, argv, fixture in (
        ('omarchy', hyprland_bindings, ['hyprctl', 'binds'], hypr_file),
        ('ghostty', ghostty_bindings, ['ghostty', '+list-keybinds', '--plain'], ghostty_file)):
        try:
            if name == 'omarchy' and not fixture:
                from server import session_env
                session_env()
            text = Path(fixture).read_text() if fixture else command(argv)
            profiles[name] = {'schema': 1, 'environment': name, 'version': versions.get(name),
                'evidence': 'imported_snapshot' if fixture else 'runtime' if name == 'omarchy' else 'effective_config',
                'coverage': 'All records returned by ' + ' '.join(argv) + '. Dynamic bindings may change with UI state.',
                'sources': [source(fixture)] if fixture else [{'command': argv}], 'bindings': parser(text)}
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            errors[name] = str(exc)[:300]
    if vault:
        config = Path(vault).expanduser().resolve() / '.obsidian'
        if not config.is_dir():
            raise ValueError('Vault has no .obsidian configuration directory')
        hotkeys = config / 'hotkeys.json'
        profile = apply_obsidian_overrides(profiles['obsidian'], json.loads(hotkeys.read_text()) if hotkeys.exists() else {})
        profile['vault'] = str(config.parent)
        profile['sources'] += [source(config / name) for name in ('hotkeys.json', 'core-plugins.json', 'community-plugins.json', 'app.json')]
        for field, filename in (('core_plugins', 'core-plugins.json'), ('community_plugins', 'community-plugins.json')):
            path = config / filename
            profile[field] = json.loads(path.read_text()) if path.exists() else None
        profile['community_commands_enumerated'] = False
        app_config = json.loads((config / 'app.json').read_text()) if (config / 'app.json').exists() else {}
        profile['editor_options'] = {k: app_config[k] for k in ('vimMode', 'legacyEditor', 'livePreview', 'defaultViewMode') if k in app_config}
        profiles['obsidian'] = profile
    config_home = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    for name, builder, path in (
        ('herdr', herdr_profile, herdr_config or os.environ.get('HERDR_CONFIG_PATH') or config_home / 'herdr/config.toml'),
        ('obs', obs_profile, obs_config or config_home / 'obs-studio'),
        ('lazyvim', lazyvim_context, nvim_config or config_home / 'nvim')):
        if name not in profiles:
            continue
        try:
            profiles[name] = builder(profiles[name], path)
        except (OSError, ValueError, KeyError, TypeError, configparser.Error) as exc:
            errors[name] = str(exc)[:300]
            profiles[name]['configuration_error'] = errors[name]
            profiles[name]['coverage'] += ' Local configuration could not be resolved; these remain source defaults.'
    compositor = profiles.get('omarchy', {}).get('bindings', [])
    for name, profile in profiles.items():
        profile['collected_at'] = collected_at
        if name != 'omarchy':
            conflicts(profile, compositor)
        profile['compositor_check'] = 'Snapshot spelling matches only; physical keys, remaps, submaps and other interceptors need live verification.' if compositor else 'unavailable'
        (output / (name + '.json')).write_text(json.dumps(profile, indent=2, ensure_ascii=False) + '\n')
        (output / (name + '.md')).write_text(markdown(profile))
    # Failed refreshes must not leave old runtime snapshots looking current.
    for name in ('omarchy', 'ghostty'):
        if name not in profiles:
            for suffix in ('.json', '.md'):
                (output / (name + suffix)).unlink(missing_ok=True)
    manifest = {'generated_at': collected_at,
                'versions': versions, 'errors': errors,
                'profiles': {name: {'bindings': len(p['bindings']), 'coverage': p['coverage'],
                                    'evidence': p['evidence']} for name, p in profiles.items()}}
    (output / 'index.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'index.md').write_text('# Keyboard environment context\n\n' + manifest['generated_at'] + '\n\n' +
        '\n'.join(f"- [{name}]({name}.md): {len(p['bindings'])} bindings; {p['evidence']}." for name, p in profiles.items()) +
        '\n\nCoverage is limited to each named source. See each profile before treating a shortcut as available.\n' +
        ('\nQuery errors: `' + json.dumps(errors) + '`\n' if errors else ''))
    from cu.context_records import normalize, SnapshotStore
    manifest['context_revision'] = SnapshotStore(output/'normalized').publish(normalize(profiles, errors))
    return manifest


def refresh(output, *args, **kwargs):
    from cu.context_records import SnapshotStore
    try:
        return _refresh(Path(output), *args, **kwargs)
    except Exception as exc:
        SnapshotStore(Path(output)/'normalized').failed(type(exc).__name__)
        raise


def markdown(profile):
    def cell(value):
        return html.escape(str(value), quote=False).replace('|', '\\|').replace('\n', ' ')
    lines = [f"# {profile['environment']} shortcuts", '',
             f"Version: {profile.get('version')}. Evidence: {profile['evidence']}.",
             f"Collected: {profile.get('collected_at', 'not collected')}.",
             f"Installed version: {profile.get('installed_version', profile.get('version'))}. "
             f"Source version matches: {profile.get('version_matches', 'not applicable')}.",
             '', profile['coverage'], '',
             '| Shortcut | Command | Context / condition | Compositor match |', '|---|---|---|---|']
    for b in profile['bindings']:
        intercepted = '; '.join(x['command'] + ' (' + x['trigger'] + ')' for x in b.get('compositor_matches', []))
        context = b.get('context', '') + '; ' + b.get('condition', '')
        if b.get('modes'):
            context += '; modes: ' + ', '.join(b['modes'])
        if 'extra_listed_in_manifest' in b:
            context += '; extra listed: ' + str(b['extra_listed_in_manifest'])
        if b.get('same_sequence_candidates'):
            context += '; competing bindings: ' + ', '.join(b['same_sequence_candidates'])
        if b.get('compositor_check'):
            intercepted = b['compositor_check']
        if 'trigger' in b:
            context += '; ' + b['trigger'] + ('; repeats' if b.get('repeat') else '')
        lines.append('| ' + ' | '.join(map(cell, [b['shortcut'], b['command'], context, intercepted])) + ' |')
    for field in ('unassigned_commands', 'configurable_commands', 'configured_unassigned', 'configuration_error'):
        if profile.get(field):
            lines += ['', field.replace('_', ' ').capitalize() + ':', '', '```json', json.dumps(profile[field], indent=2, ensure_ascii=False), '```']
    lines += ['', 'Sources:', '', *['- `' + json.dumps(s, ensure_ascii=False) + '`' for s in profile['sources']], '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('refresh', 'show'))
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--vault', type=Path)
    parser.add_argument('--hyprland-file', type=Path, help='Import an explicitly captured plain hyprctl binds snapshot')
    parser.add_argument('--ghostty-file', type=Path)
    parser.add_argument('--herdr-config', type=Path, help='Herdr config.toml; defaults to HERDR_CONFIG_PATH or XDG config')
    parser.add_argument('--obs-config', type=Path, help='OBS config directory; reads only selected profile and collection hotkeys')
    parser.add_argument('--nvim-config', type=Path, help='Neovim config directory; inventories Lua without executing it')
    parser.add_argument('--environment', help='Environment name; omit to list profiles')
    parser.add_argument('--search', default='', help='All whitespace-separated terms must occur in a binding')
    args = parser.parse_args()
    if args.action == 'refresh':
        try:
            result = refresh(args.output, args.vault, args.hyprland_file, args.ghostty_file,
                             args.herdr_config, args.obs_config, args.nvim_config)
        except Exception as exc:
            from cu.context_records import SnapshotStore
            SnapshotStore(args.output/'normalized').failed(type(exc).__name__)
            raise
    elif args.environment:
        if not re.fullmatch(r'[a-z][a-z0-9-]*', args.environment):
            parser.error('Invalid environment name')
        result = json.loads((args.output / (args.environment + '.json')).read_text())
        result['binding_count_total'] = len(result['bindings'])
        result['bindings'] = [b for b in result['bindings'] if all(term in json.dumps(b).lower() for term in args.search.lower().split())]
        result['binding_count_returned'] = len(result['bindings'])
    else:
        result = json.loads((args.output / 'index.json').read_text())
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
