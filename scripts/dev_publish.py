#!/usr/bin/env python3
"""Test then atomically publish a checksummed runtime bundle. No app restarts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def sources(root):
    files = {'server.py': (root/'scripts/server.py').read_bytes()}
    if (root/'scripts/cu').is_dir():
        for path in sorted((root/'scripts/cu').glob('*.py')):
            files['cu/'+path.name] = path.read_bytes()
        helper = root/'scripts/cu/capture-helper'
        if helper.is_file():
            files['cu/capture-helper'] = helper.read_bytes()
    return files


def publish(root, destination):
    root, destination = Path(root), Path(destination)
    files = sources(root)
    for name, data in files.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                   cwd=root, check=True)
    observer_tests = root/'wayland-desktop-observer/tests'
    if observer_tests.is_dir():
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(observer_tests), '-v'], cwd=root, check=True)
    if files != sources(root):
        raise ValueError("Source changed during tests; publish again after edits finish")
    if not (destination / "scripts" / "dev_host.py").is_file():
        raise ValueError("Destination must have the development host installed first")
    manifest = json.dumps({'format': 2, 'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}, sort_keys=True).encode()
    revision = hashlib.sha256(manifest).hexdigest()
    state = destination / ".dev"
    release = state / "releases" / revision
    release.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, data in {**files, 'bundle.json': manifest}.items():
        code = release/name
        code.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with code.open('xb') as output:
                output.write(data)
        except FileExistsError:
            if code.read_bytes() != data:
                raise ValueError('Existing release has mismatched contents; refusing overwrite')
        if name == 'cu/capture-helper':
            code.chmod(0o700)
    # Commit pointer only after the complete release is available.
    fd, pending = tempfile.mkstemp(prefix="publish-", dir=state)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump({"revision": revision}, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, state / "current.json")
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    return {"published_revision": revision, "destination": str(destination),
            "activation": "next request in each already-running dev_host; old frame IDs invalid"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(publish(Path(__file__).resolve().parents[1], options.destination), indent=2))
