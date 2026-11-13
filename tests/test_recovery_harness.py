import copy
import json
from pathlib import Path

import pytest

from analysis import load
from mas import tasks as tasks_mod
from mas.faults import Fault, schedule
from mas.recovery import METHODS
from mas.tasks import TASKS
from rescore import rescore
from run import expected_episode_keys, run_clean, run_fault, load_row


def test_full_phase2_grid_has_the_documented_episode_counts():
    methods = {'none', 'restart', 'verify', 'selective', 'change_aware', 'reconcile'}
    committed = expected_episode_keys(TASKS, None, methods, extra=True)
    timeout = expected_episode_keys(TASKS, {'E-timeout'}, methods)
    assert len(committed) == 300
    assert len(timeout) == 48
    assert {key for key in committed if key[1] == 'clean'} == {
        (t.id, 'clean', method) for t in TASKS for method in ('none', 'verify')}
    assert len({key for key in timeout if key[1] == 'E-timeout'}) == 36


def test_saved_results_resume_and_rescore(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks_mod, 'CASH_MODE', 'ample')
    methods = set(METHODS) | {'none', 'verify'}
    task = TASKS[0]
    clean, ok, rows = run_clean(task, methods, str(tmp_path))
    fault = next(f for f in schedule(task) if f.name == 'R-incorrect')
    rows += run_fault(task, fault, clean, ok, methods, str(tmp_path))
    result_file = tmp_path / 'results.jsonl'
    result_file.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    derived = load(str(tmp_path))
    base = next(r for r in rows if r['fault'] == 'R-incorrect' and r['method'] == 'none')
    for row in derived:
        if row['method'] in METHODS:
            assert row['total_tokens'] == base['tokens'] + row['recovery_tokens']
            assert load_row(str(tmp_path), task, 'R-incorrect', row['method']) is not None
    resumed = run_fault(task, fault, clean, ok, methods, str(tmp_path), resume=True)
    assert len(resumed) == len(METHODS) + 2
    rescore(str(tmp_path))
    rescored = load(str(tmp_path))
    for before, after in zip(derived, rescored):
        assert before['total_tokens'] == after['total_tokens']
        assert before['safe_success'] == after['safe_success']
        if before['method'] in METHODS:
            assert before['recovery_status'] == after['recovery_status']
    snapshot = result_file.read_text()
    rescore(str(tmp_path))
    assert result_file.read_text() == snapshot


def test_resume_rejects_config_change(tmp_path, monkeypatch):
    task = TASKS[0]
    run_clean(task, {'none'}, str(tmp_path))
    p = tmp_path / 'T1/clean/none/trace.json'
    data = json.loads(p.read_text())
    data['metrics']['config']['signature'] = 'different'
    p.write_text(json.dumps(data))
    assert load_row(str(tmp_path), task, 'clean', 'none') is None


@pytest.mark.parametrize('mode', ['not_committed', 'unknown'])
def test_timeout_query_and_truth_survive_save_and_rescore(tmp_path, mode):
    task = TASKS[0]
    methods = set(METHODS) | {'none'}
    clean, ok, rows = run_clean(task, methods, str(tmp_path))
    fault = Fault('E-timeout', 'timeout', 'exec:*', tool='buy', timeout_outcome=mode)
    rows += run_fault(task, fault, clean, ok, methods, str(tmp_path))
    result_file = tmp_path / 'results.jsonl'
    result_file.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    rescore(str(tmp_path))
    after = [json.loads(line) for line in result_file.read_text().splitlines()]
    for original, rescored in zip(rows, after):
        for key in ('safe_success', 'dup_committed', 'request_retry_attempted', 'business_dup_attempted',
                    'timeout_committed', 'recovery_status', 'recovery_status_queries'):
            assert original.get(key) == rescored.get(key), key
    baseline = next(r for r in after if r['fault'] == 'E-timeout' and r['method'] == 'none')
    if mode == 'not_committed':
        assert baseline['safe_success'] and not baseline['timeout_committed']
        assert baseline['dup_committed'] == 0 and baseline['outcome'] == 'absorbed'


def test_output_requires_explicit_matching_resume(tmp_path):
    from run import prepare_output
    root = tmp_path / 'experiment'
    config = {'model': 'test', 'temperature': 0}
    scope = {'tasks': ['T1'], 'faults': ['E-timeout'], 'methods': ['none']}
    prepare_output(root, config, scope)
    evidence = root / 'evidence.txt'
    evidence.write_text('keep')
    with pytest.raises(ValueError, match='not empty'):
        prepare_output(root, config, scope)
    with pytest.raises(ValueError, match='configuration changed'):
        prepare_output(root, {**config, 'temperature': 1}, scope, resume=True)
    with pytest.raises(ValueError, match='tasks/faults/methods changed'):
        prepare_output(root, config, {**scope, 'tasks': ['T2']}, resume=True)
    prepare_output(root, config, scope, resume=True)
    assert evidence.read_text() == 'keep'
    with pytest.raises(ValueError, match='requires manifest'):
        prepare_output(tmp_path / 'missing', config, scope, resume=True)


def test_partial_fault_resume_reruns_the_paired_group(tmp_path, monkeypatch):
    import run
    task = TASKS[0]
    methods = {'none', 'restart', 'selective'}
    clean, ok, _ = run_clean(task, methods, str(tmp_path))
    fault = next(f for f in schedule(task) if f.name == 'R-incorrect')
    run_fault(task, fault, clean, ok, methods, str(tmp_path))
    saved = tmp_path / 'T1/R-incorrect/none/trace.json'
    old_id = json.loads(saved.read_text())['trace']['run_id']
    (tmp_path / 'T1/R-incorrect/selective/trace.json').unlink()
    rows = run_fault(task, fault, clean, ok, methods, str(tmp_path), resume=True)
    new_id = json.loads(saved.read_text())['trace']['run_id']
    assert new_id != old_id
    assert all(r['source_trace_id'] == new_id for r in rows if r['method'] != 'none')
    def no_work(*args, **kwargs):
        raise AssertionError('complete group should be reused')
    monkeypatch.setattr(run, 'run_workflow', no_work)
    assert run_fault(task, fault, clean, ok, methods, str(tmp_path), resume=True) == rows


@pytest.mark.parametrize('model,root,slug', [
    ('api-muse-glimmer-30b', 'runs', 'api-muse-glimmer-30b'),
    ('org/model:version', 'runs', 'org-model-version'),
    ('deterministic-test-double', 'runs/scratch', 'deterministic-test-double'),
])
def test_default_output_identifies_model_and_keeps_offline_disposable(monkeypatch, model, root, slug):
    import run
    monkeypatch.setattr(run.llm, 'MODEL', model)
    path = Path(run.default_output())
    assert path.parent == Path(root)
    assert path.name.startswith('phase2-' + slug + '-')
    assert path != Path(run.default_output())
