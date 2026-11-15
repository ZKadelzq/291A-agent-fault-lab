"""Paired market-drift experiment; isolated from the frozen standard matrix.

Examples:
  python scripts/run_market_drift.py --offline --task T1 --fault E-timeout \\
      --ticker AAPL --rating hold --out runs/scratch/market-drift
  python scripts/run_market_drift.py --task T1 --fault E-timeout \\
      --ticker AAPL --price 200 --out runs/market-drift-t1-price

This script does not move the market during any recovery attempt. It freezes
one updated World market revision, then passes exact copies to every strategy.
"""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')  # process environment wins; never print credentials

from mas import llm, faults as faults_mod
from mas.env import World
from mas.faults import schedule
from mas.metrics import episode
from mas.recovery import METHODS
from mas.tasks import TASKS_BY_ID
from mas.trace import tokens
from mas.workflow import run_workflow


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task', choices=sorted(TASKS_BY_ID), default='T1')
    p.add_argument('--fault', default='E-timeout')
    p.add_argument('--ticker', default='AAPL')
    p.add_argument('--rating', choices=['buy', 'hold', 'sell'])
    p.add_argument('--price', type=float)
    p.add_argument('--news')
    p.add_argument('--offline', action='store_true')
    p.add_argument('--methods', default='restart,selective,change_aware,reconcile')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    if a.price is None and a.rating is None and a.news is None:
        p.error('select at least one of --price, --rating, --news')
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        p.error('output directory is not empty: choose another --out')
    if a.offline:
        sys.path.insert(0, str(ROOT / 'tests'))
        from conftest import fake_chat
        llm.chat = fake_chat
        llm.MODEL = 'deterministic-test-double'
        faults_mod.TIMEOUT_S = 0

    task = TASKS_BY_ID[a.task]
    fault = next((x for x in schedule(task, extra=True) if x.name == a.fault), None)
    if fault is None:
        p.error('unknown fault')
    if a.ticker not in task.tickers:
        p.error('changed ticker must be a candidate of the selected task')
    methods = a.methods.split(',')
    if len(methods) != len(set(methods)) or set(methods) - METHODS.keys():
        p.error('unknown or duplicate recovery method')

    world = World(task.client_id, task.cash)
    start = time.perf_counter()
    trace = run_workflow(task, world, fault=fault)
    faulty_cost = tokens(trace.total_cost())
    old_eval = task.check(world)
    changed = world.update_market(a.ticker, price=a.price, rating=a.rating, news=a.news)
    if not changed:
        p.error('proposed update does not alter market; use a different value')
    # New-epoch reference is separate and never revealed to any recovery strategy.
    reference = World(task.client_id, task.cash, market=world.market, news=world.news)
    ref_trace = run_workflow(task, reference)
    ref_check = task.check(reference)
    rows = []
    for name in methods:
        candidate = copy.deepcopy(world)
        recovered, stats = METHODS[name](task, candidate, trace, fault)
        check = task.check(candidate)
        row = {
            'task': task.id, 'fault': fault.name, 'method': name, 'model': llm.MODEL,
            'market_epoch': candidate.market_epoch, 'market_changed': stats['market_changed'],
            'correct': check['correct'], 'safe_success': check['safe_success'],
            'recovery_status': stats['recovery_status'],
            'effective_strategy': stats['effective_strategy'], 'errors': stats['errors'],
            'faulty_tokens': faulty_cost, 'recovery_tokens': stats['recovery_tokens'],
            'total_tokens': faulty_cost + stats['recovery_tokens'],
            'recomputed': stats['recomputed'], 'retained': stats['retained'],
            'recovery_overhead_s': stats['recovery_overhead_s'],
            'trace_overhead_s': stats['trace_overhead_s'],
            'positions': candidate.state['positions'],
            'cash': candidate.state['cash'],
        }
        rows.append(row)
        print(f"{name:12} status={row['recovery_status']:24} "
              f"safe={row['safe_success']} recomputed={row['recomputed']} "
              f"retained={row['retained']} total_tokens={row['total_tokens']}")
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'results.jsonl').open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row) + '\n')
    (out / 'manifest.json').write_text(json.dumps({
        'type': 'market_drift', 'model': llm.MODEL, 'task': task.id, 'fault': fault.name,
        'ticker': a.ticker, 'price': a.price, 'rating': a.rating, 'news': a.news,
        'market_epoch': world.market_epoch,
        'old_epoch_evaluation': old_eval,
        'new_epoch_no_recovery': task.check(world),
        'new_epoch_clean_reference': ref_check,
        'new_epoch_clean_positions': reference.state['positions'],
        'faulty_tokens': faulty_cost,
        'source_trace_id': trace.run_id,
        'reference_trace_id': ref_trace.run_id,
        'elapsed_s': time.perf_counter() - start,
    }, indent=2) + '\n', encoding='utf-8')
    print('wrote', out / 'results.jsonl')


if __name__ == '__main__':
    main()
