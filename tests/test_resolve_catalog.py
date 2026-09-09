"""Resolve catalog integration: key translation, UI scope and default-only evidence."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import keyboard_context
from cu.context_records import normalize, SnapshotStore
from cu.context_retrieval import context_for_task, eligibility, render
from server import key_args


def profile():
    result = json.loads((keyboard_context.REFERENCES / 'davinci-resolve.json').read_text())
    result['collected_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    return result


class ResolveCatalogTests(unittest.TestCase):
    def test_linux_chords_can_use_the_existing_input_contract(self):
        p = profile()
        by_command = {b['command']: b for b in p['bindings']}
        expected = {
            'File > Save Project': 'CTRL+s',
            'Edit > Redo': 'CTRL+SHIFT+z',
            'Playback > Loop/Unloop': 'CTRL+slash',
            'Timeline > Split Clips': 'CTRL+backslash',
            'Photo > Rotate Left': 'CTRL+bracketleft',
            'Edit > Multicam > Audio Only': 'ALT+SHIFT+backslash',
        }
        for command, chord in expected.items():
            self.assertEqual(by_command[command]['shortcut'], chord)
        for binding in p['bindings']:
            with self.subTest(command=binding['command']):
                self.assertTrue(key_args(binding['shortcut']))
                self.assertEqual(binding['strokes'], [binding['shortcut']])
                self.assertIn(binding['source_page'], p['sources'][0]['pages'])
                self.assertTrue(binding['source_url'].endswith('#page=' + str(binding['source_page'])))
                self.assertIn('p. ' + str(binding['source_page']), binding['context'])

    def test_track_and_multicam_number_keys_keep_distinct_applicability(self):
        snapshot = normalize({'davinci-resolve': profile()})
        records = [r for r in snapshot['records'].values() if r['record_type'] == 'action']
        track = next(r for r in records if r['command_id'] == 'Timeline > Set Video Destination 1')
        angle = next(r for r in records if r['command_id'] == 'Clip > Multicam Switch > Angle 1')
        self.assertEqual(track['input']['shortcut'], angle['input']['shortcut'])
        self.assertNotEqual(track['id'], angle['id'])
        for mode, included, excluded in [('edit-timeline', track, angle),
                                          ('edit-multicam-viewer', angle, track)]:
            facts = {'focused_app': 'davinci-resolve', 'mode': mode}
            self.assertEqual(eligibility(included, facts)[0], 'exploration')
            self.assertEqual(eligibility(excluded, facts)[0], 'excluded')

    def test_defaults_and_claimed_context_do_not_authorize_execution(self):
        snapshot = normalize({'davinci-resolve': profile()})
        payload, _ = context_for_task(snapshot, {
            'intent': 'switch multicam angle', 'max_bytes': 4096,
            'exact': {'app': 'davinci-resolve', 'command_id': 'Clip > Multicam Switch > Angle 1'},
            'facts': {'focused_app': 'davinci-resolve', 'mode': 'edit-multicam-viewer'},
        })
        self.assertFalse(payload['actions'])
        self.assertEqual(len(payload['exploration']), 1)
        candidate = payload['exploration'][0]
        self.assertFalse(candidate['executable'])
        self.assertIn('verify_source_version', candidate['missing_evidence'])
        self.assertIn('observe:mode', candidate['missing_evidence'])
        self.assertTrue(set(candidate['requires']) <= set(payload['context']))
        self.assertLessEqual(len(render(payload)), 4096)

    def test_configurable_and_undocumented_keys_are_not_assigned(self):
        p = profile()
        cut = [b for b in p['bindings'] if b['command'].startswith('Clip > Multicam Cut > Angle ')]
        switch = [b for b in p['bindings'] if b['command'].startswith('Clip > Multicam Switch > Angle ')]
        self.assertEqual({b['shortcut'] for b in cut}, {str(i) for i in range(1, 10)})
        self.assertEqual({b['shortcut'] for b in switch}, {f'ALT+{i}' for i in range(1, 10)})
        snapshot = normalize({'davinci-resolve': p})
        for command in ('Clip > Multicam Switch > Angle 25', 'Clip > Enable Clip', 'Clip > Disable Clip'):
            payload, _ = context_for_task(snapshot, {
                'intent': command, 'exact': {'app': 'davinci-resolve', 'command_id': command},
            })
            self.assertEqual(payload['status'], 'no_result')
            self.assertFalse(payload['actions'] or payload['exploration'])

    def test_refresh_discovers_catalog_and_publishes_searchable_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            hypr = root / 'hypr.txt'
            hypr.write_text('bindd\n\tmodmask: 8\n\tsubmap: \n\tkey: 1\n'
                            '\tkeycode: 0\n\tcatchall: false\n\tdescription: fixture binding\n')
            ghostty = root / 'ghostty.txt'
            ghostty.write_text('keybind = ctrl+c=copy_to_clipboard\n')
            output = root / 'output'
            with patch.object(keyboard_context, 'PACKAGES', ()):
                result = keyboard_context.refresh(output, hypr_file=hypr, ghostty_file=ghostty,
                    herdr_config=root / 'no-herdr', obs_config=root / 'no-obs', nvim_config=root / 'no-nvim')
            self.assertIn('davinci-resolve', result['profiles'])
            refreshed = json.loads((output / 'davinci-resolve.json').read_text())
            self.assertIsNone(refreshed['installed_version'])
            self.assertIsNone(refreshed['version_matches'])
            self.assertFalse(refreshed['custom_mappings_resolved'])
            angle = next(b for b in refreshed['bindings'] if b['command'] == 'Clip > Multicam Switch > Angle 1')
            self.assertEqual(angle['compositor_matches'][0]['command'], 'fixture binding')
            snapshot = SnapshotStore(output / 'normalized').load()
            payload, _ = context_for_task(snapshot, {
                'intent': 'metadata', 'exact': {'app': 'davinci-resolve', 'shortcut': 'Tab'},
            })
            candidate = payload['exploration'][0]
            self.assertIn('metadata', candidate['command_id'].lower())
            self.assertIn('does not specify', candidate['applicability']['condition'])
            self.assertTrue((output / 'davinci-resolve.md').is_file())


if __name__ == '__main__':
    unittest.main()
