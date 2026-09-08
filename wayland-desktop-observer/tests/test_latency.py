import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from cu import observation as m
from cu.events import Wakeup


def sample(value=0):
    return {'state': {'value':value,'visual':{'status':'not_requested'}}, 'rgb':None,
            'started_at':time.time(),'observed_at':time.time()}


class EventPipe:
    def __init__(self):
        self.reader,self.writer=os.pipe()
    def fileno(self): return self.reader
    def recv(self,n): return os.read(self.reader,n)
    def sendall(self,data): os.write(self.writer,data)
    def close_writer(self): os.close(self.writer)
    def close(self): os.close(self.reader)


class WakeupTests(unittest.TestCase):
    def test_request_interrupts_connected_event_wait(self):
        wake=Wakeup();events=EventPipe()
        wake.events=events
        self.addCleanup(wake.close);self.addCleanup(events.close_writer)
        timer=threading.Timer(.03,wake.notify);timer.start()
        start=time.monotonic()
        self.assertEqual(wake.wait(2),'request')
        self.assertLess(time.monotonic()-start,.5)
        timer.join()

    def test_partial_events_and_capture_lifecycle_do_not_wake(self):
        wake=Wakeup();events=EventPipe();wake.events=events
        self.addCleanup(wake.close)
        events.sendall(b'screencast>>1,monitor\nactivewin')
        self.assertEqual(wake.wait(.01),'deadline')
        events.sendall(b'dowv2>>0x1,Test\n')
        self.assertEqual(wake.wait(.1),'desktop')
        events.close_writer()
        self.assertEqual(wake.wait(.1),'disconnect')


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        class Collector:
            count=0
            def collect(self, window, channels=None):
                self.count+=1
                return sample(self.count)
        self.collector=Collector()
        self.patcher=patch.object(m.Observer,'connect_events');self.patcher.start()
        self.observer=m.Observer(self.collector,interval=5)
        self.addCleanup(self.patcher.stop);self.addCleanup(self.observer.close)

    def test_cached_read_does_not_claim_new_sample(self):
        first=json.loads(self.observer.observe(images=False)[0]['text'])
        second=json.loads(self.observer.observe(images=False,max_age_ms=5000)[0]['text'])
        self.assertTrue(second['freshness_satisfied'])
        self.assertFalse(second['fresh_sample'])
        self.assertEqual(first['revision'],second['revision'])

    def test_fresh_request_does_not_wait_for_poll_interval(self):
        self.observer.observe(images=False)
        # Drain the notification and allow the worker to enter its socket wait.
        time.sleep(.03)
        start=time.monotonic_ns()
        result=json.loads(self.observer.observe(images=False)[0]['text'])
        self.assertLess((time.monotonic_ns()-start)/1e9,.5)
        self.assertGreaterEqual(result['collection_started_ns'],start)

    def test_inflight_sample_started_before_request_is_not_fresh(self):
        entered=threading.Event();release=threading.Event()
        original=self.collector.collect
        def collect(window, channels=None):
            if not entered.is_set():
                entered.set();release.wait(2)
            return original(window,channels)
        self.collector.collect=collect
        with self.observer.cv:
            self.observer.scope=(None,('metadata',))
            self.observer.deadline=time.monotonic()+120
            self.observer.wakeup.notify()
        self.assertTrue(entered.wait(1))
        watermark=time.monotonic_ns()
        timer=threading.Timer(.03,release.set);timer.start()
        result=json.loads(self.observer.observe(images=False,after_action=watermark)[0]['text'])
        timer.join()
        self.assertGreaterEqual(result['collection_started_ns'],watermark)
        self.assertGreaterEqual(self.collector.count,2)

    def test_stop_discards_inflight_sample(self):
        entered=threading.Event();release=threading.Event()
        def collect(window,channels=None):
            entered.set();release.wait(2);return sample()
        self.collector.collect=collect
        with self.observer.cv:
            self.observer.scope=(None,('metadata',));self.observer.deadline=time.monotonic()+120
            self.observer.wakeup.notify()
        self.assertTrue(entered.wait(1))
        self.observer.stop();release.set();time.sleep(.03)
        self.assertFalse(self.observer.history.items)


