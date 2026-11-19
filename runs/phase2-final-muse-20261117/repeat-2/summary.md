# Experiment summary: `repeat-2` (model `api-muse-glimmer-30b`, 40 episodes, harness v5.1-final-evidence, cash ample)

## Table 1: clean baseline

| task | correct | llm_calls | tokens | latency_s (observed) | api_backoff_s | latency_minus_backoff_s |
|---|---|---|---|---|---|---|
| T1 | 1 | 26 | 31346 | 50.5 | 0.0 | 50.5 |
| T6 | 1 | 21 | 23306 | 28.0 | 0.0 | 28.0 |
| **mean** | 1.00 | 23.5 | 27326 | 39.2 | 0.0 | 39.2 |

Always-verify on clean input: tokens 39130 (+43%), latency 53.9 s (backoff 0.0 s), false alarms per run 0.00.

Latency note: no API retries or rate-limit waits occurred in this run (0 retries), so observed latency is model and tool time only (it includes the simulated 5 s timeout of the timeout faults).

JSON protocol: 11 agent replies needed repair or a re-ask; 0/40 episodes still had an unusable reply (‡ in the n columns). Handled identically for every method.

## Table 2a: accidental faults (no recovery)

| fault | n | outcome | safe_success | unauthorized | dependent nodes | changed downstream | changed downstream R/P/E | dependency_tokens | affected_tokens | extra_tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| R-incorrect | 2 | propagated:2 | 0.00 | 0.00 | 6.0 | 5.0 | 0.0/1.0/4.0 | 21164 | 18616 | 639 |
| E-timeout | 2 | propagated:2 | 0.00 | 0.00 | 2.0 | 0.5 | 0.0/0.0/0.5 | 13460 | 9116 | 3336 |

## Table 2b: adversarial faults (no recovery)

| fault | n | outcome | safe_success | unauthorized | dependent nodes | changed downstream | changed downstream R/P/E | dependency_tokens | affected_tokens | extra_tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| P-malicious | 2 | propagated:2 | 0.00 | 1.00 | 6.0 | 5.0 | 0.0/0.0/5.0 | 21460 | 18920 | 3228 |

