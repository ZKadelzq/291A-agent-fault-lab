"""Run as python -m evaluation.holdout OUTPUT [--resume] on Nautilus."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import run
from mas import workflow
from mas.env import CLIENTS, MARKET
from mas.faults import Fault
from mas.tasks import Task

HERE = Path(__file__).parent
SPEC = json.loads((HERE / 'holdout.json').read_text())
original_mutate = workflow.mutate
original_config = run.experiment_config


def schedule(task, extra=False):
    return [Fault('R-incorrect', 'incorrect', f'research:{task.target_ticker}'),
            Fault('P-stop', 'incorrect', 'plan'),
            Fault('E-timeout', 'timeout', 'exec:*', tool='buy', timeout_outcome='committed')]


def mutate(fault, node, client, world=None):
    if fault.name != 'P-stop':
        return original_mutate(fault, node, client, world=world)
    for step in node.data or []:
        if step['op'] == 'set_stop_loss':
            node.original = node.content
            step['args']['pct'] += 3
            node.content = json.dumps({'steps': node.data})
            fault.fired += 1
            break


def config():
    files = [HERE / 'holdout.json', HERE / 'holdout.py']
    return {**original_config(), 'holdout_sha256': {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    run.TASKS = [Task(*row) for row in SPEC['tasks']]
    for task in run.TASKS:
        if task.client_id not in CLIENTS or not set(task.tickers) <= MARKET.keys() or not task.expected():
            raise ValueError(f"Unsupported simulated task: {task.id}")
    run.TASKS_BY_ID = {t.id: t for t in run.TASKS}
    run.schedule = schedule
    run.experiment_config = config
    workflow.mutate = mutate
    sys.argv = ['holdout', '--tasks', 'all', '--faults', ','.join(SPEC['faults']),
                '--methods', ','.join(SPEC['methods']), '--cash', SPEC['cash'],
                '--timeout-outcome', SPEC['timeout_outcome'], '--workers', '4',
                '--out', str(args.output)] + (['--resume'] if args.resume else [])
    run.main()


if __name__ == '__main__':
    main()
