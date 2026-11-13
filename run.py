"""Experiment harness: clean baseline + fault schedule x recovery methods.

  python run.py --tasks T1 --faults R-incorrect --methods none,restart,verify --out runs/smoke
  python run.py --faults R-incorrect,R-distractor --out runs/control         # relevance control
  python run.py --faults E-timeout --cash ample --out runs/etimeout-ample      # controlled duplicate-order test
"""
import argparse
import copy
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name('.env'))  # environment overrides .env; never print credentials

from mas import llm, tasks as tasks_mod, faults as faults_mod
from mas.agents import config_signature
from mas.env import World
from mas.faults import schedule
from mas.metrics import episode
from mas.recovery import METHODS
from mas.tasks import TASKS, TASKS_BY_ID
from mas.trace import Node, Trace
from mas.workflow import run_workflow


HARNESS = "v4-recovery-evidence"


def expected_episode_keys(tasks, faults, methods, extra=False):
    """The exact result grid, including clean controls and the mandatory faulty run."""
    keys = {(t.id, 'clean', 'none') for t in tasks}
    if 'verify' in methods:
        keys.update((t.id, 'clean', 'verify') for t in tasks)
    fault_methods = {'none', *[m for m in METHODS if m in methods]}
    if 'verify' in methods:
        fault_methods.add('verify')
    for task in tasks:
        for fault in schedule(task, extra):
            if faults is None or fault.name in faults:
                keys.update((task.id, fault.name, method) for method in fault_methods)
    return keys


def experiment_config():
    return {'signature': config_signature(), 'cash_mode': tasks_mod.CASH_MODE,
            'harness': HARNESS, 'recovery_fallbacks': 1,
            'timeout_outcome': faults_mod.TIMEOUT_OUTCOME,
            'fault_timeout_s': faults_mod.TIMEOUT_S, 'api_timeout_s': llm.TIMEOUT, 'api_retries': llm.RETRIES}


def save(out, task, fault_name, method, trace, world, metrics):
    d = os.path.join(out, task.id, fault_name, method)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "trace.json"), "w") as f:
        json.dump({"metrics": metrics, "trace": trace.to_dict(), "state": world.state, "log": world.log,
                   'audit_log': world.audit_log}, f, indent=1)


def timed(fn, *a, **kw):
    t0 = time.time()
    r = fn(*a, **kw)
    return r, time.time() - t0


