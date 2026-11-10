import copy

import pytest

from mas.env import World
from mas.faults import Fault, mutate, schedule
from mas.recovery import METHODS, STRATEGIES
from mas.recover.evidence import recovery_inputs
from mas.tasks import TASKS
from mas.trace import Trace
from mas.workflow import run_workflow


def timeout_world(mode):
    task = TASKS[0]
    world = World(task.client_id, task.cash)
    fault = Fault('E-timeout', 'timeout', 'exec:*', tool='buy', timeout_outcome=mode)
    trace = run_workflow(task, world, fault=fault)
    return world, trace, fault


@pytest.mark.parametrize('mode', ['committed', 'not_committed', 'unknown'])
def test_recovery_view_cannot_read_simulator_timeout_truth(mode):
    world, trace, fault = timeout_world(mode)
    original = copy.deepcopy(trace.to_dict())
    old, source = recovery_inputs(trace, world)
    hit = next(a for a in old.nodes[fault.target].actions if a.get('outcome_unknown'))
    receipt = next(a for a in source.log if a['id'] == hit['id'])
    assert receipt == hit
    assert receipt['committed'] is None and receipt['writes'] == []
    assert receipt['before'] is None and receipt['after'] is None
    assert 'raw_result' not in receipt and 'state_changed' not in receipt
    assert source.audit_log == [] and source._order_outcomes == {} and source._unavailable_orders == set()
    assert max(map(int, old.checkpoints)) <= int(fault.target.split(':')[1])
    index = next(i for i, a in enumerate(source.log) if a['id'] == hit['id'])
    assert all(a['before'] is None and a['after'] is None for a in source.log[index:])
    assert trace.to_dict() == original
    actual = next(a for a in world.audit_log if a['id'] == hit['id'])
    assert actual['committed'] == (mode != 'not_committed')


@pytest.mark.parametrize('method', list(METHODS))
@pytest.mark.parametrize('mode', ['committed', 'not_committed', 'unknown'])
def test_timeout_needs_explicit_status_query(method, mode, monkeypatch):
    world, trace, fault = timeout_world(mode)
    before = copy.deepcopy(world.state)
    query, build = world.query_order_status, STRATEGIES[method].build
    queried, built = [], []

    def spy_query(request_id):
        queried.append(request_id)
        return query(request_id)

    def spy_build(ctx, runtime):
        built.append(True)
        assert runtime.world._order_outcomes == {} and runtime.world.audit_log == []
        return build(ctx, runtime)

    monkeypatch.setattr(world, 'query_order_status', spy_query)
    monkeypatch.setattr(STRATEGIES[method], 'build', spy_build)
    new, stats = METHODS[method](TASKS[0], world, trace, fault)
    assert len(queried) == 1
    assert stats['recovery_status_queries'][0]['status'] == mode
    if mode == 'unknown':
        assert stats['recovery_status'] == 'stopped' and stats['reason'] == 'EFFECT_UNKNOWN'
        assert stats['attempts'] == [method] and stats['recovery_tokens'] == 0
        assert built == [] and world.state == before
        assert all(not batch['actions'] for batch in stats['recovery_audit'])
    else:
        assert stats['recovery_status'] == 'reconstructed'
        assert TASKS[0].check(world)['correct']
        assert new.final_state == world.state


def test_query_error_stops_without_fallback(monkeypatch):
    world, trace, fault = timeout_world('committed')

    def unavailable(_):
        raise TimeoutError('status service unavailable')

    monkeypatch.setattr(world, 'query_order_status', unavailable)
    _, stats = METHODS['reconcile'](TASKS[0], world, trace, fault)
    assert stats['reason'] == 'EFFECT_UNKNOWN' and stats['attempts'] == ['reconcile']
    assert stats['recovery_status_queries'][0]['error'] == 'TimeoutError'


@pytest.mark.parametrize('second_method', list(METHODS))
def test_reconcile_terminal_evidence_supports_next_recovery(second_method):
    task = TASKS[0]
    world = World(task.client_id, task.cash)
    fault = next(f for f in schedule(task) if f.name == 'R-incorrect')
    original = run_workflow(task, world, fault=fault)
    first, stats = METHODS['reconcile'](task, world, original, fault)
    assert stats['recovery_status'] == 'reconstructed'
    assert first.checkpoints == {} and first.final_state == world.state
    first = Trace.from_dict(first.to_dict())  # terminal evidence survives disk format
    again = fault.fresh()
    mutate(again, first.nodes[again.target], task.client_id, world=world)
    final, stats = METHODS[second_method](task, world, first, again)
    assert stats['recovery_status'] == 'reconstructed', stats['errors']
    assert task.check(world)['safe_success'] and final.final_state == world.state


def test_reconcile_still_refuses_unrecorded_drift():
    world, trace, fault = timeout_world('not_committed')
    world.state['cash'] += 20
    before = world.snapshot()
    _, stats = METHODS['reconcile'](TASKS[0], world, trace, fault)
    assert stats['reason'] == 'STATE_CONFLICT' and stats['recovery_tokens'] == 0
    assert world.state == before


def test_modern_trace_without_any_terminal_evidence_stops():
    world, trace, fault = timeout_world('not_committed')
    trace.final_state, trace.checkpoints = None, {}
    _, stats = METHODS['restart'](TASKS[0], world, trace, fault)
    assert stats['reason'] == 'TRACE_INCOMPLETE'
