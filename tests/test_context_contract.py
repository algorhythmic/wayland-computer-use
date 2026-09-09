import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from cu.context_records import normalize, SnapshotStore, digest, encode
from cu.context_retrieval import context_for_task, SessionEvidence, render


def fixture():
    return normalize({'chromium': {'environment': 'chromium', 'evidence': 'source_defaults', 'version': '1',
        'version_matches': True, 'installed_version': '1', 'coverage': 'sanitized fixture',
        'sources': [{'id': 'source', 'sha256': 'abc'}], 'collected_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'bindings': [{'command': 'IDC_FOCUS_LOCATION', 'shortcut': 'CTRL+l', 'modes': ['normal']},
                     {'command': 'IDC_FOCUS_LOCATION', 'shortcut': 'CTRL+i', 'modes': ['insert']}]}})


def evidence_for(snapshot, mode='normal'):
    facts = {'focused_app': 'chromium', 'mode': mode}
    for r in snapshot['records'].values():
        if r['record_type'] == 'action':
            facts['available:'+r['id']] = True
            facts['config:'+r['catalog_app']] = r['evidence']['config_fingerprint']
    return SessionEvidence('session:1', facts)


class ContextTests(unittest.TestCase):
    def test_exact_case_and_ids_survive_unrelated_changes(self):
        p = {'neovim': {'environment': 'neovim', 'bindings': [{'shortcut': key, 'command': 'search', 'notation': 'vim', 'modes': ['n']} for key in ('n', 'N', '?')]}}
        first = normalize(p)
        p['herdr'] = {'environment': 'herdr', 'prefix': 'ctrl+a', 'bindings': [{'shortcut': 'ctrl+a then v', 'strokes': ['ctrl+a', 'v'], 'command': 'split_vertical'}]}
        second = normalize(p)
        self.assertTrue(set(first['records']) <= set(second['records']))
        for key in ('n', 'N', '?'):
            result, _ = context_for_task(second, {'intent': 'search', 'exact': {'shortcut': key}})
            self.assertEqual([r['input']['shortcut'] for r in result['exploration']], [key])
        self.assertIn('ctrl+a', [r.get('prefix') for r in second['records'].values()])

    def test_claims_are_not_evidence_and_eligibility_precedes_ranking(self):
        snapshot = fixture()
        claims = evidence_for(snapshot).facts
        payload, _ = context_for_task(snapshot, {'intent': 'address bar', 'facts': claims})
        self.assertFalse(payload['actions'])
        self.assertTrue(payload['exploration'])
        payload, _ = context_for_task(snapshot, {'intent': 'enter URL', 'observation_ref': 'session:1'}, evidence_for(snapshot))
        self.assertEqual(len(payload['actions']), 1)
        self.assertEqual(payload['actions'][0]['mode'], ['normal'])
        self.assertFalse(payload['exploration'])

    def test_every_byte_budget_and_complete_bundle(self):
        snapshot = fixture()
        for limit in (256, 512, 1024, 2048, 4096, 8192):
            payload, _ = context_for_task(snapshot, {'intent': 'address bar', 'max_bytes': limit, 'observation_ref': 'session:1'}, evidence_for(snapshot))
            self.assertLessEqual(len(render(payload)), limit)
            self.assertEqual(payload['output']['bytes'], len(encode(payload)))
            for r in payload.get('actions', [])+payload.get('exploration', []):
                self.assertTrue(set(r['requires']) <= set(payload['context']))

    def test_missing_dependency_never_admits_action(self):
        snapshot = fixture()
        for key in list(snapshot['records']):
            if snapshot['records'][key]['record_type'] == 'context':
                del snapshot['records'][key]
        snapshot['revision'] = digest({k: v for k, v in snapshot.items() if k != 'revision'})
        payload, _ = context_for_task(snapshot, {'intent': 'address bar'})
        self.assertFalse(payload['actions'] or payload['exploration'])

    def test_disabled_unassigned_missing_extra_and_version_mismatch(self):
        p = {'obs': {'environment': 'obs', 'configurable_commands': ['OBSBasic.StartRecording'],
             'disabled_commands': ['disabled'], 'bindings': []},
             'lazyvim': {'environment': 'lazyvim', 'bindings': [{'command': 'record', 'shortcut': 'n', 'extra': 'x', 'extra_listed_in_manifest': False}]},
             'chromium': {'environment': 'chromium', 'version_matches': False, 'bindings': [{'command': 'record', 'shortcut': 'N'}]}}
        snapshot = normalize(p)
        payload, diagnostics = context_for_task(snapshot, {'intent': 'start recording'})
        self.assertEqual(payload['status'], 'no_result')
        self.assertFalse(payload['actions'] or payload['exploration'])
        self.assertEqual(len(diagnostics['excluded']), 4)

    def test_atomic_publish_failure_and_deletion(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SnapshotStore(folder)
            snapshot = fixture()
            store.publish(snapshot)
            with patch('cu.context_records.os.replace', side_effect=OSError('failure')):
                with self.assertRaises(OSError):
                    store.publish(normalize({}))
            self.assertEqual(store.load(), snapshot)
            store.publish(normalize({}))
            self.assertEqual(store.load()['records'], {})
            store.failed('collector_failure')
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                store.load()

    def test_optional_backend_failure_keeps_local_snapshot(self):
        class Failing:
            def rank(self, *args):
                raise TimeoutError()
        payload, diagnostics = context_for_task(fixture(), {'intent': 'address bar', 'backend': 'braid'}, backend=Failing())
        self.assertTrue(payload['exploration'])
        self.assertEqual(diagnostics['backend'], 'local')
        self.assertEqual(diagnostics['failures'], ['TimeoutError'])

    def test_wrong_revision_and_old_evidence_cannot_authorize(self):
        evidence = evidence_for(fixture())
        for value in (SessionEvidence('session:old', evidence.facts), SessionEvidence('session:1', evidence.facts, 1)):
            result, _ = context_for_task(fixture(), {'intent': 'address bar', 'observation_ref': 'session:1'}, value)
            self.assertFalse(result['actions'])

    def test_deadline_includes_snapshot_validation_and_survives_empty_result(self):
        snapshot=fixture()
        with patch('cu.context_retrieval.time.monotonic',side_effect=[0, .002, .002]):
            payload,_=context_for_task(snapshot,{'intent':'address bar','timeout_ms':1})
        self.assertEqual(payload['status'],'deadline')
        self.assertFalse(payload['actions'] or payload['exploration'])

    def test_substring_matches_original_binding_without_curated_aliases(self):
        snapshot=fixture()
        for intent, expected in [('address bar',False),('IDC_FOCUS_LOCATION',True),('CTRL+l',True)]:
            payload,_=context_for_task(snapshot,{'intent':intent,'backend':'substring'})
            self.assertEqual(bool(payload['exploration']),expected)

    def test_recipe_requires_artifact_verification_and_invalidates_config(self):
        from cu.recipes import recipe,record_validation,applicability
        snapshot=fixture();ids=[i for i,r in snapshot['records'].items() if r['record_type']=='action' and r['mode']==['normal']]
        condition={'kind':'window','address':'0x1'}
        r=recipe('focus location',ids,condition,condition,{'chromium':snapshot['source_manifest']['chromium']['fingerprint']},[{'action':'press_key','key':'CTRL+l'}])
        self.assertFalse(applicability(r,snapshot,evidence_for(snapshot).facts)['eligible'])
        with self.assertRaises(ValueError):record_validation(r,snapshot,'session:1','verified',False)
        record_validation(r,snapshot,'session:1','verified',True)
        self.assertTrue(applicability(r,snapshot,evidence_for(snapshot).facts)['eligible'])
        changed=copy.deepcopy(snapshot);changed['source_manifest']['chromium']['fingerprint']='changed'
        self.assertFalse(applicability(r,changed,evidence_for(snapshot).facts)['eligible'])