def load_row(out, task, fault_name, method):
    """Saved (trace, row) of a finished episode, or None. Used by --resume."""
    p = os.path.join(out, task.id, fault_name, method, "trace.json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p))
        if d["metrics"].get("harness") != HARNESS:  # never mix episodes from another harness version
            return None
        if d['metrics'].get('config') != experiment_config():
            return None
        tr = Trace.from_dict(d['trace'])
        return tr, d["metrics"]
    except Exception:
        return None


def record(out, task, trace, world, m, method, fault=None):
    name = fault.name if fault else "clean"
    row = {"task": task.id, "fault": name, "method": method, "model": llm.MODEL, "harness": HARNESS,
           "cash_mode": tasks_mod.CASH_MODE,
           'config': experiment_config(),
           "kind": fault.kind if fault else "-", "category": fault.category if fault else "-",
           "stage": fault.stage if fault else "-", **m}
    save(out, task, name, method, trace, world, row)
    details = []
    if 'recovery_status' in m:
        details.extend((f"fault_outcome={m['fault_outcome']}",
                        f"recovery_status={m['recovery_status']}"))
        if m.get('effective_strategy') and m['effective_strategy'] != method:
            details.append(f"effective_strategy={m['effective_strategy']}")
        if m.get('reason'):
            details.append(f"reason={m['reason']}")
    elif 'outcome' in m:
        details.append(f"outcome={m['outcome']}")
    if method == 'verify':
        if fault is not None:
            details.append(f"detected={m.get('detected', False)}")
        details.extend((f"false_alarms={m.get('false_alarms', 0)}",
                        f"verifier_errors={m.get('verifier_errors', 0)}"))
    suffix = ' ' + ' '.join(details) if details else ''
    print(f"[{task.id}] {name:12s} {method:12s} correct={m['correct']} safe={m['safe_success']} "
          f"unauth={m['unauthorized']}{suffix}", flush=True)
    return row


def run_clean(task, methods, out, resume=False):
    """Clean baseline (and verification on clean input -> false-alarm rate)."""
    if resume:
        saved = [load_row(out, task, "clean", m) for m in ["none"] + (["verify"] if "verify" in methods else [])]
        if all(saved):
            print(f"[{task.id}] clean        (resumed)", flush=True)
            return saved[0][0], saved[0][1]["safe_success"], [x[1] for x in saved]
    world = World(task.client_id, task.cash)
    clean, lat = timed(run_workflow, task, world)
    m = episode(task, clean, world, lat)
    rows = [record(out, task, clean, world, m, "none")]
    if "verify" in methods:
        w = World(task.client_id, task.cash)
        tr, lat = timed(run_workflow, task, w, verify=True)
        rows.append(record(out, task, tr, w, episode(task, tr, w, lat), "verify"))
    return clean, m["safe_success"], rows


def run_fault(task, fault, clean, clean_ok, methods, out, resume=False):
    if resume:  # whole (task, fault) job is skipped only if every requested method finished
        need = ["none"] + [m for m in [*METHODS, 'verify'] if m in methods]
        saved = [load_row(out, task, fault.name, m) for m in need]
        if all(saved):
            print(f"[{task.id}] {fault.name:12s} (resumed)", flush=True)
            return [x[1] for x in saved]
    rows = []
    # 1) Faulty run without recovery: measures propagation.
    f = fault.fresh()
    world = World(task.client_id, task.cash)
    trace, lat = timed(run_workflow, task, world, fault=f)
    m = episode(task, trace, world, lat, f, clean, clean_ok)
    rows.append(record(out, task, trace, world, m, "none", f))
    # 2) Recovery methods start from a copy of that same faulty run (fault identity is supplied).
    for name, method in METHODS.items():
        if name not in methods:
            continue
        w = copy.deepcopy(world)
        new, stats = method(task, w, trace, f)
        rm = episode(task, new, w, stats["recovery_latency"])
        rm.update(stats, fault_outcome=m["outcome"], unauthorized_before=m["unauthorized"])
        rows.append(record(out, task, new, w, rm, name, f))
    # 3) Always-verify baseline under the same fault.
    if "verify" in methods:
        f = fault.fresh()
        w = World(task.client_id, task.cash)
        tr, lat = timed(run_workflow, task, w, fault=f, verify=True)
        rows.append(record(out, task, tr, w, episode(task, tr, w, lat, f, clean, clean_ok), "verify", f))
    return rows


def prepare_output(out, config, selection, resume=False):
    """New runs never overwrite; resumes must retain configuration and scope."""
    root = Path(out)
    manifest, scope = root / 'manifest.json', root / 'selection.json'
    if resume:
        if not manifest.exists() or not scope.exists():
            raise ValueError('resume requires manifest.json and selection.json from an existing run')
        if json.loads(manifest.read_text()) != config:
            raise ValueError('configuration changed; use a new --out directory without --resume')
        if json.loads(scope.read_text()) != selection:
            raise ValueError('tasks/faults/methods changed; use a new --out directory without --resume')
    elif root.exists() and any(root.iterdir()):
        raise ValueError('output directory is not empty; use --resume or a new --out directory')
    root.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(config, indent=2) + '\n')
    scope.write_text(json.dumps(selection, indent=2) + '\n')


