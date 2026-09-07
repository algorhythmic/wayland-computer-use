import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts" / "server.py"
spec = importlib.util.spec_from_file_location("wayland_server", PATH)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def frame(monitor=None):
    monitor = monitor or {"name": "DP-1", "x": 0, "y": 0, "width": 2560,
                          "height": 1440, "scale": 1, "transform": 0}
    return {"monitor": monitor, "width": 2560, "height": 1440,
            "active": "0x123", "time": time.monotonic(),
            "layout": server.Desktop.layout([monitor])}


class Tests(unittest.TestCase):
    def approval_desktop(self):
        d = server.Desktop()
        f = frame()
        target = {"address": "0x123", "pid": 42, "class": "Test", "initialClass": "Test",
                  "title": "Test page", "at": [0, 0], "size": [800, 600],
                  "monitor": 1, "workspace": {"id": 1}, "mapped": True}
        f.update(target=target, visual=(784, 584, bytes(784*584*3)))
        d.frames["token"] = f
        state = {"active": "0x456"}
        d.hypr = lambda cmd: ([f["monitor"]] if cmd == "monitors" else
                             [dict(target)] if cmd == "clients" else {"address": state["active"]})
        d.dispatch = lambda *args: state.update(active="0x123")
        args = {"frame_id": "token", "restore_focus": True, "target_window": "0x123",
                "target_title": "Test page", "x": 100, "y": 100, "steps": -1}
        return d, f, state, args

    def test_approval_restores_exact_target_then_validates(self):
        d, f, state, args = self.approval_desktop()
        with patch.object(server.subprocess, "run") as process, patch.object(server.time, "sleep"):
            process.return_value.returncode = 1
            self.assertIs(d.prepare("scroll", args), f)
            self.assertEqual(state["active"], "0x123")

    def test_approval_target_mismatch_never_focuses(self):
        d, f, state, args = self.approval_desktop()
        args["target_title"] = "Other app"
        with self.assertRaisesRegex(ValueError, "must match"):
            d.prepare("scroll", args)
        self.assertEqual(state["active"], "0x456")

    def test_approval_geometry_change_rejected_before_focus(self):
        d, f, state, args = self.approval_desktop()
        original = d.hypr
        d.hypr = lambda cmd: [dict(f["target"], at=[10, 20])] if cmd == "clients" else original(cmd)
        with patch.object(server.subprocess, "run") as process, patch.object(d, "screenshot", return_value=[]):
            process.return_value.returncode = 1
            with self.assertRaises(server.ActionRejected) as error:
                d.prepare("scroll", args)
            self.assertFalse(json.loads(error.exception.content[0]["text"])["action_performed"])
        self.assertEqual(state["active"], "0x456")

    def test_expired_approval_returns_review_without_input(self):
        d, f, state, args = self.approval_desktop()
        f["time"] -= 121
        with patch.object(server.subprocess, "run") as process, patch.object(d, "screenshot", return_value=[]), \
                patch.object(server.time, "sleep"):
            process.return_value.returncode = 1
            with self.assertRaisesRegex(server.ActionRejected, "expired"):
                d.prepare("scroll", args)
        self.assertFalse(d.frames)

    def test_restored_click_rejects_high_contrast_change(self):
        d, f, state, args = self.approval_desktop()
        changed = bytearray(f["visual"][2])
        changed[0] = 7
        with patch.object(server.subprocess, "run") as process, patch.object(d, "screenshot", return_value=[]), \
                patch.object(server.time, "sleep"), patch.object(d, "target_pixels", return_value=(784, 584, bytes(changed))):
            process.return_value.returncode = 1
            with self.assertRaisesRegex(server.ActionRejected, "pixels changed"):
                d.prepare("pointer", args)

    def test_guard_noise_boundaries_and_local_protection(self):
        before = (100, 100, bytes(30000))
        def compare(count, delta, region=(40, 40, 60, 60), start=0):
            rgb = bytearray(30000)
            for pixel in range(start, start+count):
                rgb[pixel*3] = delta
            return server.visual_guard(before, (100, 100, bytes(rgb)), region)["accepted"]
        self.assertTrue(compare(200, 6))
        self.assertFalse(compare(201, 1))
        self.assertFalse(compare(1, 7))
        self.assertTrue(compare(1, 2, start=4040))
        self.assertFalse(compare(1, 3, start=4040))
        self.assertFalse(compare(9, 1, start=4040))  # >2% of local 400 pixels
        self.assertFalse(compare(1, 3, region=None))  # unknown keyboard focus
        self.assertFalse(server.visual_guard(before, (1, 1, bytes(3)))["accepted"])
        with self.assertRaises(ValueError):
            server.visual_guard(before, before, (-1, 0, 10, 10))

    def test_action_region_mapping_and_drag_corridor(self):
        d, f, state, args = self.approval_desktop()
        self.assertEqual(d.action_region(f, args), (60, 60, 125, 125))
        self.assertEqual(d.action_region(f, dict(args, end_x=200, end_y=300)),
                         (60, 60, 225, 325))
        with self.assertRaises(ValueError):
            d.action_region(f, dict(args, x=4))
        f["monitor"].update(x=-100, scale=2, width=2880, height=5120, transform=1)
        f["target"]["at"] = [-100, 0]
        self.assertEqual(d.action_region(f, args), (60, 60, 125, 125))

    def test_prepare_allows_sparse_background_noise_but_rechecks_focus(self):
        d, f, state, args = self.approval_desktop()
        rgb = bytearray(f["visual"][2])
        rgb[0] = 6
        with patch.object(server.subprocess, "run") as process, patch.object(server.time, "sleep"), \
                patch.object(d, "target_pixels", return_value=(784, 584, bytes(rgb))):
            process.return_value.returncode = 1
            self.assertIs(d.prepare("pointer", args), f)
            self.assertEqual(state["active"], "0x123")
            def steal_focus(*unused, **kwargs):
                state["active"] = "0x456"
                return (784, 584, bytes(rgb))
            with patch.object(d, "target_pixels", side_effect=steal_focus), \
                    patch.object(d, "screenshot", return_value=[]):
                with self.assertRaises(server.ActionRejected):
                    d.prepare("pointer", args)

    def test_pixel_metrics_include_tiny_changes_and_exclusive_bbox(self):
        before = (3, 2, bytes(18))
        after = bytearray(18)
        after[3] = 3
        after[17] = 6
        result = server.pixel_difference(before, (3, 2, bytes(after)))
        self.assertEqual(result["changed_pixels"], 2)
        self.assertEqual(result["max_channel_difference"], 6)
        self.assertEqual(result["changed_bbox_xyxy"], [1, 0, 3, 2])
        self.assertIsNone(server.pixel_difference(before, before)["changed_bbox_xyxy"])

    def test_debug_storage_is_opt_in_and_private(self):
        crop = (1, 1, bytes(3))
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(server.save_visual_debug(crop, crop, {}))
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"WAYLAND_CU_DEBUG_DIR": root}):
            folder = Path(server.save_visual_debug(crop, crop, {"changed_pixels": 0}))
            self.assertEqual((folder / "before.ppm").stat().st_mode & 0o777, 0o600)
            self.assertTrue((folder / "after.ppm").exists())
            self.assertEqual(json.loads((folder / "metrics.json").read_text()), {"changed_pixels": 0})

    def test_crop_uses_supplied_screenshot_without_recapture(self):
        d = server.Desktop()
        from cu.capture import Capture
        capture = Capture(100, 100, bytes(30000), 1, 2, "test")
        monitor = {"name": "DP-1", "width": 100, "height": 100, "scale": 1,
                   "transform": 0, "x": 0, "y": 0}
        with patch.object(d.capturer, "capture", side_effect=AssertionError("recaptured")):
            result = d.target_pixels({"at": [10, 10], "size": [20, 20]}, monitor, capture)
            self.assertEqual(result, (4, 4, bytes(48)))

    def test_focus_race_after_restoration_rejected(self):
        d, f, state, args = self.approval_desktop()
        d.dispatch = lambda *unused: None
        with patch.object(server.subprocess, "run") as process, patch.object(d, "screenshot", return_value=[]), \
                patch.object(server.time, "sleep"):
            process.return_value.returncode = 1
            with self.assertRaisesRegex(server.ActionRejected, "Focus changed again"):
                d.prepare("scroll", args)

    def test_restore_flag_requires_real_boolean_and_mutating_annotation(self):
        with self.assertRaises(ValueError):
            server.validate("type_text", {"frame_id": "x", "text": "x", "restore_focus": "true"})
        for tool in server.TOOLS:
            if "restore_focus" in tool["inputSchema"]["properties"]:
                self.assertFalse(tool["annotations"]["readOnlyHint"])

    def test_session_selection_refuses_ambiguity_and_stale_explicit_values(self):
        one = {"instance": "sig_a", "wl_socket": "wayland-1"}
        two = {"instance": "sig_b", "wl_socket": "wayland-2"}
        self.assertEqual(server.select_session([one]), one)
        self.assertIsNone(server.select_session([one, two]))
        self.assertEqual(server.select_session([one, two], display="wayland-2"), two)
        self.assertIsNone(server.select_session([one], signature="stale"))
        self.assertIsNone(server.select_session([{"instance": "../bad", "wl_socket": "wayland-1"}]))

    def test_session_discovery_when_user_bus_unavailable(self):
        response = json.dumps([{"instance": "sig_a", "wl_socket": "wayland-1"}]).encode()
        with patch.dict(os.environ, {}, clear=True), patch.object(server, "owned_socket", return_value=True), \
                patch.object(server, "run", side_effect=[RuntimeError("no bus"), response]):
            server.session_env()
            self.assertEqual(os.environ["HYPRLAND_INSTANCE_SIGNATURE"], "sig_a")
            self.assertEqual(os.environ["WAYLAND_DISPLAY"], "wayland-1")

    def test_session_manager_import_does_not_import_unrelated_secrets(self):
        response = b"HYPRLAND_INSTANCE_SIGNATURE=sig_a\nWAYLAND_DISPLAY=wayland-1\nSECRET=private\n"
        with patch.dict(os.environ, {}, clear=True), patch.object(server, "owned_socket", return_value=True), \
                patch.object(server, "run", return_value=response):
            server.session_env()
            self.assertEqual(os.environ["HYPRLAND_INSTANCE_SIGNATURE"], "sig_a")
            self.assertNotIn("SECRET", os.environ)

    def test_scroll_supplies_both_axes_and_option_terminator(self):
        d = server.Desktop()
        with patch.object(d, "guard", return_value=frame()), patch.object(d, "move"), \
                patch.object(d, "screenshot", return_value=[]), patch.object(d, "mouse") as mouse:
            d.call("scroll", {"frame_id": "x", "x": 10, "y": 10, "steps": -3})
            mouse.assert_called_with("mousemove", "--wheel", "--", "0", "-3")
            d.call("scroll", {"frame_id": "x", "x": 10, "y": 10, "steps": 3, "axis": "horizontal"})
            mouse.assert_called_with("mousemove", "--wheel", "--", "3", "0")

    def test_lua_dispatch_templates(self):
        d = server.Desktop()
        with patch.object(server, "run", return_value=b"ok\n") as run:
            d.dispatch("focuswindow", "address:0x123")
            run.assert_called_with(["hyprctl", "dispatch", 'hl.dsp.focus({window="address:0x123"})'])
            d.move((-100, 900))
            run.assert_called_with(["hyprctl", "dispatch", "hl.dsp.cursor.move({x=-100,y=900})"])
            with self.assertRaises(ValueError):
                d.dispatch("focuswindow", 'address:0x123"; anything')

    def test_rotated_monitor_coordinates(self):
        f = frame({"name": "HDMI-A-1", "x": 2560, "y": 0, "width": 1920,
                   "height": 1080, "scale": 1, "transform": 1})
        f.update(width=1080, height=1920)
        self.assertEqual(server.Desktop.point(f, 100, 1800), (2660, 1800))

    def test_fractional_scale_negative_origin(self):
        f = frame({"name": "DP-2", "x": -1707, "y": 0, "width": 2560,
                   "height": 1440, "scale": 1.5, "transform": 0})
        f.update(width=1707, height=960)
        self.assertEqual(server.Desktop.point(f, 0, 0), (-1707, 0))
        self.assertEqual(server.Desktop.point(f, 1706, 959), (-2, 959))

    def test_outside_image_rejected(self):
        for x, y in [(-1, 0), (2560, 0), (0, 1440), (True, 0)]:
            with self.assertRaises(ValueError):
                server.Desktop.point(frame(), x, y)

    def test_schema_rejects_command_injection_surface(self):
        with self.assertRaises(ValueError):
            server.validate("desktop_state", {"command": "anything"})
        with self.assertRaises(ValueError):
            server.validate("pointer", {"frame_id": "x", "x": True, "y": 1})
        with self.assertRaises(ValueError):
            server.key_args("CTRL+Return;anything")

    def test_chord_releases_modifiers(self):
        self.assertEqual(server.key_args("CTRL+SHIFT+A"),
                         ["-M", "ctrl", "-M", "shift", "-k", "A", "-m", "shift", "-m", "ctrl"])

    def test_stale_focus_rejected_before_input(self):
        d = server.Desktop()
        f = frame()
        d.frames["token"] = f
        d.hypr = lambda cmd: [f["monitor"]] if cmd == "monitors" else {"address": "0x456"}
        with patch.object(server, "run") as run:
            with self.assertRaisesRegex(ValueError, "Active window changed"):
                d.call("type_text", {"frame_id": "token", "text": "hello"})
            run.assert_not_called()

    def test_expired_frame_rejected(self):
        d = server.Desktop()
        d.frames["token"] = frame()
        d.frames["token"]["time"] -= 121
        with self.assertRaises(ValueError):
            d.guard("token")

    def test_literal_text_uses_stdin_and_consumes_frame(self):
        d = server.Desktop()
        d.frames["token"] = frame()
        with patch.object(d, "guard", return_value=frame()), patch.object(d, "screenshot", return_value=[]), \
                patch.object(server, "run") as run:
            d.call("type_text", {"frame_id": "token", "text": "$(secret) `literal` — café"})
            run.assert_called_once_with(["wtype", "-"], "$(secret) `literal` — café".encode())
        self.assertEqual(d.frames, {})

    def test_drag_releases_button_after_failure(self):
        d = server.Desktop()
        with patch.object(d, "guard", return_value=frame()), \
                patch.object(d, "mouse") as mouse, \
                patch.object(d, "move", side_effect=[None, RuntimeError("test failure")]):
            with self.assertRaises(server.ActionRejected) as error:
                d.call("drag", {"frame_id": "x", "x": 10, "y": 10, "end_x": 40, "end_y": 40})
            self.assertEqual(mouse.call_args_list[-1].args, ("click", "0x80"))
            self.assertEqual(json.loads(error.exception.content[0]["text"])["action_performed"], "unknown")

    def test_shared_crop_maps_coordinates_and_uses_exact_capture(self):
        from cu.capture import Capture
        d, f, state, args = self.approval_desktop()
        monitor = dict(f['monitor'], id=1)
        target = dict(f['target'], at=[20,30], size=[100,100])
        capture = Capture(100,100,bytes(30000),time.monotonic_ns(),time.monotonic_ns(),'test')
        with patch.object(d.capturer,'capture',side_effect=AssertionError('recaptured')):
            content=d.capture_content(capture,monitor,target,f['layout'],'0x123',[20,30,100,100],True)
        meta=json.loads(content[0]['text']);current=d.frames[meta['frame_id']]
        self.assertEqual(d.point(current,50,60),(70,90))
        self.assertEqual(current['visual'],(84,84,bytes(84*84*3)))
        self.assertEqual(meta['origin'],[20,30])
        self.assertTrue(current['shared_observation'])

    def test_shared_observation_checks_pixels_without_restoring_focus(self):
        d,f,state,args=self.approval_desktop()
        state['active']='0x123';f['shared_observation']=True
        args['restore_focus']=False
        changed=bytearray(f['visual'][2]);changed[0]=255
        with patch.object(server.subprocess,'run') as process, \
                patch.object(d,'target_pixels',return_value=(784,584,bytes(changed))), \
                patch.object(d,'screenshot',return_value=[]),patch.object(d,'dispatch') as dispatch:
            process.return_value.returncode=1
            with self.assertRaises(server.ActionRejected):d.prepare('pointer',args)
            dispatch.assert_not_called()

    def test_input_plus_wait_executes_once_and_reports_timeout(self):
        d=server.Desktop();f=frame();f['target']={'address':'0x123'}
        args={'frame_id':'token','text':'hello','after':{'condition':{'kind':'accessible','name':'Ready'},'timeout_ms':10}}
        d.frames['token']=f
        with patch.object(d,'guard',return_value=f),patch.object(server,'run') as run, \
                patch.object(d,'observation_content',return_value=[server.text_content({'status':'timeout','condition_met':False})]) as wait, \
                patch.object(d,'screenshot',return_value=[]),patch.object(server.time,'sleep') as sleep:
            result=d.call('type_text',args)
        run.assert_called_once_with(['wtype','-'],b'hello')
        sleep.assert_not_called()
        meta=json.loads(result[0]['text'])
        self.assertTrue(meta['action_performed'])
        self.assertEqual(wait.call_args.args[0]['after_action'],meta['action_completed_ns'])
        self.assertEqual(meta['status'],'timeout')

    def test_ordinary_input_preserves_first_block_frame_metadata(self):
        d=server.Desktop()
        with patch.object(d,'guard',return_value=frame()),patch.object(server,'run'), \
                patch.object(d,'screenshot',return_value=[server.text_content({'frame_id':'next','timings_ms':{'encode_ms':1}})]):
            result=d.call('press_key',{'frame_id':'old','key':'Tab'})
        meta=json.loads(result[0]['text'])
        self.assertEqual(meta['frame_id'],'next')
        self.assertEqual(meta['timings_ms']['encode_ms'],1)
        self.assertTrue(meta['action_performed'])

    def test_post_input_capture_failure_reports_performed_and_never_replays(self):
        d=server.Desktop()
        with patch.object(d,'guard',return_value=frame()),patch.object(server,'run') as run, \
                patch.object(d,'screenshot',side_effect=RuntimeError('capture unavailable')):
            with self.assertRaises(server.ActionRejected) as error:
                d.call('type_text',{'frame_id':'x','text':'hello'})
        run.assert_called_once()
        self.assertTrue(json.loads(error.exception.content[0]['text'])['action_performed'])

    def test_invalid_wait_rejected_before_focus_or_input(self):
        d,f,state,args=self.approval_desktop()
        args.pop('steps');args['after']={'condition':{'kind':'accessible','name':'Ready'},'timeout_ms':True}
        with patch.object(d,'dispatch') as dispatch,patch.object(d,'mouse') as mouse:
            with self.assertRaises(ValueError):d.call('pointer',args)
        dispatch.assert_not_called();mouse.assert_not_called()

    def test_guard_rejection_retains_timing_and_focus_side_effect(self):
        d, f, state, args = self.approval_desktop()
        args.pop('steps')
        changed = bytearray(f['visual'][2]); changed[0] = 255
        with patch.object(server.subprocess, 'run') as process, patch.object(server.time, 'sleep'), \
                patch.object(d, 'target_pixels', return_value=(784, 584, bytes(changed))), \
                patch.object(d, 'screenshot', return_value=[server.text_content({'frame_id': 'fresh'})]):
            process.return_value.returncode = 1
            with self.assertRaises(server.ActionRejected) as error:
                d.call('pointer', args)
        meta = json.loads(error.exception.content[0]['text'])
        self.assertFalse(meta['action_performed'])
        self.assertTrue(meta['requires_review'])
        for key in ('guard_ms', 'focus_restore_ms', 'visual_check_ms', 'recovery_ms', 'total_ms'):
            self.assertIn(key, meta['timings_ms'])
        self.assertNotIn('input_ms', meta['timings_ms'])
        self.assertTrue(d.focus_restored)
        self.assertEqual(json.loads(error.exception.content[1]['text'])['frame_id'], 'fresh')

    def test_partial_input_failure_retains_timing(self):
        d = server.Desktop()
        with patch.object(d, 'guard', return_value=frame()), \
                patch.object(server, 'run', side_effect=RuntimeError('backend down')):
            with self.assertRaises(server.ActionRejected) as error:
                d.call('type_text', {'frame_id': 'token', 'text': 'hello'})
        meta = json.loads(error.exception.content[0]['text'])
        self.assertEqual(meta['action_performed'], 'unknown')
        for key in ('guard_ms', 'input_ms', 'total_ms'):
            self.assertIn(key, meta['timings_ms'])
        self.assertNotIn('result_ms', meta['timings_ms'])
        self.assertFalse(d.focus_restored)

    def test_read_only_calls_report_request_total(self):
        d = server.Desktop()
        with patch.object(d, 'screenshot', return_value=[server.text_content({'frame_id': 'f', 'timings_ms': {'capture_ms': 1}})]):
            meta = json.loads(d.call('screenshot', {})[0]['text'])
        self.assertEqual(meta['timings_ms']['capture_ms'], 1)
        for key in ('result_ms', 'total_ms'):
            self.assertIn(key, meta['timings_ms'])
        self.assertNotIn('action_performed', meta)

    def test_trace_sidecar_records_every_outcome_without_text(self):
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            with patch.dict(os.environ, {'WAYLAND_CU_TRACE_DIR': folder}):
                d = server.Desktop()
            self.assertTrue(d.recorder.enabled)
            delivered = [server.text_content({'frame_id': 'n', 'capture_backend': 'grim-ppm', 'fallback_reason': 'helper_unavailable',
                                              'width': 2, 'height': 1, 'png_bytes': 70, 'timings_ms': {'capture_ms': 1}}),
                         {'type': 'image', 'mimeType': 'image/png', 'data': 'AAAA'}]
            with patch.object(d, 'guard', return_value=frame()), patch.object(server, 'run'), \
                    patch.object(d, 'screenshot', return_value=delivered):
                d.call('type_text', {'frame_id': 'old', 'text': 'SECRET WORDS'})
            with patch.object(d, 'guard', side_effect=ValueError('Frame missing')):
                with self.assertRaises(ValueError):
                    d.call('press_key', {'frame_id': 'old', 'key': 'F13'})
            d.close()
            text = Path(d.recorder.path).read_text()
            self.assertNotIn('SECRET', text)
            self.assertNotIn('F13', text)
            records = [json.loads(line) for line in text.splitlines()]
            self.assertEqual([r['kind'] for r in records], ['process', 'request', 'request'])
            self.assertIn('server.py', records[0]['source_sha256'])
            ok, failed = records[1], records[2]
            self.assertEqual((ok['tool'], ok['seq'], ok['text_len'], ok['outcome']['status'], ok['outcome']['action_performed']),
                             ('type_text', 1, 12, 'ok', True))
            self.assertEqual((ok['result']['images'], ok['result']['image_base64_bytes']), (1, 4))
            self.assertEqual(ok['result']['frames'][0]['fallback_reason'], 'helper_unavailable')
            self.assertEqual(ok['result']['frames'][0]['capture_backend'], 'grim-ppm')
            self.assertEqual([s['name'] for s in ok['spans']][:1], ['request'])
            self.assertEqual({s['name'] for s in ok['spans']} >= {'guard', 'input', 'result'}, True)
            self.assertTrue(all(s['end_ns'] is not None for s in ok['spans']))
            self.assertEqual((failed['tool'], failed['outcome']['status'], failed['outcome']['error_type'],
                              failed['outcome']['action_performed'], failed['outcome']['reason']),
                             ('press_key', 'error', 'ValueError', False, 'Frame missing'))
            self.assertIn('total_ms', failed['timings_ms'])
            self.assertTrue(d.recorder.status()['complete'])

    def test_mcp_handshake_and_validation(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "type_text", "arguments": {"text": "no frame"}}},
        ]
        stdin = io.TextIOWrapper(io.BytesIO("\n".join(map(json.dumps, requests)).encode()))
        stdout = io.StringIO()
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout), patch.object(server, "session_env"):
            server.serve()
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(responses[1]["result"]["tools"]), 11)
        self.assertTrue(responses[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
