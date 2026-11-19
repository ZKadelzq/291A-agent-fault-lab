"""Regression tests for final evidence integrity, not model-quality claims."""
import copy
import json

from mas.env import World
from mas.faults import schedule
from mas.tasks import TASKS
from mas.workflow import run_workflow
from mas.recovery import METHODS
from mas.recover.engine import recover
from mas.recover.contracts import RecoveryContext
from mas.recover.selective import Restart
from run import run_clean, run_fault


def faulty():
    task = TASKS[0]
    world = World(task.client_id, task.cash)
    fault = next(f for f in schedule(task) if f.name == 'E-timeout')
    trace = run_workflow(task, world, fault=fault)
    return task, world, trace, fault


def test_fallback_costs_and_work_include_failed_attempt():
    task, world, trace, fault = faulty()
    world.update_market('AAPL', rating='hold')
    _, stats = METHODS['reconcile'](task, world, trace, fault)
    assert stats['recovery_status'] == 'fallback_reconstructed'
    assert len(stats['recovery_audit']) == 2
    assert [a['published'] for a in stats['recovery_audit']] == [False, True]
    assert all(a['trace']['nodes'] for a in stats['recovery_audit'])
    assert all(any(n['messages'] for n in a['trace']['nodes']) for a in stats['recovery_audit'])
    assert stats['recovery_tokens'] == sum(u['prompt_tokens'] + u['completion_tokens']
                                           for a in stats['recovery_audit'] for u in a['model_usage'])
    for key, value in stats['recovery_overhead_s'].items():
        assert value == sum(a['overhead_s'][key] for a in stats['recovery_audit'])
    assert stats['tool_tracking_s'] == sum(a['tool_tracking_s'] for a in stats['recovery_audit'])
    assert stats['attempted_recomputed'] > stats['recomputed']


def test_concurrent_market_update_is_not_reported_published():
    task, world, trace, fault = faulty()
    initial = copy.deepcopy(world.state)

    class ConcurrentUpdate(Restart):
        def build(self, ctx, runtime):
            result = super().build(ctx, runtime)
            world.update_market('AAPL', price=201)
            return result

    result = recover(RecoveryContext(task, trace, fault.target), world, ConcurrentUpdate())
    assert result.stats['recovery_status'] == 'stopped'
    assert result.stats['errors'] == ['MARKET_CHANGED_DURING_RECOVERY']
    assert not any(a['published'] for a in result.stats['recovery_audit'])
    assert world.state == initial


def test_checker_rejects_one_share_error():
    task = TASKS[0]
    world = World(task.client_id, task.cash)
    run_workflow(task, world)
    assert task.check(world)['correct']
    world.state['positions']['AAPL'] += 1
    assert not task.check(world)['checks']['right_amounts']


def test_resume_rejects_wrong_source_pairing(tmp_path):
    task = TASKS[0]
    methods = {'none', 'restart'}
    clean, ok, _ = run_clean(task, methods, str(tmp_path))
    fault = next(f for f in schedule(task) if f.name == 'R-incorrect')
    rows = run_fault(task, fault, clean, ok, methods, str(tmp_path))
    path = tmp_path / 'T1/R-incorrect/restart/trace.json'
    old = json.loads(path.read_text())
    old['metrics']['source_trace_id'] = 'wrong-pair'
    path.write_text(json.dumps(old))
    new = run_fault(task, fault, clean, ok, methods, str(tmp_path), resume=True)
    assert new[1]['source_trace_id'] != rows[1]['source_trace_id']
    assert json.loads(path.read_text())['metrics'] == new[1]
    assert (path.parent / 'artifact_io.json').exists()


def test_market_script_archives_source_reference_and_every_recovery(tmp_path, monkeypatch):
    import sys
    from scripts.run_market_drift import main
    monkeypatch.setattr(sys, 'argv', ['run_market_drift.py', '--offline', '--task', 'T1',
                                    '--ticker', 'AAPL', '--price', '200', '--out', str(tmp_path)])
    main()
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    rows = [json.loads(s) for s in (tmp_path / 'results.jsonl').read_text().splitlines()]
    assert len(rows) == 4
    source = json.loads((tmp_path / 'T1/E-timeout/none/trace.json').read_text())
    assert source['trace']['run_id'] == manifest['source_trace_id']
    reference = json.loads((tmp_path / 'T1/clean/none/trace.json').read_text())
    assert reference['trace']['run_id'] == manifest['reference_trace_id']
    for row in rows:
        path = tmp_path / 'T1/E-timeout' / row['method'] / 'trace.json'
        data = json.loads(path.read_text())
        assert data['metrics'] == row
        assert row['source_trace_id'] == manifest['source_trace_id']
        assert data['market']['AAPL']['price'] == 200
        assert row['completed_safe']
