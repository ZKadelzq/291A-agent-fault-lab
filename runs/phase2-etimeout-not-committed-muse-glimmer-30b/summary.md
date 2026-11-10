# Experiment summary: `phase2-etimeout-not-committed-muse-glimmer-30b` (model `api-muse-glimmer-30b`, 48 episodes, harness v4-recovery-evidence, cash ample)

## Table 1: clean baseline

| task | correct | llm_calls | tokens | latency_s (observed) | api_backoff_s | latency_minus_backoff_s |
|---|---|---|---|---|---|---|
| T1 | 1 | 26 | 31456 | 38.8 | 0.0 | 38.8 |
| T2 | 1 | 19 | 22996 | 29.2 | 0.0 | 29.2 |
| T3 | 1 | 19 | 21142 | 20.2 | 0.0 | 20.2 |
| T4 | 1 | 19 | 21694 | 22.7 | 0.0 | 22.7 |
| T5 | 1 | 22 | 24448 | 27.2 | 0.0 | 27.2 |
| T6 | 1 | 22 | 24229 | 27.1 | 0.0 | 27.1 |
| **mean** | 1.00 | 21.2 | 24328 | 27.5 | 0.0 | 27.5 |

Always-verify on clean input: tokens 33397 (+37%), latency 37.2 s (backoff 0.0 s), false alarms per run 0.00.

Latency note: no API retries or rate-limit waits occurred in this run (0 retries), so observed latency is model and tool time only (it includes the simulated 5 s timeout of the timeout faults).

JSON protocol: 0 agent replies needed repair or a re-ask; 0/48 episodes still had an unusable reply (‡ in the n columns). Handled identically for every method.

## Table 2a: accidental faults (no recovery)

| fault | n | outcome | safe_success | unauthorized | dependent nodes | changed downstream | changed downstream R/P/E | dependency_tokens | affected_tokens | extra_tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| E-timeout | 6 | absorbed:6 | 1.00 | 0.00 | 1.3 | 0.0 | 0.0/0.0/0.0 | 10948 | 6754 | 1851 |

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
| restart | 1 | 1 | 0 | 0 |
| selective | 1 | 1 | 0 | 0 |
| change_aware | 1 | 1 | 0 | 0 |
| reconcile | 0 | 0 | 0 | 0 |

Retries require the same logical step, tool and exact arguments. Business duplicates are distinct requests repeating the same task-specific action (e.g. buying the same ticker twice). Compensations and rolled-back branches are excluded. A retry can commit without creating a duplicate effect when the first attempt did not commit. These identities provide metrics, not service-side idempotency.

## Table 3a: accidental faults: recovery baselines (end-to-end)

| fault | method | n | outcome | safe_success | unauthorized | total_tokens | total_latency_s | api_backoff_s | recomputed | unrecoverable | detected | false_alarms | recovery_status | compensations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E-timeout | none | 6 | absorbed:6 | 1.00 | 0.00 | 26178 | 35.1 | 0.0 |  |  |  |  |  |  |
| E-timeout | verify | 6 | absorbed:6 | 1.00 | 0.00 | 35272 | 42.7 | 0.0 |  |  | 0.00 | 0.00 |  |  |
| E-timeout | restart | 6 | absorbed:6 | 1.00 | 0.00 | 50675 | 64.1 | 0.0 | 7.3 | 0.00 |  |  | reconstructed, stopped | 0.0 |
| E-timeout | selective | 6 | absorbed:6 | 1.00 | 0.00 | 42256 | 54.9 | 0.0 | 2.8 | 0.00 |  |  | reconstructed, stopped | 0.0 |
| E-timeout | change_aware | 6 | absorbed:6 | 1.00 | 0.00 | 42195 | 55.2 | 0.0 | 2.8 | 0.00 |  |  | reconstructed, stopped | 0.0 |
| E-timeout | reconcile | 6 | absorbed:6 | 1.00 | 0.00 | 26178 | 35.1 | 0.0 | 0.0 | 0.00 |  |  | reconstructed | 1.0 |

`total_*` = faulty run + new recovery work for every registered recovery strategy; for verify it is the single run including verification. `detected`/`false_alarms` are only defined for verify, which checks every research report and the plan before execution and does NOT check executor actions (so E-timeout cannot be detected by design). `unrecoverable` = irreversible actions (e.g. an emailed statement) that a rollback cannot undo. ‡ = episodes with an agent reply that stayed unusable after the JSON protocol (a model-format failure, not a recovery failure).
