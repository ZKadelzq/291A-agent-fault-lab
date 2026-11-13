"""Read-only provenance, reuse, and graph-cost audit for saved Phase 2 traces.

The timings below measure graph analysis on saved traces, not live tracing cost.
No model calls or experiment scores are changed.
"""
import argparse
import json
import platform
import statistics
import time
from collections import Counter
from pathlib import Path

from mas.plan import same_step
from mas.trace import Trace, _overlap


ROOT = Path('runs/phase2-muse-glimmer-30b')
METHODS = ('restart', 'selective', 'change_aware', 'reconcile')


def compact_bytes(value):
    return len(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def saved(root, task, fault, method):
    return json.loads((root / task / fault / method / 'trace.json').read_text())


def graph_variants(trace):
    """The uses-only variant deliberately drops plan-to-executor edges.

    It is a diagnostic lower bound, not a safe implementation: plan changes can
    change every executor step, including steps with no declared research use.
    """
    nodes = list(trace.nodes.values())
    coarse = {(p, n.id) for n in nodes for p in n.parents if p in trace.nodes}
    uses = {(p, d) for p, d in coarse
            if not (p == 'plan' and trace.nodes[d].agent == 'executor')}
    uses |= {(u, n.id) for n in nodes for u in n.uses if u in trace.nodes}
    state = set()
    for j, dst in enumerate(nodes):
        reads = [k for a in dst.actions for k in a['reads']]
        for src in nodes[:j]:
            writes = [k for a in src.actions for k in a['writes']]
            if _overlap(reads, writes):
                state.add((src.id, dst.id))
    return {'coarse': coarse, 'uses_only': uses, 'coarse_state': coarse | state,
            'uses_state': uses | state}


def descendants(edges, target):
    children = {}
    for src, dst in edges:
        children.setdefault(src, set()).add(dst)
    seen, stack = set(), [target]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def check_reuse(root, task, fault, method, source, clean):
    result = saved(root, task, fault, method)
    metrics, trace = result['metrics'], result['trace']
    reused = [n for n in trace['nodes'] if n.get('reused_from')]
    if metrics['recovery_status'] == 'stopped':
        if reused or metrics['retained']:
            raise ValueError(f'{task}/{fault}/{method}: stopped result claims reuse')
        return 0, 0, Counter(), 0
    if len(reused) != metrics['retained']:
        raise ValueError(f'{task}/{fault}/{method}: retained count differs from trace')
    if trace['source_trace_id'] != source['run_id'] or trace['context_hash'] != source['context_hash']:
        raise ValueError(f'{task}/{fault}/{method}: source or context mismatch')
    source_nodes = {n['id']: n for n in source['nodes']}
    clean_nodes = {n['id']: n for n in clean['nodes']}
    invalid = set(metrics['invalidated'])
    clean_matches, kinds, revalidated = 0, Counter(), 0
    for node in reused:
        old = source_nodes.get(node['id'])
        if old is None or node['reused_from'] != f"{source['run_id']}/{node['id']}":
            raise ValueError(f'{task}/{fault}/{method}/{node["id"]}: bad source reference')
        candidate = {k: v for k, v in node.items() if k != 'reused_from'}
        original = {k: v for k, v in old.items() if k != 'reused_from'}
        changed = {key for key in candidate if candidate[key] != original[key]}
        if changed:
            # restore_prefix updates step provenance to the repaired plan but
            # preserves the executor-visible operation and recorded actions.
            if (node['agent'] != 'executor' or method != 'change_aware'
                    or not changed <= {'parents', 'uses', 'step'}
                    or not same_step(node['step'], old['step'])):
                raise ValueError(f'{task}/{fault}/{method}/{node["id"]}: changed reused node {changed}')
        if node['id'] in invalid:
            # Change-aware/reconcile may retain a previously invalidated executor
            # prefix after the repaired plan and recorded receipts pass valid_prefix.
            if node['agent'] != 'executor' or method not in ('change_aware', 'reconcile'):
                raise ValueError(f'{task}/{fault}/{method}/{node["id"]}: invalid reuse')
            revalidated += 1
        kinds[node['agent']] += 1
        control = clean_nodes.get(node['id'])
        if control and (node['content'], node['data']) == (control['content'], control['data']):
            clean_matches += 1
    return len(reused), clean_matches, kinds, revalidated


def analyze(root, repeats):
    rows = [json.loads(line) for line in (root / 'results.jsonl').read_text().splitlines()]
    pairs = sorted({(r['task'], r['fault']) for r in rows if r['fault'] != 'clean'})
    size = Counter()
    graph_counts = Counter()
    edge_counts = Counter()
    missed = Counter()
    timing = []
    reuse, clean_match, kinds, revalidated = Counter(), Counter(), Counter(), Counter()
    for task, fault in pairs:
        source = saved(root, task, fault, 'none')['trace']
        clean = saved(root, task, 'clean', 'none')['trace']
        trace = Trace.from_dict(source)
        # The fault target is recorded directly on the faulty node.
        targets = [n.id for n in trace.nodes.values() if n.fault]
        if len(targets) != 1:
            raise ValueError(f'{task}/{fault}: expected one tagged fault; got {targets}')
        target = targets[0]
        graph = graph_variants(trace)
        if graph['coarse_state'] != trace.edges(state=True):
            raise ValueError(f'{task}/{fault}: graph reconstruction differs from runtime')
        reference = descendants(graph['coarse_state'], target)
        for name, edges in graph.items():
            reached = descendants(edges, target)
            graph_counts[name] += len(reached)
            edge_counts[name] += len(edges)
            missed[name] += len(reference - reached)
        start = time.perf_counter_ns()
        for _ in range(repeats):
            variants = graph_variants(trace)
            for edges in variants.values():
                descendants(edges, target)
        timing.append((time.perf_counter_ns() - start) / repeats / 1000)
        provenance = [{'parents': n.parents, 'uses': n.uses,
                       'access': [{'reads': a['reads'], 'writes': a['writes']} for a in n.actions]}
                      for n in trace.nodes.values()]
        size['provenance'] += compact_bytes(provenance)
        size['checkpoints'] += compact_bytes(source['checkpoints'])
        size['trace_file'] += (root / task / fault / 'none' / 'trace.json').stat().st_size
        size['nodes'] += len(trace.nodes)
        for method in METHODS:
            count, match, by_agent, checked = check_reuse(root, task, fault, method, source, clean)
            reuse[method] += count
            clean_match[method] += match
            kinds.update(by_agent)
            revalidated[method] += checked
    n = len(pairs)
    lines = ['# Phase 2 provenance and reuse audit', '',
             f'Scope: {n} main-matrix faulty source traces and {n * len(METHODS)} paired recovery traces. '
             'This analysis reads saved data only; it makes no model or tool calls and changes no scores.', '',
             '## Reuse integrity', '',
             'Every retained node was checked against the paired faulty source trace: exact source ID, '
             'context hash, and original node fields (except the added `reused_from` tag). '
             'For a verified executor prefix, repaired-plan provenance may replace `parents`, '
             '`uses`, and `step` metadata; executor-visible operation, arguments, action receipts, '
             'and other fields must stay identical. '
             'Retained nodes in the invalidation set must be executor prefix nodes that passed '
             'the runtime step/receipt/checkpoint validation. Stopped rows must report zero retained nodes.', '',
             '| Strategy | Retained nodes passing source checks | Revalidated invalidated executor prefix | Same content and parsed data as separate clean control |',
             '| --- | ---: | ---: | ---: |']
    for method in METHODS:
        lines.append(f'| {method} | {reuse[method]}/{reuse[method]} | {revalidated[method]} | '
                     f'{clean_match[method]}/{reuse[method]} |')
    lines += ['', f'Retained node types: {dict(kinds)}. The clean-control comparison is a diagnostic, '
              'not a validity oracle: independently sampled LLM text can differ while the same '
              'authorized operation and final state remain valid. Exact source preservation plus '
              'runtime receipt, checkpoint, execution-contract, and final-state checks support '
              'reuse integrity; they do not prove semantic equivalence to every possible clean run.', '',
              '## Dependency precision and metadata cost', '',
              'The reference runtime uses `coarse_state` (message parents plus shared-state '
              'read-after-write edges). The `uses_only` diagnostic removes plan-to-executor '
              'edges and adds declared research uses; it is **unsafe** when a changed plan '
              'alters an executor step with no declared use. `uses_state` also adds shared-state edges.', '',
              '| Graph | Mean edges/source | Mean downstream nodes/fault | Nodes missed vs runtime reference |',
              '| --- | ---: | ---: | ---: |']
    for name in ('coarse', 'uses_only', 'coarse_state', 'uses_state'):
        lines.append(f'| {name} | {edge_counts[name] / n:.2f} | '
                     f'{graph_counts[name] / n:.2f} | {missed[name]} |')
    lines += ['', f'Across {size["nodes"]} source nodes, compact UTF-8 provenance fields '
              f'(`parents`, `uses`, action `reads`/`writes`) occupy {size["provenance"]:,} bytes '
              f'({size["provenance"] / size["nodes"]:.0f} bytes/node); serialized '
              f'checkpoints occupy {size["checkpoints"]:,} bytes. The saved, pretty-printed '
              f'trace files total {size["trace_file"]:,} bytes. These are offline storage '
              'measurements, not incremental live memory or network usage.', '',
              f'Post-hoc construction of all four graph variants and reachability '
              f'queries on `{platform.platform()}` ({repeats} repetitions per source; '
              f'median of {n} per-source averages):', '']
    samples = sorted(timing)
    lines.append(f'- Median: {statistics.median(samples):.1f} microseconds/source; '
                 f'P95: {samples[int(.95 * (n - 1))]:.1f} microseconds/source.')
    lines += ['', 'These timings exclude live provenance capture, JSON serialization, disk I/O, '
              'model latency, and recovery work. They cannot be added to or subtracted from '
              'recorded episode latency. Run the command below on the same machine to compare '
              'implementation changes.', '',
              '```bash',
              'python analyze_phase2_provenance.py --out runs/PHASE2_PROVENANCE.md',
              '```', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=Path('runs/PHASE2_PROVENANCE.md'))
    parser.add_argument('--repeats', type=int, default=100)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    args.out.write_text(analyze(args.run, args.repeats))
    print(f'validated reuse and graph costs; wrote {args.out}')


if __name__ == '__main__':
    main()
