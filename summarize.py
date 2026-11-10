"""Aggregate runs/<exp>/results.jsonl into summary.md (Phase 1 tables).

  python summarize.py runs/<exp>

Tables (match OUTLINE.md section 5/6):
  Table 1    clean baseline per task (+ verify on clean input = false-alarm cost); observed latency AND backoff
  Table 2a   propagation, ACCIDENTAL faults   (method = none)
  Table 2b   propagation, ADVERSARIAL faults  (method = none)
  Table 2c   relevance control: R-incorrect vs R-distractor (only if R-distractor was run)
  Table 2d   timeout faults decomposed: did the agent retry, did the duplicate commit, was the task harmed
  Table 3a/b recovery baselines per fault: none vs restart vs verify, END-TO-END cost (fault run + recovery)
`python rescore.py runs/<exp>` recomputes results.jsonl from the saved traces if the metrics change.
"""
import os
import sys

from analysis import (ACCIDENTAL, ADVERSARIAL, CONTROL, METHOD_ORDER, OUTCOMES, faults_in, group, load, mean,
                      outcome_counts)

STAGES = ("research", "plan", "exec")


def f(v, nd=2):
    return "" if v is None else f"{v:.{nd}f}"


def frac(rs, key):
    """'k/n' over the rows that define `key`."""
    vals = [r[key] for r in rs if r.get(key) is not None]
    return f"{sum(bool(v) for v in vals)}/{len(vals)}" if vals else ""


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def fmt_outcomes(rs):
    c = outcome_counts(rs)
    return ", ".join(f"{k}:{c[k]}" for k in OUTCOMES + ["not_fired", "clean_failed", "-"] if c.get(k))


def n_cell(rs):
    """n, with a ‡ count of episodes where an agent reply stayed unusable after the JSON protocol."""
    bad = sum(1 for r in rs if r.get("format_failed"))
    return f"{len(rs)} ({bad}‡)" if bad else str(len(rs))


def propagation_row(fault, rs):
    return [fault, n_cell(rs), fmt_outcomes(rs), f(mean(r["safe_success"] for r in rs)),
            f(mean(r["unauthorized"] for r in rs)), f(mean(r.get("downstream") for r in rs), 1),
            f(mean(r.get("affected") for r in rs), 1),
            "/".join(f(mean(r[f"aff_{s}"] for r in rs), 1) for s in STAGES),
            f(mean(r.get("dependency_tokens") for r in rs), 0), f(mean(r.get("affected_tokens") for r in rs), 0),
            f(mean(r.get("extra_tokens") for r in rs), 0)]


PROP_HEADER = ["fault", "n", "outcome", "safe_success", "unauthorized", "dependent nodes", "changed downstream",
               "changed downstream R/P/E", "dependency_tokens", "affected_tokens", "extra_tokens"]


