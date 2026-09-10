#!/usr/bin/env python3
"""Stable stdio host: reload explicitly published implementations between requests.

No watcher, network listener, signals, arbitrary tool command, or action replay.
Restart this host for host/protocol changes; the verified runtime bundle reloads.
"""
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import types


class HostTrace:
    """Optional host-side envelope per tools/call, written as JSON lines to WAYLAND_CU_TRACE_DIR.

    Measures request-received to response-flushed in the host, identically for
    every runtime revision, so legacy bundles without timing fields can be
    compared with current ones. Records tool names, sizes, outcome and revision
    only; no arguments, screen contents or text.
    """
    def __init__(self):
        self.file = None
        location = os.environ.get('WAYLAND_CU_TRACE_DIR')
        if not location:
            return
        try:
            root = Path(location)
            info = root.lstat()
            if not root.is_absolute() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return
            path = root/f"host-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.jsonl"
            self.file = os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
        except OSError:
            self.file = None

    def write(self, record):
        if self.file is None:
            return
        try:
            self.file.write(json.dumps({'kind': 'host', 'schema': 1, 'pid': os.getpid(),
                                        'wall_s': time.time(), **record})+'\n')
            self.file.flush()
        except OSError:
            self.file = None


class VerifiedImports(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Import dependencies from the bytes just verified, without reading pyc files."""
    def __init__(self, prefix, directory, files):
        self.prefix, self.directory, self.files = prefix, directory, files

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(self.prefix+'.'):
            return None
        relative = fullname[len(self.prefix)+1:].replace('.', '/')
        package = relative+'/__init__.py'
        name = package if package in self.files else relative+'.py'
        if name not in self.files:
            raise ModuleNotFoundError('Unpublished runtime dependency: '+fullname)
        spec = importlib.util.spec_from_loader(fullname, self, is_package=name == package)
        spec.loader_state = name
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__spec__.loader_state
        module.__file__ = str(self.directory/name)
        exec(compile(self.files[name], module.__file__, 'exec'), module.__dict__)


def forget(prefix):
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix+'.'):
            sys.modules.pop(name, None)


class Runtime:
    def __init__(self, root):
        self.root = Path(root)
        self.module = self.desktop = self.revision = self.contract = None

    def refresh(self):
        pointer = self.root / ".dev" / "current.json"
        revision = json.loads(pointer.read_text())["revision"]
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
            raise ValueError("Invalid published revision")
        path = self.root / ".dev" / "releases" / revision / "server.py"
        source = path.read_bytes()
        files = {'server.py': source}
        manifest_path = path.with_name('bundle.json')
        if manifest_path.exists():
            manifest = manifest_path.read_bytes()
            if hashlib.sha256(manifest).hexdigest() != revision:
                raise ValueError('Published bundle checksum mismatch')
            bundle = json.loads(manifest)
            if bundle.get('format') != 2 or 'server.py' not in bundle.get('files', {}):
                raise ValueError('Invalid published bundle')
            for name, checksum in bundle['files'].items():
                if not re.fullmatch(r'(server\.py|cu/[a-z_]+\.py|cu/capture-helper)', name):
                    raise ValueError('Invalid bundle path')
                data = (path.parent/name).read_bytes()
                if hashlib.sha256(data).hexdigest() != checksum:
                    raise ValueError('Published dependency checksum mismatch')
                files[name] = data
            source = files['server.py']
        elif hashlib.sha256(source).hexdigest() != revision:
            raise ValueError("Published code checksum mismatch")
        if revision == self.revision:
            return False
        module = types.ModuleType("wayland_runtime_" + revision)
        module.__file__ = str(path)
        module.__package__ = module.__name__
        module.__path__ = []
        sys.modules[module.__name__] = module
        sys.dont_write_bytecode = True
        loader = VerifiedImports(module.__name__, path.parent, files)
        sys.meta_path.insert(0, loader)
        try:
            exec(compile(source, str(path), "exec"), module.__dict__)
            contract = json.dumps(module.TOOLS, sort_keys=True)
            if self.contract is not None and contract != self.contract:
                raise ValueError("Tool definitions changed; host/client rediscovery required")
            module.session_env()
            desktop = module.Desktop()
            if hasattr(desktop, 'runtime_identity'):
                desktop.runtime_identity = {'kind': 'published_bundle', 'revision': revision,
                    'source_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                    'published_skill_sha256': bundle.get('skill_sha256') if manifest_path.exists() else None,
                    'published_skill_files_sha256': bundle.get('skill_files_sha256') if manifest_path.exists() else None}
                if 'WCU_CONTEXT_ROOT' not in os.environ:
                    desktop.context_root = self.root/'.dev/keyboard-context/normalized'
            if hasattr(desktop, 'warm'):
                desktop.warm()  # Pre-start the accessibility worker; legacy bundles lack this.
        except BaseException:
            forget(module.__name__)
            raise
        finally:
            sys.meta_path.remove(loader)
        if self.desktop and hasattr(self.desktop, 'close'):
            self.desktop.close()
        if self.module:
            forget(self.module.__name__)
        self.module, self.desktop = module, desktop
        self.revision, self.contract = revision, contract
        return True

    def handle(self, request):
        self.refresh()  # Serial request boundary: never reload during a drag/click.
        module = self.module
        method, params = request.get("method"), request.get("params", {})
        if method == "initialize":
            return {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "wayland-computer-use", "version": self.revision},
                    "instructions": "Shared live desktop: retain approval gates. Use fresh scoped evidence before input; view_frame is required before coordinate input. Inputs return text evidence by default. Prefer obsidian_create_note for verified notes; reconcile uncertain operations with the same operation_id, never a new ID. "
                    "For approval focus changes approve one combined restore_focus action naming target_window and target_title. "
                    "After a development reload all old frame IDs are invalid; observe again. Never replay failed input."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": module.TOOLS}
        if method != "tools/call":
            raise ValueError("Unsupported method")
        try:
            name, args = params["name"], params.get("arguments", {})
            module.validate(name, args)
            result = {"content": self.desktop.call(name, args), "isError": False}
        except module.ActionRejected as exc:
            result = {"content": exc.content, "isError": True}
        except Exception as exc:
            result = {"content": [module.text_content({"error": str(exc)[:1400]})], "isError": True}
        if params.get('name') != 'context_for_task':
            result["content"].append(module.text_content(
                {"development_runtime": {"revision": self.revision, "host_pid": os.getpid()}}))
        return result


def serve(root=None):
    runtime = Runtime(root or Path(__file__).resolve().parents[1])
    host_trace = HostTrace()
    try:
        runtime.refresh()  # Load and warm at launch; a missing bundle still reports on the first request.
    except Exception:
        pass
    try:
        for line in sys.stdin.buffer:
            request = None
            received_ns = time.monotonic_ns()
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Expected JSON-RPC object")
                if "id" not in request:
                    continue
                try:
                    result = runtime.handle(request)
                except Exception as exc:
                    # No action was dispatched when refresh fails. Never fall back
                    # silently to the old implementation or replay a queued action.
                    if request.get("method") != "tools/call":
                        raise
                    result = {"isError": True, "content": [{"type": "text", "text": json.dumps(
                        {"error": str(exc)[:1400], "action_performed": False,
                         "requires_review": True, "development_reload_failed": True})}]}
                reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            except Exception as exc:
                reply = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                         "error": {"code": -32603, "message": str(exc)[:1400]}}
            encoded = json.dumps(reply)
            print(encoded, flush=True)
            if isinstance(request, dict) and request.get("method") == "tools/call":
                content = reply.get("result", {}).get("content", []) if "result" in reply else []
                host_trace.write({"tool": str(request.get("params", {}).get("name", ""))[:40],
                                  "revision": runtime.revision, "received_ns": received_ns,
                                  "flushed_ns": time.monotonic_ns(), "is_error": bool(reply.get("result", {}).get("isError")) or "error" in reply,
                                  "images": sum(1 for c in content if c.get("type") == "image"),
                                  "response_bytes": len(encoded)})
    finally:
        if runtime.desktop and hasattr(runtime.desktop, "close"):
            runtime.desktop.close()


if __name__ == "__main__":
    serve()
