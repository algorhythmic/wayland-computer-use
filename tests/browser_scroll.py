"""Opt-in real wheel test in an isolated Chromium profile and local page."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import server
server.session_env()
d = server.Desktop()
original = d.hypr("activewindow")
with tempfile.TemporaryDirectory(prefix="wayland-scroll-test-") as profile:
    process = subprocess.Popen(["chromium", "--user-data-dir=" + profile,
        "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
        "--disable-sync", "--ozone-platform=wayland", "--app=" + (ROOT / "tests/scroll.html").as_uri()],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        target = None
        for _ in range(100):
            target = next((w for w in d.hypr("clients") if w.get("title", "").startswith("WaylandScrollTest ")), None)
            if target:
                break
            time.sleep(.1)
        assert target, "Test browser did not open"
        address = target["address"]
        if "--monitor" in sys.argv:
            output = sys.argv[sys.argv.index("--monitor") + 1]
            assert output in [m["name"] for m in d.hypr("monitors")]
            assert re.fullmatch(r"[A-Za-z0-9_-]+", output)
            assert re.fullmatch(r"0x[0-9a-fA-F]+", address)
            server.run(["hyprctl", "dispatch", 'hl.dsp.window.move({window="address:' +
                        address + '",monitor="' + output + '",follow=true})'])
        d.dispatch("focuswindow", "address:" + address)
        time.sleep(.6)
        for axis in ("vertical", "horizontal"):
            for steps in (-3, 3):
                target = next(w for w in d.hypr("clients") if w["address"] == address)
                monitor = next(m for m in d.hypr("monitors") if m["id"] == target["monitor"])
                assert monitor["scale"] == 1
                before = list(map(int, target["title"].split()[1:]))
                meta = json.loads(d.screenshot(monitor["name"])[0]["text"])
                assert meta["active_window"] == address
                extra = {}
                if "--approval-roundtrip" in sys.argv:
                    assert original.get("address") and original["address"] != address
                    d.dispatch("focuswindow", "address:" + original["address"])
                    time.sleep(.5)
                    extra = {"restore_focus": True, "target_window": address,
                             "target_title": meta["target_window"]["title"]}
                    if axis == "vertical" and steps == -3:
                        d.call("pointer", {"frame_id": meta["frame_id"],
                            "x": target["at"][0] + target["size"][0]//2 - monitor["x"],
                            "y": target["at"][1] + target["size"][1]//2 - monitor["y"], **extra})
                        print("PASS restored-focus click with pixel revalidation", flush=True)
                        meta = json.loads(d.screenshot(monitor["name"])[0]["text"])
                        d.dispatch("focuswindow", "address:" + original["address"])
                        time.sleep(.5)
                d.call("scroll", {"frame_id": meta["frame_id"],
                    "x": target["at"][0] + target["size"][0]//2 - monitor["x"],
                    "y": target["at"][1] + target["size"][1]//2 - monitor["y"],
                    "axis": axis, "steps": steps, **extra})
                time.sleep(.5)
                after = list(map(int, next(w for w in d.hypr("clients") if w["address"] == address)["title"].split()[1:]))
                index = 1 if axis == "vertical" else 0
                assert before[index] != after[index], (axis, steps, before, after)
                print("PASS browser scroll:", axis, steps, before, after, flush=True)
    finally:
        os.killpg(process.pid, 15)
        process.wait(timeout=10)
        if original.get("address"):
            d.dispatch("focuswindow", "address:" + original["address"])
