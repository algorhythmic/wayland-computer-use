import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from cu.capture import Capturer
from cu.pixels import parse_ppm, png_rgb, crop


MONITOR = {'name':'DP-1','x':0,'y':0,'width':2,'height':1,'scale':1,'transform':0}


class PixelTests(unittest.TestCase):
    def test_ppm_preserves_header_like_first_pixel_bytes(self):
        rgb = b'\n #\xff\0\t'
        self.assertEqual(parse_ppm(b'P6\n# comment\n2 1\n255\n'+rgb), (2,1,rgb))
        for bad in (b'P3\n1 1\n255\nabc', b'P6\n0 1\n255\n',
                    b'P6\n1 1\n65535\nabcdef', b'P6\n1 1\n255\nab',
                    b'P6\n100000 100000\n255\n', b'P6\n#unterminated'):
            with self.assertRaises(ValueError):
                parse_ppm(bad)

    def test_png_roundtrip_preserves_rgb(self):
        rgb = bytes(range(255))*3
        png = png_rgb(85,3,rgb)
        decoded = subprocess.check_output(['magick','png:-','-alpha','off','-depth','8','rgb:-'], input=png)
        self.assertEqual(decoded, rgb)
        self.assertEqual(crop(rgb,85,[0,0,1,1]),rgb[:3])
        with self.assertRaises(ValueError):
            crop(rgb,85,[-1,0,1,1])

    def test_raw_fallback_is_single_command_and_validates_region(self):
        commands=[]
        def command(args, **kwargs):
            commands.append(args)
            return b'P6\n2 1\n255\nabcdef'
        capturer=Capturer(command=command, helper='/nonexistent')
        shot=capturer.capture(MONITOR)
        self.assertEqual((shot.rgb,shot.backend),(b'abcdef','grim-ppm'))
        self.assertEqual(commands[0][-3:],['-t','ppm','-'])
        with self.assertRaisesRegex(ValueError,'dimensions'):
            capturer.capture(MONITOR,[0,0,1,1])
        self.assertIn('-g',commands[-1])
        self.assertNotIn('-o',commands[-1])

    def test_helper_is_reused_and_scaled_outputs_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            helper=Path(folder)/'helper'
            helper.write_text('''#!/usr/bin/python3
import sys,json
for line in sys.stdin:
    sys.stdout.buffer.write(json.dumps({'width':2,'height':1,'damage':[0,0,2,1]}).encode()+b'\\nabcdef')
    sys.stdout.buffer.flush()
''')
            helper.chmod(0o700)
            capturer=Capturer(helper=helper,command=lambda *a,**kw:b'P6\n2 1\n255\nabcdef')
            self.addCleanup(capturer.close)
            self.assertEqual(capturer.capture(MONITOR).backend,'wlr-screencopy')
            pid=capturer.process.pid
            self.assertEqual(capturer.capture(MONITOR).rgb,b'abcdef')
            self.assertEqual(capturer.process.pid,pid)
            self.assertEqual(capturer.capture(dict(MONITOR,scale=2)).backend,'grim-ppm')

    def test_failed_helper_is_killed_and_fallback_reports_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            helper=Path(folder)/'helper'
            helper.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(10)\n')
            helper.chmod(0o700)
            capturer=Capturer(helper=helper,command=lambda *a,**kw:b'P6\n2 1\n255\nabcdef')
            shot=capturer.capture(MONITOR,timeout=.05)
            self.assertEqual(shot.backend,'grim-ppm')
            self.assertIn('timeout',shot.fallback_reason)
            self.assertIsNone(capturer.process)


    def test_fallback_reason_codes_and_lock_wait(self):
        command=lambda *a,**kw:b'P6\n2 1\n255\nabcdef'
        shot=Capturer(command=command, helper='/nonexistent').capture(MONITOR)
        self.assertEqual(shot.fallback_reason,'helper_unavailable')
        self.assertLessEqual(shot.requested_ns, shot.started_ns)
        with tempfile.TemporaryDirectory() as folder:
            helper=Path(folder)/'helper'
            helper.write_text('#!/bin/sh\nexit 1\n')
            helper.chmod(0o700)
            capturer=Capturer(command=command, helper=helper)
            self.addCleanup(capturer.close)
            self.assertEqual(capturer.capture(dict(MONITOR,scale=2)).fallback_reason,'unsupported_output')
            self.assertTrue(capturer.capture(MONITOR).fallback_reason.startswith('helper_failed: '))
            self.assertTrue(capturer.capture(MONITOR).fallback_reason.startswith('helper_cooldown: '))


if __name__ == '__main__':
    unittest.main()
