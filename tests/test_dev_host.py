import hashlib
import importlib.util
import json
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("dev_host", SCRIPTS / "dev_host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
spec = importlib.util.spec_from_file_location("dev_publish", SCRIPTS / "dev_publish.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

FIXTURE = '''
import json
TOOLS = [{"name": "test", "description": "fixed"}]
def session_env(): pass
def validate(name, args): pass
def text_content(value): return {"type": "text", "text": json.dumps(value)}
class ActionRejected(Exception): pass
class Desktop:
    def __init__(self): self.frames = {}
    def call(self, name, args):
        if args.get("old_frame") and "frame" not in self.frames:
            raise ValueError("Frame missing")
        self.frames["frame"] = True
        return [text_content({"build": BUILD})]
BUILD = %r
'''


def release(root, source):
    data = source.encode()
    revision = hashlib.sha256(data).hexdigest()
    directory = root / ".dev" / "releases" / revision
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "server.py").write_bytes(data)
    (root / ".dev" / "current.json").write_text(json.dumps({"revision": revision}))
    return revision


class DevTests(unittest.TestCase):
    def test_reload_discards_frames_and_pins_tool_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release(root, FIXTURE % "one")
            runtime = host.Runtime(root)
            runtime.refresh()
            runtime.desktop.frames["old"] = 1
            release(root, FIXTURE % "two")
            self.assertTrue(runtime.refresh())
            self.assertEqual(runtime.module.BUILD, "two")
            self.assertEqual(runtime.desktop.frames, {})
            self.assertFalse(runtime.refresh())
            release(root, (FIXTURE % "three").replace('"fixed"', '"changed"'))
            with self.assertRaisesRegex(ValueError, "Tool definitions changed"):
                runtime.refresh()
            self.assertEqual(runtime.module.BUILD, "two")

    def test_checksum_invalid_revision_and_syntax_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rev = release(root, FIXTURE % "one")
            runtime = host.Runtime(root)
            runtime.refresh()
            (root / ".dev" / "releases" / rev / "server.py").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "checksum"):
                runtime.refresh()
            release(root, "invalid python !")
            with self.assertRaises(SyntaxError):
                runtime.refresh()
            (root / ".dev" / "current.json").write_text('{"revision":"../../bad"}')
            with self.assertRaisesRegex(ValueError, "Invalid published"):
                runtime.refresh()

    def test_publish_failure_preserves_previous_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "server.py").write_text(FIXTURE % "new")
            (root / "scripts" / "dev_host.py").touch()
            previous = release(root, FIXTURE % "old")
            with patch.object(publisher.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "tests")):
                with self.assertRaises(subprocess.CalledProcessError):
                    publisher.publish(root, root)
            self.assertEqual(json.loads((root / ".dev" / "current.json").read_text())["revision"], previous)
            with patch.object(publisher.subprocess, "run"):
                result = publisher.publish(root, root)
            self.assertNotEqual(result["published_revision"], previous)

    def test_persistent_stdio_reload_and_old_frame_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            shutil.copyfile(SCRIPTS / "dev_host.py", root / "scripts" / "dev_host.py")
            first = release(root, FIXTURE % "one")
            process = subprocess.Popen([sys.executable, str(root / "scripts" / "dev_host.py")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            def request(method, args=None):
                process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                    "params": {"name": "test", "arguments": args or {}}}) + "\n")
                process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5), "Host response timed out")
                return json.loads(process.stdout.readline())["result"]
            try:
                request("initialize")
                before = request("tools/call")
                second = release(root, FIXTURE % "two")
                stale = request("tools/call", {"old_frame": True})
                self.assertTrue(stale["isError"])
                after = request("tools/call")
                self.assertEqual(json.loads(after["content"][0]["text"])["build"], "two")
                a = json.loads(before["content"][-1]["text"])["development_runtime"]
                b = json.loads(after["content"][-1]["text"])["development_runtime"]
                self.assertEqual((a["revision"], b["revision"]), (first, second))
                self.assertEqual(a["host_pid"], b["host_pid"])
                release(root, "broken !")
                failure = request("tools/call")
                self.assertTrue(failure["isError"])
                self.assertFalse(json.loads(failure["content"][0]["text"])["action_performed"])
            finally:
                process.stdin.close()
                process.wait(timeout=5)
                process.stdout.close()
