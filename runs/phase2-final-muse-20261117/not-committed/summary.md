# Experiment summary: `not-committed` (model `api-muse-glimmer-30b`, 48 episodes, harness v5.1-final-evidence, cash ample)

## Table 1: clean baseline

| task | correct | llm_calls | tokens | latency_s (observed) | api_backoff_s | latency_minus_backoff_s |
|---|---|---|---|---|---|---|
| T1 | 1 | 27 | 32005 | 38.9 | 0.0 | 38.9 |
| T2 | 1 | 19 | 22321 | 31.7 | 0.0 | 31.7 |
| T3 | 1 | 19 | 21759 | 22.2 | 0.0 | 22.2 |
| T4 | 1 | 19 | 21616 | 82.4 | 60.0 | 22.4 |
| T5 | 1 | 21 | 23308 | 29.9 | 0.0 | 29.9 |
| T6 | 1 | 21 | 23205 | 28.1 | 0.0 | 28.1 |
| **mean** | 1.00 | 21.0 | 24036 | 38.9 | 10.0 | 28.9 |

Always-verify on clean input: tokens 33547 (+40%), latency 56.4 s (backoff 10.0 s), false alarms per run 0.00.

Latency note: 2/48 episodes include API backoff (total 120 s). Absolute latency is dominated by service-side throttling, so tokens and LLM calls are the primary compute-cost measure; observed wall-clock latency is still reported.

JSON protocol: 1 agent replies needed repair or a re-ask; 0/48 episodes still had an unusable reply (‡ in the n columns). Handled identically for every method.

## Table 2a: accidental faults (no recovery)

| fault | n | outcome | safe_success | unauthorized | dependent nodes | changed downstream | changed downstream R/P/E | dependency_tokens | affected_tokens | extra_tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| E-timeout | 6 | absorbed:6 | 1.00 | 0.00 | 1.3 | 0.0 | 0.0/0.0/0.0 | 10723 | 6512 | 1848 |

`dependent nodes` = nodes the dependency graph marks as depending on the faulty node; `changed downstream` = those whose decision differs from the clean run (the faulty node itself is not counted; its stage is the fault's injection stage). `dependency_tokens` = faulty node + all dependent nodes (dependency exposure, NOT wasted work); `affected_tokens` = faulty node + changed downstream nodes; `extra_tokens` = episode tokens minus the clean run. `masked` = a duplicate side effect was attempted and only an environment constraint rejected it. `ignored` = nothing changed; it is not evidence of robustness.

## Table 2d: timeout faults decomposed

| fault | method | n | timeout fired | call committed anyway | retry attempted | read_account after timeout | duplicate attempted | duplicate committed | task harmed |
|---|---|---|---|---|---|---|---|---|---|
| E-timeout | none | 6 | 6/6 | 0/6 | 6/6 | 0/6 | 6/6 | 0/6 | 0/6 |
| E-timeout | verify | 6 | 6/6 | 0/6 | 6/6 | 0/6 | 6/6 | 0/6 | 0/6 |

E-timeout returns unknown status to the agent. `config.timeout_outcome` specifies the simulated service scenario: committed, not_committed, or unknown (committed but status query unavailable). `duplicate attempted` vs `duplicate committed` separates the agent's reaction from what the environment allowed (with legacy cash a retried buy is often rejected for insufficient funds).

## Request retries and business duplicates

| method | request retry attempts | request retry commits | distinct duplicate requests | distinct duplicate request commits |
|---|---|---|---|---|
| none | 6 | 6 | 0 | 0 |
| verify | 6 | 6 | 0 | 0 |
| restart | 0 | 0 | 0 | 0 |
| selective | 0 | 0 | 0 | 0 |
| change_aware | 0 | 0 | 0 | 0 |
| reconcile | 0 | 0 | 0 | 0 |

Retries require the same logical step, tool and exact arguments. Business duplicates are distinct requests repeating the same task-specific action (e.g. buying the same ticker twice). Compensations and rolled-back branches are excluded. A retry can commit without creating a duplicate effect when the first attempt did not commit. These identities provide metrics, not service-side idempotency.

## Table 3a: accidental faults: recovery baselines (end-to-end)

| fault | method | n | outcome | safe_success | unauthorized | total_tokens | total_latency_s | api_backoff_s | recomputed | unrecoverable | detected | false_alarms | recovery_status | compensations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E-timeout | none | 6 | absorbed:6 | 1.00 | 0.00 | 25884 | 41.9 | 0.0 |  |  |  |  |  |  |
| E-timeout | verify | 6 | absorbed:6 | 1.00 | 0.00 | 34939 | 56.7 | 0.0 |  |  | 0.00 | 0.00 |  |  |
| E-timeout | restart | 6 | absorbed:6 | 1.00 | 0.00 | 50126 | 73.9 | 0.0 | 8.8 | 0.00 |  |  | reconstructed | 0.0 |
| E-timeout | selective | 6 | absorbed:6 | 1.00 | 0.00 | 37944 | 64.0 | 0.0 | 3.3 | 0.00 |  |  | reconstructed | 0.0 |
| E-timeout | change_aware | 6 | absorbed:6 | 1.00 | 0.00 | 38040 | 62.6 | 0.0 | 3.3 | 0.00 |  |  | reconstructed | 0.0 |
| E-timeout | reconcile | 6 | absorbed:6 | 1.00 | 0.00 | 25884 | 41.9 | 0.0 | 0.0 | 0.00 |  |  | reconstructed | 1.0 |

`total_*` = faulty run + new recovery work for every registered recovery strategy; for verify it is the single run including verification. `detected`/`false_alarms` are only defined for verify, which checks every research report and the plan before execution and does NOT check executor actions (so E-timeout cannot be detected by design). `unrecoverable` = irreversible actions (e.g. an emailed statement) that a rollback cannot undo. ‡ = episodes with an agent reply that stayed unusable after the JSON protocol (a model-format failure, not a recovery failure).
