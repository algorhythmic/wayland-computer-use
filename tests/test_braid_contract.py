"""Run real subprocess conformance with WCU_BRAID_TEST_BIN set to a pinned build."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from cu.braid_client import BraidBackend, BraidError, Connection, POLICY
from cu.context_records import atomic_json, normalize
from test_context_contract import fixture


class TransportTests(unittest.TestCase):
    def connection(self, program, cap=4096):
        c=Connection([sys.executable,'-u','-c',program],cap)
        c.process=__import__('subprocess').Popen(c.command,stdin=__import__('subprocess').PIPE,stdout=__import__('subprocess').PIPE,stderr=__import__('subprocess').DEVNULL,bufsize=0)
        os.set_blocking(c.process.stdin.fileno(),False);os.set_blocking(c.process.stdout.fileno(),False)
        self.addCleanup(c.close)
        return c

    def test_oversized_mismatched_malformed_and_duplicate_replies_stop_process(self):
        replies=['x'*5000, json.dumps({'protocol_version':1,'id':'other','result':{}}), 'not json',
                 '{"protocol_version":1,"id":"wcu-1","id":"wcu-1","result":{}}']
        for reply in replies:
            c=self.connection('import sys,time;sys.stdin.readline();print('+repr(reply)+',flush=True);time.sleep(5)')
            with self.assertRaises(BraidError):c.request('dataset',time.monotonic()+1)
            self.assertIsNone(c.process)

    def test_deadline_and_missing_binary_leave_no_partial_context(self):
        c=self.connection('import sys,time;sys.stdin.readline();time.sleep(5)')
        start=time.monotonic()
        with self.assertRaisesRegex(BraidError,'deadline'):c.request('dataset',start+.05)
        self.assertLess(time.monotonic()-start,.5)
        self.assertIsNone(c.process)
        with tempfile.TemporaryDirectory() as folder:
            b=BraidBackend('/nonexistent-braid',folder,'x')
            with self.assertRaises(OSError):b.rank(fixture(),'address bar',[],time.monotonic()+1)


@unittest.skipUnless(os.environ.get('WCU_BRAID_TEST_BIN'),'real Braid build is opt-in')
class RealBraidTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        root=Path(self.folder.name);self.dataset='wcu-conformance'
        self.config=root/'config.json'
        atomic_json(self.config,{'version':1,'dataset_id':self.dataset,'store':{'backend':'sqlite','path':str(root/'test.db')},
            'embed':{'provider':'none'},'candidate_limit':3,'defaults':{**POLICY,'budget':{'max':10000}}})
        self.c=Connection([os.environ['WCU_BRAID_TEST_BIN'],'serve','--stdio','--config',str(self.config)])
        self.addCleanup(self.c.close);self.end=time.monotonic()+10
        self.hello=self.c.start(self.end)
        self.rev=0

    def request(self,method,**args):return self.c.request(method,self.end,**args)
    def publish(self,nodes):
        result=self.request('replace_snapshot',snapshot={'dataset_id':self.dataset,'expected_revision':self.rev,
            'batch':{'nodes':[{'id':i,'type':'action','text':text,'ts':'2026-01-01T00:00:00Z','cost':1,'attrs':attrs} for i,text,attrs in nodes],'edges':[]}})
        self.rev=result['revision'];return result
    def query(self,**kwargs):
        return self.request('query',query={'dataset_id':self.dataset,'expected_revision':self.rev,'text':'address bar',
            'weights':POLICY['weights'],**kwargs})
    @staticmethod
    def ids(result):return [n['id'] for n in result.get('items') or []]

    def test_filter_before_limit_and_absent_null_empty_ids(self):
        self.publish([(f'forbidden:{i}','address bar address bar',{}) for i in range(40)]+[('allowed','address bar',{})])
        self.assertEqual(self.ids(self.query(filters={'allowed_ids':['allowed']})),['allowed'])
        self.assertEqual(self.ids(self.query(filters={'allowed_ids':[]})),[])
        self.assertTrue(self.ids(self.query(filters={'allowed_ids':None})))
        self.assertTrue(self.ids(self.query()))

    def test_predicates_do_not_coerce_missing_or_wrong_types(self):
        self.publish([('bool','address bar',{'enabled':True}),('number','address bar',{'enabled':1}),
                      ('string','address bar',{'enabled':'true'}),('missing','address bar',{})])
        self.assertEqual(self.ids(self.query(filters={'predicate':{'op':'eq','path':'enabled','value':True}})),['bool'])
        self.assertEqual(self.ids(self.query(filters={'predicate':{'op':'eq','path':'enabled','value':None}})),[])

    def test_exact_case_punctuation_wrong_dataset_and_revision_change(self):
        self.publish([(i,'search',{'shortcut':i}) for i in ('n','N','?','<C-w>v')])
        get={'dataset_id':self.dataset,'expected_revision':self.rev,'ids':['N','n','?','<C-w>v','n','missing']}
        result=self.request('get_many',get=get)
        self.assertEqual([n['id'] for n in result['nodes']],['N','n','?','<C-w>v'])
        self.assertEqual(result['missing_ids'],['missing'])
        with self.assertRaisesRegex(BraidError,'dataset_mismatch'):self.request('get_many',get={**get,'dataset_id':'other'})
        self.query(text='search')
        self.publish([('n','new search',{})])
        with self.assertRaisesRegex(BraidError,'revision_conflict'):self.request('get_many',get=get)

    def test_required_missing_forbidden_shared_and_mandatory_budget(self):
        self.publish([('a','address bar',{}),('b','address bar',{}),('context','prefix',{})])
        for dep in ('missing','context'):
            result=self.query(filters={'allowed_ids':['a']},requires={'a':[dep]})
            self.assertEqual(self.ids(result),[])
            self.assertEqual(result['abstention_reason'],'required_context_unavailable')
        result=self.query(requires={'a':['context'],'b':['context']})
        self.assertEqual(set(self.ids(result)),{'a','b','context'})
        self.assertEqual(self.ids(result).count('context'),1)
        result=self.query(mandatory_ids=['context'],budget={'max':0})
        self.assertEqual(result['abstention_reason'],'infeasible_budget')

    def test_export_accounts_for_metadata_and_complete_context(self):
        self.publish([('a','address bar',{'heavy':'é'*1000}),('context','prefix',{})])
        result=self.request('export_context',export={'query':{'dataset_id':self.dataset,'expected_revision':self.rev,
            'text':'address bar','weights':POLICY['weights'],'requires':{'a':['context']}},
            'output':{'fields':['id','text','attrs'],'max_bytes':300}})
        self.assertLessEqual(result['context_bytes'],300)
        self.assertEqual(result['context'],[])

    def test_backend_snapshot_mapping_empty_filter_and_binary_pin(self):
        exe=os.environ['WCU_BRAID_TEST_BIN'];fingerprint=hashlib.sha256(Path(exe).read_bytes()).hexdigest()
        backend=BraidBackend(exe,Path(self.folder.name)/'adapter',fingerprint);self.addCleanup(backend.close)
        snapshot=fixture()
        ids,info=backend.rank(snapshot,'address bar',[],self.end)
        self.assertEqual(ids,[]);self.assertEqual(info['binary_sha256'],fingerprint)
        allowed=[i for i,r in snapshot['records'].items() if r['record_type']=='action']
        ids,info=backend.rank(snapshot,'address bar',allowed,self.end)
        self.assertEqual(set(ids),set(allowed))
        backend.expected_sha256='wrong'
        with self.assertRaisesRegex(BraidError,'binary_identity_mismatch'):backend.rank(snapshot,'address bar',allowed,self.end)

    def test_restart_reconciles_large_catalog_with_bounded_exact_reads(self):
        snapshot=normalize({'fixture':{'bindings':[{'command':str(i),'shortcut':'F1','annotation':'x'*32000} for i in range(160)]}})
        exe=os.environ['WCU_BRAID_TEST_BIN'];fingerprint=hashlib.sha256(Path(exe).read_bytes()).hexdigest()
        backend=BraidBackend(exe,Path(self.folder.name)/'large-adapter',fingerprint);self.addCleanup(backend.close)
        self.end=time.monotonic()+20
        backend.rank(snapshot,'fixture',[],self.end)
        backend.close()
        ids,info=backend.rank(snapshot,'fixture',[],self.end)
        self.assertEqual(ids,[])
        self.assertEqual(info['backend'],'braid')
