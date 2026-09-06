#!/usr/bin/env python3
"""Test then atomically publish server.py. Does not restart apps or modify approvals."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def publish(root, destination):
    root, destination = Path(root), Path(destination)
    source = (root / "scripts" / "server.py").read_bytes()
    compile(source, "server.py", "exec")
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                   cwd=root, check=True)
    if source != (root / "scripts" / "server.py").read_bytes():
        raise ValueError("Source changed during tests; publish again after edits finish")
    if not (destination / "scripts" / "dev_host.py").is_file():
        raise ValueError("Destination must have the development host installed first")
    revision = hashlib.sha256(source).hexdigest()
    state = destination / ".dev"
    release = state / "releases" / revision
    release.mkdir(parents=True, exist_ok=True, mode=0o700)
    code = release / "server.py"
    try:
        with code.open("xb") as output:
            output.write(source)
    except FileExistsError:
        if code.read_bytes() != source:
            raise ValueError("Existing release has mismatched contents; refusing overwrite")
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
