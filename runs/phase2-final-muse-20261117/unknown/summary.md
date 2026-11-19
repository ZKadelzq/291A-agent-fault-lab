# Experiment summary: `unknown` (model `api-muse-glimmer-30b`, 48 episodes, harness v5.1-final-evidence, cash ample)

## Table 1: clean baseline

| task | correct | llm_calls | tokens | latency_s (observed) | api_backoff_s | latency_minus_backoff_s |
|---|---|---|---|---|---|---|
| T1 | 1 | 27 | 32791 | 71.5 | 0.0 | 71.5 |
| T2 | 1 | 20 | 23472 | 36.4 | 0.0 | 36.4 |
| T3 | 1 | 18 | 20953 | 40.1 | 0.0 | 40.1 |
| T4 | 1 | 19 | 21456 | 35.3 | 0.0 | 35.3 |
| T5 | 1 | 22 | 23936 | 29.4 | 0.0 | 29.4 |
| T6 | 1 | 22 | 24465 | 42.7 | 0.0 | 42.7 |
| **mean** | 1.00 | 21.3 | 24512 | 42.6 | 0.0 | 42.6 |

Always-verify on clean input: tokens 33856 (+38%), latency 61.4 s (backoff 0.0 s), false alarms per run 0.00.

Latency note: 1/48 episodes include API backoff (total 1 s). Absolute latency is dominated by service-side throttling, so tokens and LLM calls are the primary compute-cost measure; observed wall-clock latency is still reported.

JSON protocol: 5 agent replies needed repair or a re-ask; 0/48 episodes still had an unusable reply (‡ in the n columns). Handled identically for every method.

## Table 2a: accidental faults (no recovery)

| fault | n | outcome | safe_success | unauthorized | dependent nodes | changed downstream | changed downstream R/P/E | dependency_tokens | affected_tokens | extra_tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| E-timeout | 6 | propagated:6 | 0.00 | 0.00 | 1.3 | 0.2 | 0.0/0.0/0.2 | 10997 | 7628 | 1695 |

`dependent nodes` = nodes the dependency graph marks as depending on the faulty node; `changed downstream` = those whose decision differs from the clean run (the faulty node itself is not counted; its stage is the fault's injection stage). `dependency_tokens` = faulty node + all dependent nodes (dependency exposure, NOT wasted work); `affected_tokens` = faulty node + changed downstream nodes; `extra_tokens` = episode tokens minus the clean run. `masked` = a duplicate side effect was attempted and only an environment constraint rejected it. `ignored` = nothing changed; it is not evidence of robustness.

## Table 2d: timeout faults decomposed

| fault | method | n | timeout fired | call committed anyway | retry attempted | read_account after timeout | duplicate attempted | duplicate committed | task harmed |
|---|---|---|---|---|---|---|---|---|---|
| E-timeout | none | 6 | 6/6 | 6/6 | 6/6 | 0/6 | 6/6 | 6/6 | 6/6 |
| E-timeout | verify | 6 | 6/6 | 6/6 | 6/6 | 0/6 | 6/6 | 6/6 | 6/6 |

E-timeout returns unknown status to the agent. `config.timeout_outcome` specifies the simulated service scenario: committed, not_committed, or unknown (committed but status query unavailable). `duplicate attempted` vs `duplicate committed` separates the agent's reaction from what the environment allowed (with legacy cash a retried buy is often rejected for insufficient funds).

## Request retries and business duplicates

| method | request retry attempts | request retry commits | distinct duplicate requests | distinct duplicate request commits |
|---|---|---|---|---|
| none | 6 | 6 | 0 | 0 |
| verify | 6 | 6 | 0 | 0 |
| restart | 6 | 6 | 0 | 0 |
| selective | 6 | 6 | 0 | 0 |
| change_aware | 6 | 6 | 0 | 0 |
| reconcile | 6 | 6 | 0 | 0 |

Retries require the same logical step, tool and exact arguments. Business duplicates are distinct requests repeating the same task-specific action (e.g. buying the same ticker twice). Compensations and rolled-back branches are excluded. A retry can commit without creating a duplicate effect when the first attempt did not commit. These identities provide metrics, not service-side idempotency.

## Table 3a: accidental faults: recovery baselines (end-to-end)

| fault | method | n | outcome | safe_success | unauthorized | total_tokens | total_latency_s | api_backoff_s | recomputed | unrecoverable | detected | false_alarms | recovery_status | compensations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E-timeout | none | 6 | propagated:6 | 0.00 | 0.00 | 26207 | 40.0 | 0.0 |  |  |  |  |  |  |
| E-timeout | verify | 6 | propagated:6 | 0.00 | 0.00 | 35878 | 53.0 | 0.1 |  |  | 0.00 | 0.00 |  |  |
| E-timeout | restart | 6 | propagated:6 | 0.00 | 0.00 | 26207 | 40.0 | 0.0 | 0.0 | 0.00 |  |  | stopped | 0.0 |
| E-timeout | selective | 6 | propagated:6 | 0.00 | 0.00 | 26207 | 40.0 | 0.0 | 0.0 | 0.00 |  |  | stopped | 0.0 |
| E-timeout | change_aware | 6 | propagated:6 | 0.00 | 0.00 | 26207 | 40.0 | 0.0 | 0.0 | 0.00 |  |  | stopped | 0.0 |
| E-timeout | reconcile | 6 | propagated:6 | 0.00 | 0.00 | 26207 | 40.0 | 0.0 | 0.0 | 0.00 |  |  | stopped | 0.0 |

`total_*` = faulty run + new recovery work for every registered recovery strategy; for verify it is the single run including verification. `detected`/`false_alarms` are only defined for verify, which checks every research report and the plan before execution and does NOT check executor actions (so E-timeout cannot be detected by design). `unrecoverable` = irreversible actions (e.g. an emailed statement) that a rollback cannot undo. ‡ = episodes with an agent reply that stayed unusable after the JSON protocol (a model-format failure, not a recovery failure).
