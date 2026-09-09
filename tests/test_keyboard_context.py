import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'scripts/keyboard_context.py'
spec = importlib.util.spec_from_file_location('keyboard_context', PATH)
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


def bind(flags='d', key='Home', mask=0, description='Dictation', submap=''):
    return (f'bind{flags}\n\tmodmask: {mask}\n\tsubmap: {submap}\n'
            f'\tkey: {key}\n\tkeycode: 0\n\tcatchall: false\n'
            f'\tdescription: {description}\n\tdispatcher: __lua\n\targ: 42\n')


class KeyboardContextTests(unittest.TestCase):
    def test_compositor_duplicates_release_and_submap_survive(self):
        records = context.hyprland_bindings(bind() + '\n' + bind('rd', submap='capture'))
        self.assertEqual(len(records), 2)
        self.assertEqual([r['trigger'] for r in records], ['press', 'release'])
        self.assertEqual(records[1]['context'], 'capture')

    def test_physical_key_is_not_guessed_from_us_layout(self):
        records = context.hyprland_bindings(bind(key='code:10', mask=64))
        self.assertEqual(records[0]['shortcut'], 'SUPER+code:10')
        self.assertNotEqual(context.canonical(records[0]['shortcut']), context.canonical('SUPER+1'))

    def test_ghostty_equals_key_and_colon_action_are_preserved(self):
        records = context.ghostty_bindings('keybind = ctrl+==increase_font_size:1\nkeybind = shift+enter=csi:13;2u\n')
        self.assertEqual(records[0]['shortcut'], 'ctrl+=')
        self.assertEqual(records[1]['command'], 'csi:13;2u')
        with self.assertRaises(ValueError):
            context.ghostty_bindings('unrecognized output')

    def test_override_replaces_defaults_and_empty_array_disables(self):
        original = {'bindings': [{'command': 'open', 'shortcut': 'CTRL+O'},
                                 {'command': 'save', 'shortcut': 'CTRL+S'}]}
        result = context.apply_obsidian_overrides(original, {
            'open': [], 'save': [{'modifiers': ['Mod', 'Shift'], 'key': 's'}]})
        self.assertEqual(result['disabled_commands'], ['open'])
        self.assertEqual(len(result['bindings']), 1)
        self.assertEqual(context.canonical(result['bindings'][0]['shortcut']), 'CTRL+SHIFT+S')
        self.assertEqual(len(original['bindings']), 2)

    def test_collision_checks_preserve_trigger_and_modifiers(self):
        compositor = context.hyprland_bindings(bind() + '\n' + bind('rd'))
        profile = {'bindings': [{'shortcut': 'Home'}, {'shortcut': '<Primary>Home'}]}
        context.conflicts(profile, compositor)
        self.assertEqual(len(profile['bindings'][0]['compositor_matches']), 2)
        self.assertEqual(profile['bindings'][1]['compositor_matches'], [])
        self.assertEqual(context.canonical('<shift><Primary>Return'), context.canonical('CTRL+SHIFT+Enter'))

    def test_failed_refresh_does_not_retain_old_runtime_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'omarchy.json').write_text('{"stale": true}')
            (root / 'omarchy.md').write_text('stale')
            ghostty = root / 'ghostty-input.txt'
            ghostty.write_text('keybind = ctrl+c=copy_to_clipboard:mixed\n')
            with patch.object(context, 'PACKAGES', ()):
                manifest = context.refresh(root, hypr_file=root / 'absent', ghostty_file=ghostty,
                    herdr_config=root / 'no-herdr', obs_config=root / 'no-obs', nvim_config=root / 'no-nvim')
            self.assertIn('omarchy', manifest['errors'])
            self.assertFalse((root / 'omarchy.json').exists())
            self.assertFalse((root / 'omarchy.md').exists())
            self.assertEqual(manifest['profiles']['ghostty']['bindings'], 1)

    def test_herdr_prefix_ranges_disabled_keys_and_commands(self):
        profile = {'sources': [], 'bindings': [], 'default_keys': {
            'prefix': 'ctrl+b', 'detach': 'prefix+q', 'new_tab': 'prefix+c',
            'switch_tab': 'prefix+1..9', 'navigate_pane_left': 'h'}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.toml'
            path.write_text('[keys]\nprefix="ctrl+space"\ndetach=""\n'
                            'new_tab=["prefix+c", "alt+c"]\n'
                            '[[keys.command]]\nkey="prefix+g"\ncommand="private shell argument"\n')
            result = context.herdr_profile(profile, path)
        self.assertIn('detach', result['unassigned_commands'])
        tabs = [b for b in result['bindings'] if b['command'] == 'switch_tab']
        self.assertEqual(len(tabs), 9)
        self.assertEqual(tabs[8]['strokes'], ['ctrl+space', '9'])
        new = [b for b in result['bindings'] if b['command'] == 'new_tab']
        self.assertEqual([b['strokes'] for b in new], [['ctrl+space', 'c'], ['alt+c']])
        self.assertNotIn('private shell argument', json.dumps(result))
        self.assertEqual(profile['bindings'], [])

    def test_vim_notation_does_not_become_a_case_insensitive_chord(self):
        compositor = context.hyprland_bindings(bind(key='N'))
        profile = {'bindings': [{'shortcut': 'n', 'notation': 'vim'},
                                {'shortcut': 'N', 'notation': 'vim'},
                                {'shortcut': '<leader>ff', 'notation': 'vim'}]}
        context.conflicts(profile, compositor)
        for binding in profile['bindings']:
            self.assertEqual(binding['compositor_matches'], [])
            self.assertIn('Translate', binding['compositor_check'])

    def test_herdr_sequence_checks_prefix_and_following_key(self):
        compositor = context.hyprland_bindings(bind(key='space', mask=4))
        profile = {'bindings': [{'shortcut': 'ctrl+space then c', 'strokes': ['ctrl+space', 'c']}]}
        context.conflicts(profile, compositor)
        self.assertEqual(len(profile['bindings'][0]['compositor_matches']), 1)

    def test_obs_reads_only_active_hotkeys_and_preserves_source_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'basic/profiles/Selected').mkdir(parents=True)
            (root / 'basic/scenes').mkdir()
            (root / 'user.ini').write_text('[Basic]\nProfileDir=Selected\nSceneCollectionFile=Collection\n')
            (root / 'basic/profiles/Selected/basic.ini').write_text(
                '[Service]\nStreamKey=private-token\n[Hotkeys]\n'
                'OBSBasic.StartRecording={"bindings":[{"key":"OBS_KEY_F9","control":true}]}\n')
            (root / 'basic/scenes/Collection.json').write_text(json.dumps({'sources': [
                {'name': 'Mic A', 'hotkeys': {'libobs.mute': []}},
                {'name': 'Mic B', 'hotkeys': {'libobs.mute': [{'key': 'OBS_KEY_M', 'alt': True}]}}],
                'quick_transitions': [{'id': 2, 'hotkeys': [{'key': 'OBS_KEY_F2'}]}]}))
            (root / 'basic/scenes/Inactive.json').write_text('{invalid json')
            result = context.obs_profile({'sources': [], 'bindings': []}, root)
        self.assertEqual([b['shortcut'] for b in result['bindings']], ['CTRL+F9', 'ALT+M', 'F2'])
        self.assertIn('quick_transitions', result['bindings'][2]['context'])
        self.assertIn('Mic B', result['bindings'][1]['context'])
        self.assertIn('Mic A', result['configured_unassigned'][0]['context'])
        self.assertNotIn('private-token', json.dumps(result))

    def test_lazyvim_extra_manifest_is_evidence_not_runtime_registration(self):
        extra = 'lazyvim.plugins.extras.coding.yanky'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'lazyvim.json').write_text(json.dumps({'extras': [extra]}))
            (root / 'init.lua').write_text('error("must never execute")')
            profile = {'sources': [], 'bindings': [{'extra': extra}, {'extra': 'missing'}]}
            result = context.lazyvim_context(profile, root)
        self.assertEqual([b['extra_listed_in_manifest'] for b in result['bindings']], [True, False])
        self.assertFalse(result['custom_mappings_resolved'])

    def test_catalogs_have_provenance_and_complete_records(self):
        for path in context.REFERENCES.glob('*.json'):
            profile = json.loads(path.read_text())
            self.assertTrue(profile['sources'], path)
            self.assertTrue(profile['coverage'], path)
            self.assertTrue(profile['version'], path)
            self.assertTrue(profile['bindings'], path)
            for binding in profile['bindings']:
                self.assertTrue(binding['shortcut'], (path, binding))
                self.assertTrue(binding['command'], (path, binding))
                self.assertTrue(binding['context'], (path, binding))
                self.assertTrue(binding['condition'], (path, binding))


if __name__ == '__main__':
    unittest.main()
