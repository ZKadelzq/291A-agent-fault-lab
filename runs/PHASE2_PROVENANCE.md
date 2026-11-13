# Phase 2 provenance and reuse audit

Scope: 48 main-matrix faulty source traces and 192 paired recovery traces. This analysis reads saved data only; it makes no model or tool calls and changes no scores.

## Reuse integrity

Every retained node was checked against the paired faulty source trace: exact source ID, context hash, and original node fields (except the added `reused_from` tag). For a verified executor prefix, repaired-plan provenance may replace `parents`, `uses`, and `step` metadata; executor-visible operation, arguments, action receipts, and other fields must stay identical. Retained nodes in the invalidation set must be executor prefix nodes that passed the runtime step/receipt/checkpoint validation. Stopped rows must report zero retained nodes.

| Strategy | Retained nodes passing source checks | Revalidated invalidated executor prefix | Same content and parsed data as separate clean control |
| --- | ---: | ---: | ---: |
| restart | 0/0 | 0 | 0/0 |
| selective | 120/120 | 0 | 114/120 |
| change_aware | 209/209 | 86 | 186/209 |
| reconcile | 138/138 | 0 | 131/138 |

Retained node types: {'researcher': 355, 'planner': 16, 'executor': 96}. The clean-control comparison is a diagnostic, not a validity oracle: independently sampled LLM text can differ while the same authorized operation and final state remain valid. Exact source preservation plus runtime receipt, checkpoint, execution-contract, and final-state checks support reuse integrity; they do not prove semantic equivalence to every possible clean run.

## Dependency precision and metadata cost

The reference runtime uses `coarse_state` (message parents plus shared-state read-after-write edges). The `uses_only` diagnostic removes plan-to-executor edges and adds declared research uses; it is **unsafe** when a changed plan alters an executor step with no declared use. `uses_state` also adds shared-state edges.

| Graph | Mean edges/source | Mean downstream nodes/fault | Nodes missed vs runtime reference |
| --- | ---: | ---: | ---: |
| coarse | 7.96 | 4.67 | 8 |
| uses_only | 5.85 | 1.46 | 162 |
| coarse_state | 10.54 | 4.83 | 0 |
| uses_state | 8.44 | 2.33 | 120 |

Across 430 source nodes, compact UTF-8 provenance fields (`parents`, `uses`, action `reads`/`writes`) occupy 50,451 bytes (117 bytes/node); serialized checkpoints occupy 51,788 bytes. The saved, pretty-printed trace files total 3,374,511 bytes. These are offline storage measurements, not incremental live memory or network usage.

Post-hoc construction of all four graph variants and reachability queries on `macOS-27.2-arm64-arm-64bit` (100 repetitions per source; median of 48 per-source averages):

- Median: 24.5 microseconds/source; P95: 39.6 microseconds/source.

These timings exclude live provenance capture, JSON serialization, disk I/O, model latency, and recovery work. They cannot be added to or subtracted from recorded episode latency. Run the command below on the same machine to compare implementation changes.

```bash
python analyze_phase2_provenance.py --out runs/PHASE2_PROVENANCE.md
```
