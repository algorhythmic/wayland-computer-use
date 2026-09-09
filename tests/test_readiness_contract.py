import copy
import sys
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from cu import observation as obs
from cu.accessibility_worker import probe_tree


class Node:
    def __init__(self, name='', role='label', states=(), children=(), text=None):
        self.name, self.role, self.states, self.children, self.text = name, role, states, children, text
    def get_name(self): return self.name
    def get_role_name(self): return self.role
    def get_role(self): return self.role
    def get_state_set(self): return SimpleNamespace(contains=lambda state: state in self.states)
    def get_child_count(self): return len(self.children)
    def get_child_at_index(self, index): return self.children[index]
    def get_component_iface(self): return None
    def get_value_iface(self): return None
    def get_text_iface(self):
        return None if self.text is None else self
    def get_character_count(self): return len(self.text)
    def get_text(self): raise AssertionError('Accessible.get_text is the legacy interface accessor')


AT = SimpleNamespace(Role=SimpleNamespace(PASSWORD_TEXT='password'),
    Text=SimpleNamespace(get_text=lambda obj,a,b:obj.text[a:b]),
    StateType=SimpleNamespace(**{s:s for s in ('VISIBLE','SHOWING','ENABLED','SENSITIVE','FOCUSED','CHECKED','SELECTED','EDITABLE')}))


class ReadinessTests(unittest.TestCase):
    def test_scoped_tree_focus_after_tab_and_partial_coverage(self):
        controls = [Node('first', states=['FOCUSED']), Node('second')]
        panel = Node('panel', role='panel', children=controls)
        root = Node('window', children=[panel, Node('huge', children=[Node() for _ in range(250)])])
        self.assertEqual(probe_tree(root, AT)['status'], 'partial')
        selector = {'ref':'root/0','name':'panel','role':'panel'}
        before = probe_tree(root, AT, selector)
        self.assertTrue(before['complete'])
        self.assertEqual(before['focused_control']['name'], 'first')
        controls[0].states = []
        controls[1].states = ['FOCUSED']
        self.assertEqual(probe_tree(root, AT, selector)['focused_control']['name'], 'second')
        controls[0].states = ['FOCUSED']
        self.assertEqual(probe_tree(root, AT, selector)['focus_status'], 'ambiguous')

    def test_absence_requires_complete_scope_and_all_states(self):
        sample = {'state': {'accessibility': {'status':'partial','nodes':[]}}}
        condition = {'kind':'accessible_absent','name':'Loading'}
        self.assertFalse(obs.matches(condition,sample))
        sample['state']['accessibility']['status']='available'
        self.assertTrue(obs.matches(condition,sample))
        sample['state']['accessibility']['nodes']=[{'name':'Ready','states':['visible','enabled']}]
        condition={'kind':'accessible','name':'Ready','states':['visible','enabled']}
        self.assertTrue(obs.matches(condition,sample))
        sample['state']['accessibility']['nodes'][0]['states']=['visible']
        self.assertFalse(obs.matches(condition,sample))

    def test_protected_text_is_unverifiable_and_readback_is_bounded(self):
        root=Node('window',children=[Node('secret',role='password',text='TOP SECRET'),Node('field',role='entry',text='hello')])
        protected=probe_tree(root,AT,read_text={'ref':'root/0','name':'[protected]','role':'password'})
        self.assertEqual(protected['nodes'][1]['name'],'[protected]')
        self.assertEqual(protected['text_readback'],{'status':'protected','verifiable':False})
        short=probe_tree(root,AT,read_text={'ref':'root/1','name':'field','role':'entry','max_chars':3})
        self.assertEqual(short['text_readback']['text'],'hel')
        self.assertFalse(short['text_readback']['verifiable'])
        full=probe_tree(root,AT,read_text={'ref':'root/1','name':'field','role':'entry'})
        self.assertTrue(obs.matches({'kind':'text_equals','text':'hello'},{'state':{'accessibility':full}}))

    def test_baseline_geometry_change_and_region_stability(self):
        base={'target':{'id':'same','size':[2,2]},'state':{'visual':{'geometry':[0,0,2,2]}},'rgb':bytes(12),
              'started_ns':1_000_000_000,'completed_ns':1_010_000_000}
        later=copy.deepcopy(base);later['target']['size']=[3,2]
        self.assertTrue(obs.matches({'kind':'window_changed'},later,base))
        later['target']['id']='different'
        self.assertFalse(obs.matches({'kind':'window_changed'},later,base))
        later=copy.deepcopy(base);later['started_ns']+=300_000_000
        self.assertTrue(obs.matches({'kind':'region_stable','box':[0,0,2,2],'stable_ms':100},later,base))
        later['rgb']=b'1'+bytes(11)
        self.assertFalse(obs.matches({'kind':'region_stable','box':[0,0,2,2],'stable_ms':100},later,base))

    def test_non_settling_region_times_out(self):
        class Collector:
            counter=0
            def collect(self,window,channels=None,**kwargs):
                self.counter+=1
                return {'state':{'visual':{'geometry':[0,0,1,1], 'sha256':str(self.counter)}},'rgb':bytes([self.counter%255,0,0]),
                        'started_at':time.time(),'observed_at':time.time()}
        with patch.object(obs.Observer,'connect_events'):
            observer=obs.Observer(Collector(),interval=.01)
            try:
                result=observer.observe('0x1',images=False,condition={'kind':'region_stable','box':[0,0,1,1],'stable_ms':100},timeout_ms=160)
                import json
                self.assertEqual(json.loads(result[0]['text'])['status'],'timeout')
            finally:observer.close()
