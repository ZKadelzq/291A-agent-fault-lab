"""Paired replay of predecoded clean events. Run on Nautilus only."""
import argparse
import hashlib
import json
import math
import platform
import random
import statistics as st
import time
import tracemalloc
from pathlib import Path

from mas.env import World
from mas.trace import Node, Trace

MODES = ('off', 'message', 'raw', 'checkpoint')


def replay(nodes, checkpoints, mode):
    tracker = Trace('replay') if mode != 'off' else None
    last = None
    for node in nodes:
        last = node.id
        if tracker:
            tracker.add(node)
    edges = tracker.edges(state=mode != 'message') if tracker else set()
    if mode == 'checkpoint':
        # Use the very same World.snapshot implementation as live execution.
        world = object.__new__(World)
        for key, state in checkpoints.items():
            world.state = state
            tracker.capture_checkpoint(key, world)
    return last, edges, tracker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rng = random.Random(291)
    samples, graph, digests = [], {}, {}
    for i in range(1, 7):
        task = f'T{i}'
        path = args.matrix / task / 'clean/none/trace.json'
        data = path.read_bytes()
        digests[task] = hashlib.sha256(data).hexdigest()
        trace = json.loads(data)['trace']
        nodes = [Node(**n) for n in trace['nodes']]
        checkpoints = trace['checkpoints']
        ref = {tuple(e) for e in trace['edges']}
        graph[task] = {}
        for mode in MODES:
            last, edges, tracker = replay(nodes, checkpoints, mode)
            assert last == nodes[-1].id and edges <= ref
            if mode in ('raw', 'checkpoint'):
                assert edges == ref
            if mode == 'checkpoint':
                assert tracker.checkpoints == checkpoints
            graph[task][mode] = {'missed': len(ref - edges), 'reference': len(ref)}
            for _ in range(5):
                replay(nodes, checkpoints, mode)
        for block in range(30):
            order = list(MODES)
            rng.shuffle(order)
            for mode in order:
                start = time.process_time_ns()
                for _ in range(20):
                    replay(nodes, checkpoints, mode)
                elapsed = (time.process_time_ns() - start) / 20 / 1000
                tracemalloc.start()
                replay(nodes, checkpoints, mode)
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                samples.append(dict(task=task, block=block, mode=mode, cpu_us=elapsed, peak_bytes=peak))
    indexed = {(x['task'], x['block'], x['mode']): x for x in samples}
    summary = {}
    for mode in MODES:
        rows = [x for x in samples if x['mode'] == mode]
        diffs = sorted(x['cpu_us'] - indexed[x['task'], x['block'], 'off']['cpu_us'] for x in rows)
        summary[mode] = dict(cpu_us=st.median(x['cpu_us'] for x in rows),
                             paired_us=st.median(diffs), paired_p95_us=diffs[math.ceil(.95*len(diffs))-1],
                             peak_bytes=st.median(x['peak_bytes'] for x in rows),
                             missed=sum(g[mode]['missed'] for g in graph.values()))
    args.output.write_text(json.dumps(dict(platform=platform.platform(), python=platform.python_version(),
        source_sha256=digests, graph=graph, samples=samples, summary=summary), indent=1) + '\n')
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