def main(exp):
    rows = load(exp)
    model = rows[0].get("model", "unknown (run predates the model field)")
    modes = sorted({r.get("cash_mode", "legacy") for r in rows})
    harness = sorted({r.get("harness", "v1") for r in rows})
    parts = [f"# Experiment summary: `{os.path.basename(os.path.normpath(exp))}` (model `{model}`, {len(rows)} episodes, "
             f"harness {'/'.join(harness)}, cash {'/'.join(modes)})\n"]
    if len(modes) > 1:
        parts.append("**Warning:** this folder mixes cash modes; compare like with like.\n")

    # Table 1: clean baseline
    clean = [r for r in rows if r["fault"] == "clean"]
    all_none = [r for r in clean if r["method"] == "none"]
    t1 = []
    for task, rs in sorted(group(all_none, "task").items()):
        r = rs[0]
        t1.append([task[0], int(r["correct"]), r["llm_calls"], r["tokens"], f(r["latency"], 1),
                   f(r["backoff_s"], 1), f(r["latency_net"], 1)])
    t1.append(["**mean**", f(mean(r["correct"] for r in all_none)), f(mean(r["llm_calls"] for r in all_none), 1),
               f(mean(r["tokens"] for r in all_none), 0), f(mean(r["latency"] for r in all_none), 1),
               f(mean(r["backoff_s"] for r in all_none), 1), f(mean(r["latency_net"] for r in all_none), 1)])
    parts.append("## Table 1: clean baseline\n\n" + table(
        ["task", "correct", "llm_calls", "tokens", "latency_s (observed)", "api_backoff_s", "latency_minus_backoff_s"],
        t1))
    v = [r for r in clean if r["method"] == "verify"]
    if v:
        parts.append(f"\nAlways-verify on clean input: tokens {f(mean(r['tokens'] for r in v), 0)} "
                     f"(+{f(100 * (mean(r['tokens'] for r in v) / mean(r['tokens'] for r in all_none) - 1), 0)}%), "
                     f"latency {f(mean(r['latency'] for r in v), 1)} s (backoff {f(mean(r['backoff_s'] for r in v), 1)} s), "
                     f"false alarms per run {f(mean(r.get('false_alarms') for r in v))}.")
    n_bad = sum(1 for r in all_none if not r["correct"])
    if n_bad:
        parts.append(f"\n**Warning:** the clean run failed on {n_bad} task(s); fault outcomes there are `clean_failed`.")
    if model == 'deterministic-test-double':
        parts.append('\nDeterministic model double: token counts are synthetic and latency is local test time. '
                     'These results test mechanisms, not real LLM performance.')
    elif not any(r.get("api_s") for r in rows):
        parts.append("\nLatency note: this run predates backoff instrumentation, so `api_backoff_s` is 0 and observed "
                     "latency includes rate-limit waits. Use tokens / LLM calls as the primary compute-cost measure.")
    elif not any(r["backoff_s"] for r in rows):
        parts.append(f"\nLatency note: no API retries or rate-limit waits occurred in this run "
                     f"({sum(r.get('api_retries') or 0 for r in rows)} retries), so observed latency is model and tool "
                     "time only (it includes the simulated 5 s timeout of the timeout faults).")
    else:
        slow = sum(1 for r in rows if r["backoff_s"] > 0)
        parts.append(f"\nLatency note: {slow}/{len(rows)} episodes include API backoff "
                     f"(total {sum(r['backoff_s'] for r in rows):.0f} s). Absolute latency is dominated by "
                     "service-side throttling, so tokens and LLM calls are the primary compute-cost measure; observed "
                     "wall-clock latency is still reported.")
    fe = sum(r.get("format_errors") or 0 for r in rows)
    ff = sum(1 for r in rows if r.get("format_failed"))
    parts.append(f"\nJSON protocol: {fe} agent replies needed repair or a re-ask; {ff}/{len(rows)} episodes still had "
                 "an unusable reply (‡ in the n columns). Handled identically for every method.")

    # Table 2: propagation
    for title, names in (("2a: accidental faults", ACCIDENTAL), ("2b: adversarial faults", ADVERSARIAL)):
        present = [x for x in faults_in(rows) if x in names]
        if present:
            t = [propagation_row(x, [r for r in rows if r["fault"] == x and r["method"] == "none"]) for x in present]
            parts.append(f"\n## Table {title} (no recovery)\n\n" + table(PROP_HEADER, t))
    if any(r["fault"] in CONTROL for r in rows):
        tasks = {r["task"] for r in rows if r["fault"] in CONTROL and r["method"] == "none"}
        t = [propagation_row(f"{x} ({'relevant' if x == 'R-incorrect' else 'irrelevant'} report)",
                             [r for r in rows if r["fault"] == x and r["method"] == "none" and r["task"] in tasks])
             for x in ["R-incorrect"] + CONTROL]
        parts.append("\n## Table 2c: relevance control (same wrong-rating fault, relevant vs. irrelevant report)\n\n"
                     + table(PROP_HEADER, t) + "\n\nA fault propagates only when it enters a dependency the downstream "
                     "decision actually uses; injecting it elsewhere changes nothing even though the dependency graph "
                     "marks the same downstream nodes.")
    parts.append("\n`dependent nodes` = nodes the dependency graph marks as depending on the faulty node; `changed "
                 "downstream` = those whose decision differs from the clean run (the faulty node itself is not counted; "
                 "its stage is the fault's injection stage). `dependency_tokens` = faulty node + all dependent nodes "
                 "(dependency exposure, NOT wasted work); `affected_tokens` = faulty node + changed downstream nodes; "
                 "`extra_tokens` = episode tokens minus the clean run. `masked` = a duplicate side effect was attempted "
                 "and only an environment constraint rejected it. `ignored` = nothing changed; it is not evidence of "
                 "robustness.")

    # Table 2d: timeouts decomposed
    tr = []
    for fault in [x for x in faults_in(rows) if x in ("R-timeout", "E-timeout")]:
        for m in ("none", "verify"):
            rs = [r for r in rows if r["fault"] == fault and r["method"] == m]
            if not rs:
                continue
            fired = [r for r in rs if r.get("outcome") != "not_fired"]
            tr.append([fault, m, n_cell(rs), f"{len(fired)}/{len(rs)}", frac(fired, "timeout_committed"),
                       frac(fired, "retry_attempted"), frac(fired, "state_checked"),
                       f"{sum(1 for r in fired if r.get('dup_attempted'))}/{len(fired)}",
                       f"{sum(1 for r in fired if r.get('dup_committed'))}/{len(fired)}",
                       f"{sum(1 for r in fired if not r['safe_success'])}/{len(fired)}"])
    if tr:
        parts.append("\n## Table 2d: timeout faults decomposed\n\n" + table(
            ["fault", "method", "n", "timeout fired", "call committed anyway", "retry attempted",
             "read_account after timeout", "duplicate attempted", "duplicate committed", "task harmed"], tr)
            + "\n\nE-timeout returns unknown status to the agent. `config.timeout_outcome` specifies the simulated "
              "service scenario: committed, not_committed, or unknown (committed but status query unavailable). "
              "`duplicate attempted` vs `duplicate committed` separates the agent's reaction from what the "
              "environment allowed (with legacy cash a retried buy is often rejected for insufficient funds).")

    if any('request_retry_attempted' in r for r in rows):
        detail = []
        for method in METHOD_ORDER:
            rs = [r for r in rows if r['method'] == method and r['fault'] != 'clean']
            if rs:
                detail.append([method] + [sum(r.get(key, 0) for r in rs) for key in
                    ('request_retry_attempted', 'request_retry_committed', 'business_dup_attempted',
                     'business_dup_committed')])
        parts.append('\n## Request retries and business duplicates\n\n' + table(
            ['method', 'request retry attempts', 'request retry commits',
             'distinct duplicate requests', 'distinct duplicate request commits'], detail)
            + '\n\nRetries require the same logical step, tool and exact arguments. Business duplicates are '
              'distinct requests repeating the same task-specific action (e.g. buying the same ticker twice). '
              'Compensations and rolled-back branches are excluded. A retry can commit without creating a '
              'duplicate effect when the first attempt did not commit. These identities provide metrics, '
              'not service-side idempotency.')

    # Table 3: recovery baselines, end-to-end
    for title, names in (("3a: accidental faults", ACCIDENTAL + CONTROL), ("3b: adversarial faults", ADVERSARIAL)):
        present = [x for x in faults_in(rows) if x in names]
        t = []
        for fault in present:
            for m in METHOD_ORDER:
                rs = [r for r in rows if r["fault"] == fault and r["method"] == m]
                if not rs:
                    continue
                t.append([fault, m, n_cell(rs), fmt_outcomes(rs), f(mean(r["safe_success"] for r in rs)),
                          f(mean(r["unauthorized"] for r in rs)), f(mean(r["total_tokens"] for r in rs), 0),
                          f(mean(r["total_latency"] for r in rs), 1), f(mean(r["total_backoff"] for r in rs), 1),
                          f(mean(r.get("recomputed") for r in rs), 1), f(mean(r.get("unrecoverable") for r in rs)),
                          f(mean(r.get("detected") for r in rs)), f(mean(r.get("false_alarms") for r in rs)),
                          ', '.join(sorted({r['recovery_status'] for r in rs if 'recovery_status' in r})),
                          f(mean(r.get('compensations') for r in rs), 1)])
        if t:
            parts.append(f"\n## Table {title}: recovery baselines (end-to-end)\n\n" + table(
                ["fault", "method", "n", "outcome", "safe_success", "unauthorized", "total_tokens", "total_latency_s",
                 "api_backoff_s", "recomputed", "unrecoverable", "detected", "false_alarms",
                 'recovery_status', 'compensations'], t))
    parts.append("\n`total_*` = faulty run + new recovery work for every registered recovery strategy; for verify it is the "
                 "single run including verification. `detected`/`false_alarms` are only defined for verify, which "
                 "checks every research report and the plan before execution and does NOT check executor actions "
                 "(so E-timeout cannot be detected by design). `unrecoverable` = irreversible actions (e.g. an emailed "
                 "statement) that a rollback cannot undo. ‡ = episodes with an agent reply that stayed unusable after "
                 "the JSON protocol (a model-format failure, not a recovery failure).")

    text = "\n".join(parts) + "\n"
    print(text)
    with open(os.path.join(exp, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    main(sys.argv[1])
