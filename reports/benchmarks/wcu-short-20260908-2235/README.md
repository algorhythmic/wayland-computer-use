# Short WCU benchmark — September 8, 2026

Completed with findings: **16 of 17 attempted artifact checks passed**; one note transfer failed its readback, and one subsequent note case was not attempted. Two trials per task/mode were planned. Canvas and focus cases ran in fresh fixtures after the first harness stopped. The failed input was not replayed.

| Task | Individual calls, monitor | Sequence, monitor | Sequence, target crop | Successful samples |
|---|---:|---:|---:|---|
| Form | 628 ms | 435 ms | 336 ms | 2, 2, 2 |
| Note | 768 ms | 488 ms | 367 ms | 1, 1, 2 |
| Canvas | 152 ms | 152 ms | 59 ms | 2, 2, 2 |

Medians above include successful samples only. Form sequences with a target crop took 46.6% less time, used one tool call instead of four, and delivered 98.5% fewer PNG bytes than individual calls. Note timing is incomplete because of the failure. These are local deterministic task timings, excluding model inference, approvals, fixture reset, and initial focus.

## Findings

- **Clipboard transfer:** the second sequence/monitor note trial submitted Ctrl+A, Ctrl+C, focus, and Ctrl+V, but the focused note stayed empty. The exact-text readback timed out after 3 seconds and prevented saving. The fixture independently confirmed empty text. The precise cause is unresolved; source selection and clipboard completion were not independently verified.
- **Natural focus interruption:** both trials typed 40 characters into the intended field and 40 into the disposable decoy, then rejected the final segment. The ledger correctly reported two submitted segments and partial execution.
- **Synchronized interruption between segments:** both trials stopped after the first 40-character segment, with zero decoy characters. This confirms the between-segment guard for the tested ordering, while the natural race remains exposed.

## Context and build

Local retrieval found all 8 expected actions in the labeled fixture with zero contract violations. Installed-catalog retrieval covered 2,306 records and 15 warm samples: p50 **69.1 ms**, p95 **88.4 ms**. Braid was not enabled.

The isolated benchmark bundle was `5586b0fdc86d3844a6fec60d095b1e467383a465fe61a8c732e021ca6903b8eb`. Runtime code bytes match the connected plugin `fb4ac4da6ab03192d99ca4b8e26963a3eb60e9f8e5748350adbc0badad1957ab`; the isolated bundle incorporates the latest skill/reference files. The loaded skill SHA-256 was `a09384f3c2baec4bf4e9a9053b5c131d3895babcece3cf9cefc2d94d6e25210e`. Existing uncommitted checkout changes were included; Git HEAD alone does not identify this build.

Publication validation passed 122 WCU tests and 31 observer tests; seven optional Braid tests were skipped. An initial sandboxed socket test could not bind; the authorized run with socket access passed. The benchmark used the unchanged repository harness copied into an isolated runtime. No source fixes or connected-plugin publication changes were made. Disposable windows were closed and the original window regained focus.

## Evidence

- [Results, execution ledgers, and exact build identity](results.json)
- [Labeled retrieval results](context-labeled.json)
- [Installed-catalog retrieval results](context-catalog.json)
- Private original harness reports and build logs: `.dev/short-benchmark-20260908-2235/`.