def default_output():
    model = ''.join(c if c.isalnum() or c in '.-' else '-' for c in llm.MODEL).strip('.') or 'model'
    root = 'runs/scratch' if llm.MODEL == 'deterministic-test-double' else 'runs'
    return f"{root}/phase2-{model}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="all", help="comma-separated task ids or 'all'")
    ap.add_argument("--faults", default="all", help="comma-separated fault names or 'all'")
    ap.add_argument('--clean-only', action='store_true', help='run only clean controls (for model preflight)')
    ap.add_argument("--methods", default="none,restart,verify", help=','.join(['none', 'verify', *METHODS]))
    ap.add_argument("--out", default=default_output(),
                    help='output directory; defaults to runs/phase2-<model>-<timestamp>-<id> (offline: runs/scratch)')
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel episodes (default 1: parallel episodes on a shared API cause rate-limit waits)")
    ap.add_argument("--resume", action="store_true", help="skip episodes already saved under --out")
    ap.add_argument('--timeout-outcome', choices=['committed', 'not_committed', 'unknown'],
                    default='committed', help='E-timeout submission/status-query scenario (simulated service)')
    ap.add_argument("--extra-faults", action="store_true", help="add R-distractor (wrong rating on a ticker the task ignores)")
    ap.add_argument("--cash", choices=["legacy", "ample"], default=tasks_mod.CASH_MODE,
                    help="starting cash: legacy = budget+5000 (Phase 1 runs); ample = 2x budget, so a duplicate buy "
                         "always fits (controlled E-timeout). Do not mix modes in one --out folder.")
    a = ap.parse_args()
    tasks_mod.CASH_MODE = a.cash
    faults_mod.TIMEOUT_OUTCOME = a.timeout_outcome
    task_ids = [t.id for t in TASKS] if a.tasks == 'all' else a.tasks.split(',')
    if not task_ids or len(task_ids) != len(set(task_ids)) or set(task_ids) - TASKS_BY_ID.keys():
        ap.error('tasks must be distinct known task IDs')
    tasks = [TASKS_BY_ID[t] for t in task_ids]
    if a.clean_only and a.faults != 'all':
        ap.error('--clean-only cannot be combined with --faults')
    faults = set() if a.clean_only else None if a.faults == 'all' else set(a.faults.split(','))
    extra = a.extra_faults or bool(faults and "R-distractor" in faults)  # naming the control is enough
    methods = set(a.methods.split(","))
    if a.workers < 1:
        ap.error('--workers must be positive')
    unknown = methods - {'none', 'verify', *METHODS}
    if unknown:
        ap.error('unknown methods: ' + ','.join(sorted(unknown)))
    available_faults = {f.name for task in tasks for f in schedule(task, extra)}
    if faults is not None and faults - available_faults:
        ap.error('unknown faults: ' + ','.join(sorted(faults - available_faults)))
    expected_keys = expected_episode_keys(tasks, faults, methods, extra)
    config = experiment_config()
    selection = {'tasks': sorted(t.id for t in tasks),
                 'faults': sorted({f.name for t in tasks for f in schedule(t, extra)
                                   if faults is None or f.name in faults}),
                 'methods': sorted(methods | {'none'})}
    try:
        prepare_output(a.out, config, selection, a.resume)
    except ValueError as e:
        ap.error(str(e))
    # These derive from the previous results, which a resumed group may replace.
    for name in ('summary.md', 'results.before_rescore.jsonl', 'results.v1.jsonl'):
        (Path(a.out) / name).unlink(missing_ok=True)
    if (Path(a.out) / 'figs').exists():
        shutil.rmtree(Path(a.out) / 'figs')
    stale = os.path.join(a.out, "failures.log")
    if os.path.exists(stale):  # from an earlier attempt; rewritten below if this run has failures
        os.remove(stale)
    print(f"model={llm.MODEL} base_url={llm.BASE_URL} temperature={llm.TEMPERATURE} "
          f"reasoning_effort={llm.REASONING_EFFORT} cash={a.cash} workers={a.workers} out={a.out}", flush=True)
    failures = []

    def safe(fn, label, *args):
        try:
            return fn(*args)
        except Exception as e:  # one API hiccup must not kill a 140-episode run; rerun with --resume
            failures.append(f"{label}: {type(e).__name__}: {e}")
            print(f"[FAILED] {label}: {type(e).__name__}: {str(e)[:200]}", flush=True)
            return None

    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        cleans = list(ex.map(lambda t: safe(run_clean, f"{t.id} clean", t, methods, a.out, a.resume), tasks))
        jobs = [(t, f, c[0], c[1]) for t, c in zip(tasks, cleans) if c
                for f in schedule(t, extra) if faults is None or f.name in faults]
        faulty = list(ex.map(lambda j: safe(run_fault, f"{j[0].id} {j[1].name}", *j, methods, a.out, a.resume), jobs))
    results = [c[2] for c in cleans if c] + [r for r in faulty if r]
    rows = [row for group in results for row in group]
    with open(os.path.join(a.out, "results.jsonl"), "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    actual_keys = [(r['task'], r['fault'], r['method']) for r in rows]
    if not failures and (len(actual_keys) != len(expected_keys) or set(actual_keys) != expected_keys):
        failures.append(f'incomplete result grid: expected {len(expected_keys)} distinct rows, '
                        f'got {len(actual_keys)} rows and {len(set(actual_keys))} distinct keys')
    s = llm.STATS
    print(f"\nwall={time.time() - t0:.0f}s calls={s['calls']} prompt_tok={s['prompt_tokens']} "
          f"completion_tok={s['completion_tokens']} (reasoning {s['reasoning_tokens']}) retries={s['retries']} "
          f"backoff={s['backoff_s']:.0f}s "
          f"est_cost=${llm.estimate_cost():.2f}")
    if failures:
        with open(os.path.join(a.out, "failures.log"), "w") as f:
            f.write("\n".join(failures) + "\n")
        print(f"{len(failures)} episode(s) failed (see {a.out}/failures.log). Re-run the same command with --resume.")
    print(f"wrote {a.out}/results.jsonl  ->  python summarize.py {a.out}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