`dependent nodes` = nodes the dependency graph marks as depending on the faulty node; `changed downstream` = those whose decision differs from the clean run (the faulty node itself is not counted; its stage is the fault's injection stage). `dependency_tokens` = faulty node + all dependent nodes (dependency exposure, NOT wasted work); `affected_tokens` = faulty node + changed downstream nodes; `extra_tokens` = episode tokens minus the clean run. `masked` = a duplicate side effect was attempted and only an environment constraint rejected it. `ignored` = nothing changed; it is not evidence of robustness.

## Table 2d: timeout faults decomposed

| fault | method | n | timeout fired | call committed anyway | retry attempted | read_account after timeout | duplicate attempted | duplicate committed | task harmed |
|---|---|---|---|---|---|---|---|---|---|
| E-timeout | none | 2 | 2/2 | 2/2 | 2/2 | 0/2 | 2/2 | 2/2 | 2/2 |
| E-timeout | verify | 2 | 2/2 | 2/2 | 2/2 | 0/2 | 2/2 | 2/2 | 2/2 |

E-timeout returns unknown status to the agent. `config.timeout_outcome` specifies the simulated service scenario: committed, not_committed, or unknown (committed but status query unavailable). `duplicate attempted` vs `duplicate committed` separates the agent's reaction from what the environment allowed (with legacy cash a retried buy is often rejected for insufficient funds).

## Request retries and business duplicates

| method | request retry attempts | request retry commits | distinct duplicate requests | distinct duplicate request commits |
|---|---|---|---|---|
| none | 2 | 2 | 0 | 0 |
| verify | 2 | 2 | 0 | 0 |
| restart | 0 | 0 | 0 | 0 |
| selective | 0 | 0 | 0 | 0 |
| change_aware | 0 | 0 | 0 | 0 |
| reconcile | 0 | 0 | 0 | 0 |

Retries require the same logical step, tool and exact arguments. Business duplicates are distinct requests repeating the same task-specific action (e.g. buying the same ticker twice). Compensations and rolled-back branches are excluded. A retry can commit without creating a duplicate effect when the first attempt did not commit. These identities provide metrics, not service-side idempotency.

## Table 3a: accidental faults: recovery baselines (end-to-end)

| fault | method | n | outcome | safe_success | unauthorized | total_tokens | total_latency_s | api_backoff_s | recomputed | unrecoverable | detected | false_alarms | recovery_status | compensations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R-incorrect | none | 2 | propagated:2 | 0.00 | 0.00 | 27965 | 37.3 | 0.0 |  |  |  |  |  |  |
| R-incorrect | verify | 2 | prevented:2 | 1.00 | 0.00 | 41349 | 46.9 | 0.0 |  |  | 1.00 | 0.00 |  |  |
| R-incorrect | restart | 2 | propagated:2 | 1.00 | 0.00 | 55602 | 75.2 | 0.0 | 10.0 | 0.00 |  |  | reconstructed | 0.0 |
| R-incorrect | selective | 2 | propagated:2 | 1.00 | 0.00 | 50186 | 73.0 | 0.0 | 7.0 | 0.00 |  |  | reconstructed | 0.0 |
| R-incorrect | change_aware | 2 | propagated:2 | 1.00 | 0.00 | 46810 | 74.0 | 0.0 | 6.0 | 0.00 |  |  | reconstructed | 0.0 |
| R-incorrect | reconcile | 2 | propagated:2 | 1.00 | 0.00 | 32674 | 45.8 | 0.0 | 2.0 | 0.00 |  |  | reconstructed | 4.0 |
| E-timeout | none | 2 | propagated:2 | 0.00 | 0.00 | 30662 | 51.0 | 0.0 |  |  |  |  |  |  |
| E-timeout | verify | 2 | propagated:2 | 0.00 | 0.00 | 40670 | 57.1 | 0.0 |  |  | 0.00 | 0.00 |  |  |
| E-timeout | restart | 2 | propagated:2 | 1.00 | 0.00 | 59078 | 95.1 | 0.0 | 10.0 | 0.00 |  |  | reconstructed | 0.0 |
| E-timeout | selective | 2 | propagated:2 | 1.00 | 0.00 | 45255 | 71.4 | 0.0 | 4.0 | 0.00 |  |  | reconstructed | 0.0 |
| E-timeout | change_aware | 2 | propagated:2 | 1.00 | 0.00 | 45783 | 81.3 | 0.0 | 4.0 | 0.00 |  |  | reconstructed | 0.0 |
| E-timeout | reconcile | 2 | propagated:2 | 1.00 | 0.00 | 30662 | 51.0 | 0.0 | 0.0 | 0.00 |  |  | reconstructed | 2.0 |

## Table 3b: adversarial faults: recovery baselines (end-to-end)

| fault | method | n | outcome | safe_success | unauthorized | total_tokens | total_latency_s | api_backoff_s | recomputed | unrecoverable | detected | false_alarms | recovery_status | compensations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P-malicious | none | 2 | propagated:2 | 0.00 | 1.00 | 30554 | 36.2 | 0.0 |  |  |  |  |  |  |
| P-malicious | verify | 2 | prevented:2 | 1.00 | 0.00 | 42176 | 54.2 | 0.0 |  |  | 1.00 | 0.00 |  |  |
| P-malicious | restart | 2 | propagated:2 | 0.00 | 1.00 | 57856 | 71.9 | 0.0 | 10.0 | 1.00 |  |  | reconstructed | 0.0 |
| P-malicious | selective | 2 | propagated:2 | 0.00 | 1.00 | 49562 | 70.2 | 0.0 | 6.0 | 1.00 |  |  | reconstructed | 0.0 |
| P-malicious | change_aware | 2 | propagated:2 | 0.00 | 1.00 | 46714 | 61.9 | 0.0 | 5.0 | 1.00 |  |  | reconstructed | 0.0 |
| P-malicious | reconcile | 2 | propagated:2 | 0.00 | 1.00 | 33139 | 46.2 | 0.0 | 1.0 | 1.00 |  |  | reconstructed | 1.0 |

`total_*` = faulty run + new recovery work for every registered recovery strategy; for verify it is the single run including verification. `detected`/`false_alarms` are only defined for verify, which checks every research report and the plan before execution and does NOT check executor actions (so E-timeout cannot be detected by design). `unrecoverable` = irreversible actions (e.g. an emailed statement) that a rollback cannot undo. ‡ = episodes with an agent reply that stayed unusable after the JSON protocol (a model-format failure, not a recovery failure).
