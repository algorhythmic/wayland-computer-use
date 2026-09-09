import json
import time
import unittest
from unittest.mock import patch
import test_server
from test_server import server
from cu.capture import Capture


class ResultViewTests(unittest.TestCase):
    setUp=test_server.Tests.setUp
    def desktop(self):
        d,f,state,args=test_server.Tests.approval_desktop(self)
        state['active']='0x123'
        original=d.hypr
        d.hypr=lambda command: {} if command=='layers' else dict(f['target']) if command=='activewindow' else original(command)
        d.result_target=d.identity(f['target']);d.result_view={'kind':'target'}
        started=time.monotonic_ns()
        sample={'capture':Capture(800,600,bytes(800*600*3),started,started+1,'test'),
            'monitor':f['monitor'],'target':f['target'],'geometry':[0,0,800,600],
            'state':{'monitors':[f['monitor']]}}
        return d,f,sample

    def test_target_crop_and_region_keep_original_capture_provenance(self):
        d,f,sample=self.desktop()
        with patch.object(d,'observation_service') as observer,patch.object(d,'screenshot') as overview:
            observer.return_value.observe.return_value=([server.text_content({})],sample)
            for policy,size,origin in (({'kind':'target'},[800,600],[0,0]),({'kind':'region','box':[10,20,210,120]},[200,100],[10,20])):
                d.result_view=policy
                content=d.result_capture('DP-1');meta=json.loads(content[0]['text'])
                self.assertEqual([meta['width'],meta['height']],size)
                self.assertEqual(meta['origin'],origin)
                self.assertEqual(meta['capture_started_ns'],sample['capture'].started_ns)
                self.assertEqual(content[1]['_meta']['codex/imageDetail'],'original')
                frame=d.frames[meta['frame_id']]
                self.assertEqual(d.point(frame,0,0),tuple(origin))
            overview.assert_not_called()

    def test_overlay_or_changed_target_requires_overview(self):
        for cause in ('overlay','focus'):
            d,f,sample=self.desktop();original=d.hypr
            d.hypr=lambda command: {'DP-1':{'levels':{'3':[{'namespace':'dialog'}]}}} if cause=='overlay' and command=='layers' else {'address':'0x777'} if cause=='focus' and command=='activewindow' else original(command)
            with patch.object(d,'screenshot',return_value=[server.text_content({'frame_id':'overview'})]) as shot:
                content=d.result_capture('DP-1')
            shot.assert_called_once()
            self.assertEqual(json.loads(content[0]['text'])['result_view']['kind'],'monitor')
