# Comparison checkpoint: 2026-09-06

The annotated tag `comparison-2026-09-06` preserves the initial consolidated tree.
It must never be moved. Future releases may share helpers or servers, but this
checkpoint retains the separate input and observer implementations.

`COMPARISON.md` and `comparison-20260906-011216.json` are byte-for-byte historical
results, not a new run. `historical-benchmark.py` is the exact original runner,
retained for inspection; its old paths/import context are not a portable launcher.
The report's original paths identify source installations at measurement time.
No screenshots, audio, debug crops, credentials or local published builds are
included. Source documentation includes historical account paths, not secrets.

`SHA256SUMS` records consolidation-time implementations, the historical runner,
raw results and original report. Verify from the repository root:

```bash
sha256sum --check benchmarks/SHA256SUMS
```

The input source matches the pre-existing observer baseline provenance:
`d58ff2a66460f695ab07540524f7c78de56b84947e43ef1937d24ef93bc6076c`.
`wayland-desktop-observer/baseline/` is retained byte-for-byte, including its
archived README and checksum record. It is a frozen fixture, not another current
setup guide. Hashes collected now prove the preserved bytes, not retrospective
proof of every process/environment present during the original run.

The checksum list intentionally flags later source changes. Run it against the
tagged checkpoint; do not update historical hashes to make newer code pass.

## Repeat the comparison

Use a separate checkout at the tag so ongoing development and installed plugins
are unaffected. Install dependencies from the root README. From that checkout:

```bash
python3 scripts/configure_mcp.py --write-plugin-configs
python3 scripts/dev_publish.py --destination .
python3 wayland-desktop-observer/scripts/benchmark.py
```

The migrated runner uses this checkout's `.mcp.json`, not the original machine's
installed plugin. It otherwise retains the task/predicates and writes timestamped
results to root `benchmarks/`. New reports add source hashes; the input host's
published build must match the source (publish immediately before measuring).
Don't edit sources or republish while a measurement is running.

The live benchmark opens/closes a disposable GTK window. Allow it to remain
focused; it stops on capture/focus failure instead of stealing focus back. It does
not inject input, record a video, or change application settings. Screenshots are
processed in memory. This consolidation did not rerun the live benchmark or
claim that today's desktop reproduces the original timings.

For the video, show the tagged version and report, then the full-monitor polling
versus window/delta/wait paths. Report initial and unchanged-read regressions as
well as detection gains. Measure accuracy separately with labelled tasks,
failures and repeated trials; this report has no model-mediated accuracy data.
