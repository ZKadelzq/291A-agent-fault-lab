"""Re-score saved episodes with the CURRENT metrics and task checker. No LLM calls.

  python rescore.py runs/phase1-api-muse-glimmer-30b [more run dirs ...]
  python summarize.py runs/phase1-api-muse-glimmer-30b

Recomputes every row of results.jsonl from the saved trace.json files (trace, final state, action log) with the
current metrics and task checker, so a metric change can be applied to an existing run without new LLM calls.
The rows as they were before the first re-score are kept in results.before_rescore.jsonl, which is always the input,
so re-scoring is repeatable. Agent behaviour (what is in the traces) is never changed.
"""
import json
import os
import shutil
import sys

from mas import tasks as tasks_mod
from mas.faults import schedule
from mas.metrics import episode
from mas.tasks import TASKS_BY_ID
from mas.trace import Node, Trace
from mas.recovery import METHODS

NODE_FIELDS = set(Node.__dataclass_fields__)


class SavedWorld:
    """Just enough of World for Task.check: final state + action log."""

    def __init__(self, d):
        self.state, self.log = d["state"], d["log"]
        self.audit_log = d.get('audit_log', self.log)


def load_episode(exp, row):
    p = os.path.join(exp, row["task"], row["fault"], row["method"], "trace.json")
    d = json.load(open(p, encoding="utf-8"))
    tr = Trace.from_dict(d['trace'])
    for n in d["trace"]["nodes"]:
        node = Node(**{k: v for k, v in n.items() if k in NODE_FIELDS})
        if "parse" not in n:  # pre-v2 trace: infer unusable JSON replies from what was (not) parsed
            if node.agent == "planner":
                node.parse = "ok" if node.data else "failed"
            elif node.agent == "researcher":
                node.parse = "ok" if isinstance(node.data, dict) else "failed"
            else:
                node.parse = "n/a"
        tr.add(node)
    return d, tr, SavedWorld(d)


def rebuild_fault(task, name, trace, log):
    f = next((x for x in schedule(task, extra=True) if x.name == name), None)
    if f is None:
        return None
    hits = [a for a in log if a.get("fault") == name]          # tool faults leave a mark on the action
    marked = [n for n in trace.nodes.values() if n.fault == name]  # output faults on the node
    if hits:
        f.target = hits[0]["by"]
    elif marked:
        f.target = marked[0].id
    f.fired = len(hits) or len(marked)
    return f


def rescore(exp):
    src = os.path.join(exp, "results.jsonl")
    backup = os.path.join(exp, "results.before_rescore.jsonl")
    legacy = os.path.join(exp, "results.v1.jsonl")  # older name of the same backup
    if not os.path.exists(backup):
        if os.path.exists(legacy):
            os.replace(legacy, backup)
        else:
            shutil.copy(src, backup)
    old = [json.loads(l) for l in open(backup, encoding="utf-8")]
    keep = ("task", "fault", "method", "model")
    cleans, nones, out = {}, {}, {}
    order = sorted(range(len(old)), key=lambda i: (old[i]["fault"] != "clean", old[i]["method"] != "none"))
    changed = []
    for i in order:
        r = old[i]
        task = TASKS_BY_ID[r["task"]]
        tasks_mod.CASH_MODE = r.get("cash_mode", "legacy")  # the checker compares against the run's starting cash
        d, tr, w = load_episode(exp, r)
        base = {k: r[k] for k in keep if k in r}
        tag = r.get("harness", "v1")
        base.update(harness=tag if tag.endswith("+rescored") else tag + "+rescored", cash_mode=r.get("cash_mode", "legacy"))
        if r["fault"] == "clean":
            m = episode(task, tr, w, r["latency"])
            row = {**base, "kind": "-", "category": "-", "stage": "-", **m}
            if r["method"] == "none":
                cleans[task.id] = (tr, m["safe_success"])
        else:
            c_tr, c_ok = cleans.get(task.id, (None, True))
            if r['method'] in METHODS or 'recovery_status' in r:
                m = episode(task, tr, w, r["latency"])
                f = next(x for x in schedule(task, extra=True) if x.name == r["fault"])
                m.update({k: r[k] for k in ("recovery_latency", "recomputed", "retained", "unrecoverable",
                                             "recovery_cost") if k in r})
                m.update({k: v for k, v in r.items() if k.startswith('recovery_') or k in
                          {'requested_strategy', 'effective_strategy', 'reason', 'attempts', 'errors',
                           'compensations', 'decisions', 'invalidated', 'source_trace_id'}})
                none_row = nones.get((task.id, r["fault"]), {})
                m.update(fault_outcome=none_row.get("outcome"), unauthorized_before=none_row.get("unauthorized"))
            else:
                f = rebuild_fault(task, r["fault"], tr, w.log)
                m = episode(task, tr, w, r["latency"], f, c_tr, c_ok)
            row = {**base, "kind": f.kind, "category": f.category, "stage": f.stage, **m}
            if r["method"] == "none":
                nones[(task.id, r["fault"])] = row
        for k in ("correct", "safe_success", "outcome"):
            if r.get(k) != row.get(k):
                changed.append(f"{r['task']} {r['fault']} {r['method']}: {k} {r.get(k)} -> {row.get(k)}")
        out[i] = row
        if 'config' in r:
            row['config'] = r['config']
    with open(src, "w", encoding="utf-8") as fh:
        for i in range(len(old)):
            fh.write(json.dumps(out[i]) + "\n")
    print(f"{exp}: re-scored {len(out)} episodes (input: results.before_rescore.jsonl). {len(changed)} changes:")
    for c in changed:
        print("  ", c)


if __name__ == "__main__":
    for e in sys.argv[1:]:
        rescore(e)