class ConditionTests(unittest.TestCase):
    def test_unrelated_pixel_changes_do_not_satisfy_name_wait(self):
        class Collector:
            label='Waiting';count=0
            def collect(self,window,channels=None):
                self.count+=1
                s=sample(self.count)
                s['state']['accessibility']={'status':'available','nodes':[{'name':self.label,'role':'label','states':['showing']}]}
                return s
        collector=Collector()
        with patch.object(m.Observer,'connect_events'):
            observer=m.Observer(collector,interval=.01)
            try:
                result=json.loads(observer.observe('0x1',images=False,condition={'kind':'accessible','name':'Ready'},timeout_ms=50)[0]['text'])
                self.assertEqual(result['status'],'timeout')
                self.assertGreater(collector.count,1)
                collector.label='Ready'
                result=json.loads(observer.observe('0x1',images=False,condition={'kind':'accessible','name':'Ready'},timeout_ms=500)[0]['text'])
                self.assertTrue(result['condition_met'])
            finally:
                observer.close()

    def test_duplicate_and_protected_controls_are_not_unique_matches(self):
        s=sample();s['state']['accessibility']={'status':'available','nodes':[{'name':'Ready'},{'name':'Ready'}]}
        self.assertFalse(m.matches({'kind':'accessible','name':'Ready'},s))
        s['state']['accessibility']['nodes']=[{'name':'[protected]'}]
        self.assertFalse(m.matches({'kind':'accessible','name':'[protected]'},s))

    def test_region_wait_ignores_changes_outside_box_and_rejects_missing_baseline(self):
        old=sample();old['rgb']=bytes(12);old['state']['visual']={'geometry':[0,0,2,2]}
        new=sample();new['rgb']=b'\1'+bytes(11);new['state']['visual']={'geometry':[0,0,2,2]}
        self.assertFalse(m.matches({'kind':'region_changed','box':[1,1,2,2]},new,old))
        self.assertTrue(m.matches({'kind':'region_changed','box':[0,0,1,1]},new,old))
        with patch.object(m.Observer,'connect_events'):
            observer=m.Observer(collector=object())
            try:
                with self.assertRaisesRegex(ValueError,'baseline'):
                    observer.observe('0x1',condition={'kind':'region_changed','box':[0,0,1,1]})
            finally:
                observer.close()

    def test_window_condition_prefix_and_focus(self):
        windows = [{'address': '0xa', 'title': 'Untitled - Vault - Obsidian 1.13', 'class': 'md.obsidian.Obsidian', 'mapped': True},
                   {'address': '0xb', 'title': 'Untitled document', 'class': 'gedit', 'mapped': True}]
        state = {'windows': windows, 'active_window': '0xb'}
        sample = {'state': state}
        self.assertFalse(m.matches({'kind': 'window', 'title_prefix': 'Untitled'}, sample))  # two matches: not unique
        self.assertTrue(m.matches({'kind': 'window', 'title_prefix': 'Untitled -'}, sample))
        self.assertTrue(m.matches({'kind': 'window', 'class': 'md.obsidian.Obsidian', 'title_prefix': 'Untitled'}, sample))
        self.assertFalse(m.matches({'kind': 'window', 'class': 'md.obsidian.Obsidian', 'focused': True}, sample))
        self.assertTrue(m.matches({'kind': 'window', 'class': 'gedit', 'focused': True}, sample))
        m.validate_condition({'kind': 'window', 'title_prefix': 'x', 'focused': True})
        with self.assertRaises(ValueError):
            m.validate_condition({'kind': 'window', 'focused': True})
        with self.assertRaises(ValueError):
            m.validate_condition({'kind': 'window', 'class': 'x', 'focused': 'yes'})

    def test_nested_schema_rejects_invalid_or_executable_conditions(self):
        for args in ({'condition':{'kind':'eval','code':'anything'}},
                     {'condition':{'kind':'accessible','name':'Ready','value':float('nan')}},
                     {'condition':{'kind':'region_changed','box':[True,0,1,1]}},
                     {'condition':{'kind':'window'}},
                     {'condition':{'kind':'accessible','name':'Ready'},'channels':['pixels','pixels']}):
            with self.assertRaises(ValueError):
                m.validate('wait_for',args)


if __name__ == '__main__':unittest.main()
