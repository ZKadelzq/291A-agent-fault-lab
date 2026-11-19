# Phase 2 provenance and reuse audit

Scope: 48 main-matrix faulty source traces and 192 paired recovery traces. This analysis reads saved data only; it makes no model or tool calls and changes no scores.

## Reuse integrity

Every retained node was checked against the paired faulty source trace: exact source ID, context hash, and original node fields (except the added `reused_from` tag). For a verified executor prefix, repaired-plan provenance may replace `parents`, `uses`, and `step` metadata; executor-visible operation, arguments, action receipts, and other fields must stay identical. Retained nodes in the invalidation set must be executor prefix nodes that passed the runtime step/receipt/checkpoint validation. Stopped rows must report zero retained nodes.

| Strategy | Retained nodes passing source checks | Revalidated invalidated executor prefix | Same content and parsed data as separate clean control |
| --- | ---: | ---: | ---: |
| restart | 0/0 | 0 | 0/0 |
| selective | 144/144 | 0 | 125/144 |
| change_aware | 242/242 | 98 | 169/242 |
| reconcile | 138/138 | 0 | 121/138 |

Retained node types: {'researcher': 396, 'planner': 18, 'executor': 110}. The clean-control comparison is a diagnostic, not a validity oracle: independently sampled LLM text can differ while the same authorized operation and final state remain valid. Exact source preservation plus runtime receipt, checkpoint, execution-contract, and final-state checks support reuse integrity; they do not prove semantic equivalence to every possible clean run.

## Dependency precision and metadata cost

The reference runtime uses `coarse_state` (message parents plus shared-state read-after-write edges). The `uses_only` diagnostic removes plan-to-executor edges and adds declared research uses; it is **unsafe** when a changed plan alters an executor step with no declared use. `uses_state` also adds shared-state edges.

| Graph | Mean edges/source | Mean downstream nodes/fault | Nodes missed vs runtime reference |
| --- | ---: | ---: | ---: |
| coarse | 7.94 | 4.65 | 8 |
| uses_only | 6.12 | 1.56 | 156 |
| coarse_state | 10.56 | 4.81 | 0 |
| uses_state | 8.75 | 2.33 | 119 |

Across 429 source nodes, compact UTF-8 provenance fields (`parents`, `uses`, action `reads`/`writes`) occupy 52,013 bytes (121 bytes/node); serialized checkpoints occupy 57,625 bytes. The saved, pretty-printed trace files total 3,739,689 bytes. These are offline storage measurements, not incremental live memory or network usage.

Post-hoc construction of all four graph variants and reachability queries on `Linux-6.8.0-149-generic-x86_64-with-glibc2.36` (100 repetitions per source; median of 48 per-source averages):

- Median: 36.5 microseconds/source; P95: 58.5 microseconds/source.

These timings exclude live provenance capture, JSON serialization, disk I/O, model latency, and recovery work. They cannot be added to or subtracted from recorded episode latency. Run the command below on the same machine to compare implementation changes.

```bash
python analyze_phase2_provenance.py --out runs/PHASE2_PROVENANCE.md
```
