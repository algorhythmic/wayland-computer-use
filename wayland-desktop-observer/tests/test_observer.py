import base64
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from cu import observation as m
from cu.capture import Capture
OBSERVER_ENTRY = Path(__file__).resolve().parents[1]/'scripts/observer_server.py'


def sample(rgb=None, value=0, width=128, height=128):
    return {'state': {'value': value, 'visual':
                     {'status': 'available', 'geometry': [10, 20, width, height],
                      'sha256': hashlib.sha256(rgb).hexdigest()} if rgb is not None else {'status': 'not_requested'}},
            'rgb': rgb, 'started_at': time.time(), 'observed_at': time.time()}


def body(response):
    return json.loads(response[0]['text'])


class HistoryTests(unittest.TestCase):
    def test_unchanged_sample_keeps_revision_and_omits_image(self):
        h = m.History()
        s = sample(bytes(128*128*3))
        rev = h.add(s)['revision']
        h.add(copy.deepcopy(s))
        result = h.response(rev)
        self.assertEqual(len(result), 1)
        self.assertEqual(body(result)['changes'], {})
        self.assertEqual(body(result)['revision'], rev)

    def test_exact_pixel_change_returns_correct_crop_and_origin(self):
        h = m.History()
        rgb = bytes(128*128*3)
        rev = h.add(sample(rgb))['revision']
        updated = bytearray(rgb)
        updated[(70*128+80)*3] = 1  # low contrast must not be silently suppressed
        h.add(sample(bytes(updated)))
        result = h.response(rev)
        meta = body(result)['images'][0]
        self.assertEqual(meta['box_in_window_crop'], [64, 64, 128, 128])
        self.assertEqual(meta['desktop_origin'], [74, 84])
        self.assertEqual(meta['kind'], 'changed_region')
        png = base64.b64decode(result[1]['data'])
        decoded = m.command(['magick', 'png:-', '-depth', '8', 'rgb:-'], png)
        self.assertEqual(decoded, m.crop(bytes(updated), 128, [64,64,128,128]))

    def test_evicted_or_foreign_revision_returns_full_snapshot(self):
        h = m.History(capacity=2)
        rev = h.add(sample(value=0))['revision']
        h.add(sample(value=1));h.add(sample(value=2))
        for cursor in (rev, 'another:1'):
            b = body(h.response(cursor))
            self.assertEqual(b['mode'], 'snapshot')
            self.assertEqual(b['state']['value'], 2)
            self.assertEqual(b['reset_reason'], 'unknown_or_evicted_revision')

    def test_delta_is_from_requested_revision_not_last_sample(self):
        h = m.History()
        rev = h.add(sample(value=0))['revision']
        h.add(sample(value=1));h.add(sample(value=2))
        self.assertEqual(body(h.response(rev))['changes']['value'], {'before':0,'after':2})

    def test_geometry_change_forces_overview(self):
        h = m.History();s = sample(bytes(128*128*3))
        rev = h.add(s)['revision']
        s = copy.deepcopy(s);s['state']['visual']['geometry'][0] = 42
        h.add(s)
        self.assertEqual(body(h.response(rev))['images'][0]['kind'], 'overview')

    def test_backend_failure_never_reuses_image(self):
        h = m.History();rev = h.add(sample(bytes(128*128*3)))['revision']
        failed = sample();failed['state'] = {'error':'disconnected'};h.add(failed)
        result = h.response(rev)
        self.assertEqual(len(result), 1)
        self.assertIsNone(body(result)['changes']['visual']['after'])

    def test_images_false_and_memory_bounds(self):
        h = m.History(capacity=2)
        for i in range(80):h.add(sample(bytes(128*128*3), value=i))
        self.assertEqual(len(h.items), 2)
        self.assertEqual(len(h.samples), 60)
        self.assertEqual(len(h.response(images=False)), 1)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(m, 'desktop_locked', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        with patch.object(m.baseline, 'session_env'):
            self.c = m.Collector()
        self.addCleanup(self.c.close)
        self.window = {'address':'0xa','pid':123,'class':'Test','title':'Test',
                       'at':[0,0], 'size':[100,100], 'workspace':{'id':1}, 'monitor':0, 'mapped':True}
        self.monitor = {'id':0,'name':'DP-1','x':0,'y':0,'width':200,'height':200,'scale':1,'transform':0}
        self.c.hypr = lambda name: {'clients':[self.window], 'monitors':[self.monitor],
                                   'activewindow':self.window}[name]

    def test_desktop_metadata_does_not_capture_or_probe(self):
        with patch.object(m, 'command', side_effect=AssertionError('unexpected capture')):
            s = self.c.collect(None)
        self.assertIsNone(s['rgb'])
        self.assertEqual(s['state']['accessibility']['status'], 'not_requested')

    def test_channels_skip_capture_or_accessibility_work(self):
        with patch.object(self.c.capturer,'capture',side_effect=AssertionError('pixels requested')) as capture, \
                patch.object(self.c.accessibility,'probe',return_value={'status':'available','nodes':[]}) as probe, \
                patch.object(m.subprocess,'run') as run:
            run.return_value.returncode=1
            s=self.c.collect('0xa',channels=['metadata','accessibility'])
            self.assertIsNone(s['rgb']);capture.assert_not_called();probe.assert_called_once()
            probe.reset_mock()
            self.c.collect('0xa',channels=['metadata'])
            probe.assert_not_called();capture.assert_not_called()

    def test_locked_desktop_reports_reason_without_capture(self):
        with patch.object(m, 'desktop_locked', return_value=True), \
                patch.object(self.c.capturer, 'capture', side_effect=AssertionError('captured while locked')):
            s = self.c.collect('0xa')
        self.assertEqual(s['state']['visual']['reason'], 'desktop_locked')
        self.assertIn('lock_check_ms', s['timings_ms'])

    def test_unfocused_or_missing_window_does_not_capture(self):
        for address, reason in [('0xa','target_not_focused'),('0xb','window_missing')]:
            self.c.hypr = lambda name: {'clients':[self.window], 'monitors':[self.monitor], 'activewindow':{}}[name]
            with patch.object(m, 'command', side_effect=AssertionError('unexpected capture')):
                s = self.c.collect(address)
            self.assertEqual(s['state']['visual']['reason'], reason)
            self.assertIsNone(s['rgb'])

    def test_accessibility_timeout_preserves_valid_visual(self):
        capture = Capture(100,100,bytes(30000),1,2,'test')
        with patch.object(self.c.capturer, 'capture', return_value=capture), \
                patch.object(self.c.accessibility, 'probe', return_value={'status':'unavailable','reason':'probe_timeout'}), \
                patch.object(m.subprocess,'run') as run:
            run.return_value.returncode = 1
            s = self.c.collect('0xa')
        self.assertEqual(s['state']['accessibility']['reason'], 'probe_timeout')
        self.assertEqual(s['state']['visual']['status'], 'available')

    def test_focus_changed_during_capture_discards_evidence(self):
        calls = 0
        def hypr(name):
            nonlocal calls
            if name == 'activewindow':
                calls += 1
                return self.window if calls == 1 else {}
            return {'clients':[self.window], 'monitors':[self.monitor]}[name]
        self.c.hypr = hypr
        with patch.object(self.c.capturer, 'capture', return_value=Capture(100,100,bytes(30000),1,2,'test')), \
                patch.object(self.c.accessibility,'probe',return_value={'status':'unavailable'}), \
                patch.object(m.subprocess,'run') as run:
            run.return_value.returncode=1;s=self.c.collect('0xa')
        self.assertIsNone(s['rgb'])
        self.assertIsNone(s['capture'])
        self.assertEqual(s['state']['visual']['reason'], 'desktop_changed_during_collection')

    def test_window_identity_survives_title_change_not_pid_reuse(self):
        one=self.c.collect(None)['state']['windows'][0]['id']
        self.window['title']='Renamed'
        self.assertEqual(one,self.c.collect(None)['state']['windows'][0]['id'])
        self.window['pid']=456
        self.assertNotEqual(one,self.c.collect(None)['state']['windows'][0]['id'])

    def test_scaled_rotated_negative_origin_clipping(self):
        w=dict(self.window,at=[-100,0],size=[200,500])
        monitor=dict(self.monitor,x=-100,width=800,height=600,scale=2,transform=1)
        self.assertEqual(m.capture_geometry(w,monitor),[-100,0,200,400])


class WorkerTests(unittest.TestCase):
    def setUp(self):
        class Fake:
            value=0
            def collect(self, window, channels=None):return sample(value=(window,self.value))
        self.fake=Fake()
        self.p=patch.object(m.Observer,'connect_events');self.p.start()
        self.o=m.Observer(self.fake,interval=.01)

    def tearDown(self):
        self.o.close();self.p.stop()

    def test_wait_timeout_and_change(self):
        rev=body(self.o.observe(images=False))['revision']
        b=body(self.o.observe(since_revision=rev,images=False,wait=True,timeout_ms=40))
        self.assertTrue(b['wait_timed_out'])
        self.fake.value=1
        b=body(self.o.observe(since_revision=rev,images=False,wait=True,timeout_ms=500))
        self.assertFalse(b['wait_timed_out'])
        self.assertNotEqual(rev,b['revision'])

    def test_scope_switch_resets_and_stop_clears(self):
        rev=body(self.o.observe('0xa',images=False))['revision']
        b=body(self.o.observe('0xb',since_revision=rev,images=False))
        self.assertEqual(b['mode'],'snapshot')
        self.assertEqual(b['state']['value'][0],'0xb')
        self.o.stop()
        self.assertEqual(len(self.o.history.items),0)

    def test_lease_expiry_stops_collection(self):
        self.o.observe(images=False)
        with self.o.cv:self.o.deadline=0
        time.sleep(.05)
        count=self.o.sample_count
        time.sleep(.05)
        self.assertEqual(count,self.o.sample_count)


class ProtocolTests(unittest.TestCase):
    def test_strict_validation_and_no_input_tools(self):
        for name,args in [('pointer',{}),('observe',{'images':'false'}),
                          ('observe',{'frame_id':'x'}),('wait_for_change',{}),
                          ('wait_for_change',{'since_revision':'x','timeout_ms':True}),
                          ('wait_for_change',{'since_revision':'x','timeout_ms':30001})]:
            with self.assertRaises(ValueError):m.validate(name,args)
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in m.TOOLS))

    def test_stdio_discovery_and_rejected_input(self):
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize'},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'pointer','arguments':{}}}]
        out=subprocess.run([sys.executable,str(OBSERVER_ENTRY)],
                           input='\n'.join(map(json.dumps,requests))+'\n',text=True,capture_output=True,timeout=5,check=True)
        responses=[json.loads(l) for l in out.stdout.splitlines()]
        self.assertEqual([r['id'] for r in responses],[1,2,3])
        self.assertEqual(len(responses[1]['result']['tools']),4)
        self.assertTrue(responses[2]['result']['isError'])


if __name__=='__main__':unittest.main()
