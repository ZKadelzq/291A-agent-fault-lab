"""Shared analysis helpers for summarize.py and figures.py.

Everything here is *derived* from results.jsonl / trace.json, so it also works on runs made before
these metrics existed (e.g. the first Qwen run). Derived fields:

  total_tokens / total_latency  end-to-end cost of an episode. For recovery strategies this is the faulty run
                                PLUS newly incurred recovery work, including failed attempts.
  total_backoff / total_latency_net  the same for API backoff time and latency minus backoff (0 / = latency in
                                runs made before v2, which did not record backoff).
  extra_tokens                  total_tokens minus the clean run of the same task (clean `verify` for verify rows,
                                clean `none` otherwise): the extra compute the fault (+ recovery) cost.
  dependency_tokens             = old `wasted_tokens` (renamed: it is dependency exposure, not wasted work).
  outcome2                      outcome with one extra value, `prevented`: the verifier caught the fault and the
                                node was regenerated before the fault spread. (Raw `ignored` on a verify row would
                                wrongly read as "the workload never depended on it".)
  aff_research/plan/exec        `affected_nodes` of the faulty run, counted per stage.
"""
import json
import os
from collections import Counter, defaultdict
from mas.recovery import METHODS

ACCIDENTAL = ["R-incorrect", "R-stale", "R-timeout", "E-timeout"]
CONTROL = ["R-distractor"]  # negative control: same fault as R-incorrect on a report the decision does not use
ADVERSARIAL = ["R-malicious", "R-ipi", "P-malicious"]
FAULT_ORDER = ACCIDENTAL + CONTROL + ADVERSARIAL
METHOD_ORDER = ['none', 'verify', *METHODS]
OUTCOMES = ["propagated", "masked", "absorbed", "prevented", "ignored"]  # stack order (see figures.py colours)


def load(exp):
    """Rows of runs/<exp>/results.jsonl with derived fields added."""
    rows = [json.loads(l) for l in open(os.path.join(exp, "results.jsonl"))]
    none = {(r["task"], r["fault"]): r for r in rows if r["method"] == "none"}
    clean = {(r["task"], r["method"]): r for r in rows if r["fault"] == "clean"}
    for r in rows:
        r.setdefault("backoff_s", 0.0)
        r.setdefault("latency_net", r["latency"] - r["backoff_s"])
        if "dependency_tokens" not in r and "wasted_tokens" in r:
            r["dependency_tokens"] = r["wasted_tokens"]
        base = none.get((r["task"], r["fault"]))
        if (r['method'] in METHODS or 'recovery_status' in r) and base:
            r["total_tokens"] = base["tokens"] + r["tokens"]
            r["total_latency"] = base["latency"] + r["latency"]
            r["total_backoff"] = base.get("backoff_s", 0.0) + r["backoff_s"]
            r["outcome2"] = r.get("fault_outcome")
        else:
            r["total_tokens"], r["total_latency"], r["total_backoff"] = r["tokens"], r["latency"], r["backoff_s"]
            r["outcome2"] = r.get("outcome")
        r["total_latency_net"] = r["total_latency"] - r["total_backoff"]
        ref = clean.get((r["task"], "verify" if r["method"] == "verify" else "none"))
        r["extra_tokens"] = r["total_tokens"] - ref["tokens"] if ref and r["fault"] != "clean" else None
        if r["method"] == "verify" and r.get("detected") and r.get("safe_success"):
            r["outcome2"] = "prevented"
        for st in ("research", "plan", "exec"):
            r[f"aff_{st}"] = sum(1 for n in (r.get("affected_nodes") or []) if n.split(":")[0] == st)
    return rows


def faults_in(rows):
    seen = {r["fault"] for r in rows if r["fault"] != "clean"}
    return [f for f in FAULT_ORDER if f in seen] + sorted(seen - set(FAULT_ORDER))


def category(fault):
    return "adversarial" if fault in ADVERSARIAL else "control" if fault in CONTROL else "accidental"


def mean(vals):
    vals = [float(v) for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def outcome_counts(rows):
    return Counter(r["outcome2"] or "-" for r in rows)


def group(rows, *keys):
    g = defaultdict(list)
    for r in rows:
        g[tuple(r[k] for k in keys)].append(r)
    return g


# ---- dependency sets (Phase 2 preview): how many nodes would each tracking granularity invalidate? ----

def invalidation_sets(trace_json, target):
    """Nodes recovery would have to recompute if `target` is faulty, under three bookkeeping levels:
      coarse  message edges only (research -> plan -> every exec step)
      state   message + shared-state read-after-write edges
      fine    declared `uses` + shared-state edges (an exec step is invalid only if its plan step used the
              faulty report, or it reads state written by an invalid step)
    Returns {name: set(node ids)} (target itself excluded)."""
    nodes = {n["id"]: n for n in trace_json["nodes"]}
    all_edges = {tuple(e) for e in trace_json["edges"]}
    msg = {(p, n["id"]) for n in nodes.values() for p in n["parents"] if p in nodes}
    st_edges = all_edges - msg

    def reach(edges):
        kids = defaultdict(set)
        for s, d in edges:
            kids[s].add(d)
        seen, stack = set(), [target]
        while stack:
            for c in kids.get(stack.pop(), ()):
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return seen

    coarse, state = reach(msg), reach(all_edges)
    # fine: drop message edges plan -> exec_i unless the step declared it uses the faulty report (or the plan itself is faulty)
    fine_msg = set()
    for s, d in msg:
        if nodes[d]["agent"] == "executor" and s == "plan" and target != "plan":
            if target in (nodes[d].get("uses") or []):
                fine_msg.add((s, d))
        else:
            fine_msg.add((s, d))
    fine = reach(fine_msg | st_edges)
    return {"coarse": coarse, "state": state, "fine": fine}


def load_trace(exp, task, fault, method="none"):
    p = os.path.join(exp, task, fault, method, "trace.json")
    return json.load(open(p)) if os.path.exists(p) else None
