"""Validate the three live Phase 2 matrices and write a compact comparison.

This is a release gate, not a replacement for the per-run summaries. It refuses
partial grids, mismatched configurations, and deterministic test-double runs.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from analysis import load


METHODS = ('none', 'restart', 'verify', 'selective', 'change_aware', 'reconcile')
TASKS = tuple(f'T{i}' for i in range(1, 7))
FAULTS = ('R-incorrect', 'R-stale', 'R-timeout', 'E-timeout',
          'R-malicious', 'R-ipi', 'P-malicious', 'R-distractor')


def read_run(path, scenario):
    root = Path(path)
    if (root / 'failures.log').exists():
        raise ValueError(f'{root}: failures.log exists')
    try:
        manifest = json.loads((root / 'manifest.json').read_text())
        selection = json.loads((root / 'selection.json').read_text())
        rows = load(str(root))
        raw_rows = [json.loads(line) for line in (root / 'results.jsonl').read_text().splitlines()]
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError(f'{root}: missing or invalid run files: {exc}') from exc
    expected_faults = FAULTS if scenario == 'committed' else ('E-timeout',)
    if (selection != {'tasks': list(TASKS), 'faults': sorted(expected_faults),
                      'methods': sorted(METHODS)}
            or manifest.get('timeout_outcome') != scenario.replace('-', '_')
            or manifest.get('cash_mode') != 'ample'):
        raise ValueError(f'{root}: unexpected experiment scope or configuration')
    expected = {(t, 'clean', m) for t in TASKS for m in ('none', 'verify')}
    expected.update((t, f, m) for t in TASKS for f in expected_faults for m in METHODS)
    actual = [(r['task'], r['fault'], r['method']) for r in rows]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError(f'{root}: expected {len(expected)} distinct episodes; '
                         f'found {len(actual)} rows and {len(set(actual))} distinct keys')
    if len(raw_rows) != len(rows):
        raise ValueError(f'{root}: derived and raw result row counts differ')
    for row, raw_row in zip(rows, raw_rows):
        if (row['task'], row['fault'], row['method']) != (raw_row['task'], raw_row['fault'], raw_row['method']):
            raise ValueError(f'{root}: derived and raw result order differs')
        trace_path = root / row['task'] / row['fault'] / row['method'] / 'trace.json'
        try:
            saved = json.loads(trace_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f'{trace_path}: missing or invalid trace: {exc}') from exc
        if saved.get('metrics') != raw_row:
            raise ValueError(f'{trace_path}: trace metrics differ from results.jsonl')
    models = {r['model'] for r in rows}
    if len(models) != 1 or 'deterministic-test-double' in models:
        raise ValueError(f'{root}: expected one real model, found {sorted(models)}')
    if any(r.get('config') != manifest for r in rows):
        raise ValueError(f'{root}: row configuration differs from manifest')
    return rows, next(iter(models)), manifest


def comparison(runs):
    models = {model for _, model, _ in runs.values()}
    shared_configs = {json.dumps({key: value for key, value in manifest.items()
                                  if key != 'timeout_outcome'}, sort_keys=True)
                      for _, _, manifest in runs.values()}
    if len(models) != 1 or len(shared_configs) != 1:
        raise ValueError('the three runs use different models or configurations')
    model = next(iter(models))
    lines = ['# Phase 2 complete-matrix audit', '', f'Model: `{model}`. '
             'All three runs use six tasks, six methods, ample cash, and matching agent configuration.', '',
             '| Scenario | Episodes | Safe success by method (none / restart / verify / selective / change_aware / reconcile) |',
             '| --- | ---: | --- |']
    for scenario, (rows, _, _) in runs.items():
        fault_rows = [r for r in rows if r['fault'] != 'clean']
        counts = {m: (sum(bool(r['safe_success']) for r in fault_rows if r['method'] == m),
                      sum(r['method'] == m for r in fault_rows)) for m in METHODS}
        summary = ' / '.join(f'{counts[m][0]}/{counts[m][1]}' for m in METHODS)
        lines.append(f'| {scenario} | {len(rows)} | {summary} |')
    lines.extend(['', '## Main matrix: paired recovery cost', '',
                  'Pairs are the same task and fault. Total tokens include the faulty run plus recovery work; '
                  'a lower cost is useful only alongside safe success.', '',
                  '| Method | Safe success | Mean total tokens | Mean tokens saved vs restart | '
                  'Reconstructed / fallback / stopped |',
                  '| --- | ---: | ---: | ---: | --- |'])
    main = runs['committed'][0]
    fault_rows = [r for r in main if r['fault'] != 'clean']
    restart = {(r['task'], r['fault']): r for r in fault_rows if r['method'] == 'restart'}
    for method in METHODS:
        rs = [r for r in fault_rows if r['method'] == method]
        safe = sum(bool(r['safe_success']) for r in rs)
        mean_tokens = sum(r['total_tokens'] for r in rs) / len(rs)
        saved = sum(restart[(r['task'], r['fault'])]['total_tokens'] - r['total_tokens']
                    for r in rs) / len(rs)
        statuses = Counter(r.get('recovery_status', 'n/a') for r in rs)
        status_text = ' / '.join(str(statuses[s]) for s in
                                 ('reconstructed', 'fallback_reconstructed', 'stopped'))
        lines.append(f'| {method} | {safe}/{len(rs)} | {mean_tokens:.0f} | {saved:.0f} | {status_text} |')
    lines.extend(['', '## Interpretation limits', '',
                  '- Each task/fault cell is one model run. Counts describe this run, not a confidence interval.',
                  '- `verify` is preventive and does not inspect executor effects; its run is separate from the shared faulty baseline.',
                  '- Irreversible external effects remain in `safe_success` after account reconstruction.',
                  '- `R-stale` and `R-incorrect` both flip the decision-relevant rating in this benchmark; '
                  'the stale fault additionally changes the source date and price.', ''])
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--committed', required=True)
    ap.add_argument('--not-committed', required=True)
    ap.add_argument('--unknown', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    runs = {name: read_run(getattr(args, name.replace('-', '_')), name)
            for name in ('committed', 'not-committed', 'unknown')}
    output = comparison(runs)
    Path(args.out).write_text(output)
    print(f'validated 396 episodes; wrote {args.out}')


if __name__ == '__main__':
    main()
