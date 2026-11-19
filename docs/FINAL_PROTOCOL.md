# Final Phase 2 protocol (frozen before the live results)

## Question and scope
Can dependency-aware invalidation retain valid work and reduce recovery cost without silently accepting incorrect or unsafe outcomes? This is a controlled course experiment, not a general safety guarantee or a new fault-detection system.

- Six synthetic, stateful finance tasks; Researcher → Planner → Executor.
- Muse Glimmer 30B through the school API, orchestration and tools on Nautilus `ecepxie`.
- Fault identity is supplied equally to all recovery methods. Single injected fault per episode.
- Main matrix: 6 tasks × 8 faults × 6 methods + 12 clean controls = 300 episodes.
- E-timeout not_committed and unknown: 48 episodes each. Main total: 396.
- Repeats: T1/T6 × {R-incorrect, E-timeout, P-malicious} × all methods + clean controls, two extra repetitions (40 each). Combined with main: three observations per selected cell. These are repetitions of a small fixed benchmark, not independent samples of all agent workloads.
- Environment extension: six rating changes, one T1 price change, one irrelevant INTC news change. Each saves one faulty source, one updated clean reference, four paired recoveries (32 recovery rows + 16 control traces).
- New runs use exact integer share counts; historical runs retain their original checker and are not pooled.
- No local model execution, workflow simulation, tests or timing experiments in this release run.

## Comparison and budgets
`none` and every recovery share the identical faulty source. `verify` is a separate preventive run with the same fault injection rule. Verification checks every research report and the plan, not executor effects. Compare its prevention/inference cost, without pretending detection coverage is identical.

Same model, temperature 0, initial cash (twice task budget), API retry/timeout settings and per-agent limits across all methods. Eight turns per agent; at most one JSON re-ask; one regeneration per verifier failure. Every recovery method uses the same executor contract and bounded three-turn correction. Non-restart methods may fall back once to restart; all attempted model calls and overhead count. Model output token limit and configuration hashes are in the manifest. This is matched per-call budgets, not equal consumed compute or a globally fixed episode token cap.

## Measures
Report final correctness, historical unauthorized actions, safe_success, recovery completion, completed AND safe, direct/fallback/stopped, retained/recomputed nodes, attempted recomputation across fallback, total and recovery-only tokens, latency and API backoff. End-to-end recovery cost includes the faulty source. For absorbed faults, do not count unchanged correctness as a rescued task. For unknown commit, a fail-closed stop is not completed recovery.

Tracking measurements are scoped instrumented times: receipt/state snapshots and contract bookkeeping (excluding actual tool execution and fault sleeps), trace insertion/checkpoints, dependency queries, isolation/config identity, source and reuse validation, state restoration, candidate validation, publication. Failed attempts are included. Trace construction/serialization and filesystem write are measured separately in artifact_io.json and excluded from episode latency. These are component timings, not a causal tracing-on/off slowdown or a memory-usage benchmark; timer overhead is included. Some orchestration/diagnostic assembly remains in total latency but not the component sum.

## Environment change
The feed changes after the faulty run and is pinned during each recovery. An updated clean run is evaluator-only. Old artifacts are invalidated by versions/fingerprints; old account checkpoints do not rewind the external feed. Unknown commits stop. Irreversible external effects persist through internal state restoration. Reconcile currently falls back to restart on market drift. No claims about real broker rollback, concurrent distributed transactions, or arbitrary noncontiguous execution reuse.

## Acceptance of results
Do not tune or discard cases because a method performs poorly. Preserve all planned outcomes and failures. Only infrastructure failure justifies retrying an incomplete paired group, which replaces all methods together under explicit matching resume. Trace metrics must match result rows; source pairing, final checker, status/publication and reuse provenance must be audited. Report the small sample and no general statistical superiority claim.

## Related work and concrete design implications
- [TAMAS](https://arxiv.org/abs/2511.05269), [official code](https://github.com/microsoft/TAMAS): multi-agent attack surfaces and domain workflows motivate our adversarial cases. Our six tasks are a local finance-inspired simulator, not a reproduction of the complete TAMAS benchmark.
- [A2ASecBench](https://github.com/SaFo-Lab/A2ASecBench): protocol-aware attacks motivate separating attack surface from recovery guarantees. This project does not implement A2A networking or claim its benchmark coverage.
- [RDD lineage, NSDI 2012](https://www.usenix.org/conference/nsdi12/technical-sessions/presentation/zaharia): dependency-based reconstruction motivates invalidation/recomputation. LLM output nondeterminism and external actions require stronger validation than deterministic data lineage alone.
- [AWS: Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/): a lost response does not prove no effect; operation identity and status evidence matter. Our request IDs are status-query identifiers, not service-side idempotency keys. Unknown status remains fail-closed.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution): saved graph state does not itself undo external side effects. We retain historical safety violations and separate internal reconstruction from safe completion.

Reconcile uses deterministic delta execution instead of a full LLM executor chain. Its token advantage includes that architectural difference; it is an extension, not isolated evidence of dependency precision. Selective and change-aware vs restart are the central course comparison.

Every recovery attempt, including a discarded candidate before fallback, archives its complete node messages, actions, checkpoints and model-usage ledger. Final-token totals must equal the sum of all attempt usage entries. Development runs made before this archival fix are excluded from the final cohort.
