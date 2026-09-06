# Optional persistent capture helper

Build from the repository root with `python3 scripts/build_capture.py`.
Dependencies: C compiler, pkg-config, wayland-client headers/library and
wayland-scanner. The generated executable is ignored by git and can be included
in a checksummed local runtime bundle by `dev_publish.py`.

`wlr-screencopy-unstable-v1.xml` was obtained from the upstream
[wlr-protocols repository](https://github.com/swaywm/wlr-protocols/blob/master/unstable/wlr-screencopy-unstable-v1.xml)
on 2026-09-06. Its permissive license is retained inside the XML. Protocol
bindings are generated during the build; generated files are not tracked.

The helper binds no input interfaces, creates no listener socket, and reads only
enumerated capture requests on stdin. The parent validates geometry and backend
selection, limits reads, kills unresponsive helpers, and falls back to raw grim.
It supports wlr-screencopy v3, wl_output v4 names, untransformed scale-1 outputs
and ARGB/XRGB/ABGR/XBGR 8888 shared-memory formats. Other configurations use the
fallback. Capture guards, leases and user authorization remain in the Python
servers; invoking this raw helper directly does not provide those guards.

`copy_with_damage` is bounded by a local deadline. On timeout the cancelled
frame's buffer is discarded and a new immediate capture is requested. The
parent also enforces a total read timeout. Presentation timestamps are reported
as protocol metadata, without assuming they prove application task completion.
