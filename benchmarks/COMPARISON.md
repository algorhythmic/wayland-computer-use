# Same-task comparison — 2026-09-06

Task: observe a disposable GTK status window, observe it again while unchanged,
then detect its scheduled transition from Waiting to Ready.

Six trials per implementation, alternating their order. Each trial included one
initial observation, three unchanged follow-ups, and one change-detection task.
The status changed 500 ms after starting the detection phase. Both MCP servers
were already initialized; startup and tool discovery are excluded. Existing
Wayland Computer Use ran through its installed `dev_host.py`; the experimental
observer ran through its own stdio server. Neither implementation was modified.

| Measurement (median) | Original | Observer |
|---|---:|---:|
| Initial observation | 350 ms | 359 ms |
| Initial response, serialized JSON | 3.39 MB | 0.97 MB |
| Unchanged follow-up | 337 ms | 399 ms |
| Unchanged response, serialized JSON | 3.39 MB | 973 bytes |
| Detect Ready, measured after label update | 813 ms | 316 ms |
| Detection task including scheduled 500 ms wait | 1,314 ms | 816 ms |
| Tool calls during detection | 3 | 1 |
| Total detection responses, serialized JSON | 10.16 MB | 19.73 KB |
| Image pixels delivered during detection | 11,059,200 | 16,384 |

The observer completed the local change-detection portion about 2.58 times as
quickly, with about 99.81% less serialized response data. Initial observations
were effectively tied in this small run. Unchanged reads were about 18% slower
in median latency despite eliminating image delivery. One observer unchanged read
took 1,250 ms; its fresh-sample scheduling can add waiting time.

## Interpretation and limits

- These are local MCP transport and deterministic verification timings. They do
  **not** measure model inference, approvals, model accuracy, or end-to-end agent
  completion. Image bytes and pixel counts are not model token measurements.
- The original returns a full 2560×1440 monitor image. The observer returns a
  window crop initially and a small changed-region crop later. Thus these are
  comparisons of the two current pipelines, not equal-sized capture encoders.
- The original detection loop compares pixels only in the known status-label
  rectangle. PNG decoding/cropping is included in its detection-task time, but
  not in its individual MCP tool timings. The observer verifies the exact Ready
  label from AT-SPI. These are different evidence paths for the same status task;
  a screenshot-only application may not show the same benefit.
- Original screenshot polling ran back-to-back without an extra sleep or model
  round trip. The observer used `wait_for_change`, with no model polling loop.
- Its background observation was stopped before each trial. Capture/probing ran
  locally while its own trial was active; a full CPU/GPU utilization comparison
  was not performed. The observer's work between requests is not fully reflected
  by tool latency alone.
- The change timestamp marks GTK's label update, not the compositor's presentation
  timestamp. No click or keystroke was injected. Only the disposable fixture was
  opened and closed; application configuration was not changed.
- Six trials are enough for an initial comparison, not a general performance
  claim. The UI was static apart from the status label. Downloads, moving meters,
  accessibility-poor applications, and model-mediated tasks remain to be tested.

Raw samples and ranges: [comparison-20260906-011216.json](comparison-20260906-011216.json).

Reproduce on an unlocked desktop, allowing the disposable window to remain focused:

```bash
python3 scripts/benchmark.py
```

Run from the observer project root. This requires GTK4/PyGObject and desktop socket
access in addition to both plugins' dependencies. It writes metrics only under
`benchmarks/`; screen images are not saved. The process stops on capture/focus
failure instead of refocusing over the user's interaction.
