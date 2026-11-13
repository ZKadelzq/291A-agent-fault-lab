import copy
import json
from types import SimpleNamespace

import pytest

from mas import tasks as tasks_mod
from mas.env import World
from mas.faults import schedule
from mas.metrics import episode
from mas.recovery import METHODS
from mas.tasks import TASKS, Task
from mas.trace import Trace, tokens
from mas.workflow import run_workflow


def broken(task, name):
    w = World(task.client_id, task.cash)
    f = next(f for f in schedule(task, extra=True) if f.name == name)
    tr = run_workflow(task, w, fault=f)
    return w, tr, f


@pytest.mark.parametrize('task', TASKS, ids=lambda t: t.id)
@pytest.mark.parametrize('fault', ['R-incorrect', 'R-stale', 'R-timeout', 'E-timeout',
                                    'R-distractor', 'R-malicious', 'R-ipi', 'P-malicious'])
def test_all_strategies(task, fault, monkeypatch):
    monkeypatch.setattr(tasks_mod, 'CASH_MODE', 'ample')
    world, trace, f = broken(task, fault)
    frozen = copy.deepcopy(trace.to_dict())
    original = copy.deepcopy(world.state)
    for name, method in METHODS.items():
        w = copy.deepcopy(world)
        with monkeypatch.context() as m:
            m.setattr(Task, 'expected', lambda self: (_ for _ in ()).throw(AssertionError('oracle used')))
            new, stats = method(task, w, trace, f)
        assert stats['recovery_status'] == 'reconstructed', (name, stats['errors'])
        checks = task.check(w)
        assert checks['correct'], (name, checks)
        assert checks['safe_success'] == (fault != 'P-malicious')
        assert len(w.state['external']) == (1 if fault == 'P-malicious' else 0)
        assert not any(a['irreversible'] for batch in stats['recovery_audit'] for a in batch['actions'])
        assert stats['recovery_tokens'] == tokens(new.new_cost)
        assert episode(task, new, w, stats['recovery_latency'])['tokens'] == stats['recovery_tokens']
        assert trace.to_dict() == frozen and world.state == original
        assert Trace.from_dict(json.loads(json.dumps(new.to_dict()))).to_dict() == new.to_dict()


def test_change_prunes_identical_plan():
    task = TASKS[0]
    world, trace, f = broken(task, 'R-distractor')
    new, stats = METHODS['change_aware'](task, world, trace, f)
    assert stats['recomputed'] == 2  # one research + plan, no executor
    assert all(n.reused_from for n in new.nodes.values() if n.agent == 'executor')
    assert stats['recovery_tokens'] < tokens(new.total_cost())


def test_changed_plan_retains_only_identity_check():
    world, trace, f = broken(TASKS[0], 'R-incorrect')
    new, stats = METHODS['change_aware'](TASKS[0], world, trace, f)
    assert new.nodes['exec:0'].reused_from
    assert not new.nodes['exec:1'].reused_from
    assert new.nodes['exec:1'].step['args']['ticker'] == 'AAPL'
    assert trace.nodes['exec:1'].step['args']['ticker'] == 'NVDA'


def test_reconcile_exact_delta():
    world, trace, f = broken(TASKS[0], 'R-incorrect')
    new, stats = METHODS['reconcile'](TASKS[0], world, trace, f)
    actions = [a for n in new.nodes.values() if n.agent == 'recovery' for a in n.actions]
    trades = [(a['tool'], a['args']) for a in actions if a['tool'] in {'buy', 'sell'}]
    assert trades == [('sell', {'ticker': 'NVDA', 'shares': 42}),
                      ('buy', {'ticker': 'AAPL', 'amount_usd': 4940.0})]
    assert not any(n.agent == 'executor' for n in new.nodes.values())
    assert TASKS[0].check(world)['safe_success']


def test_stopped_does_not_publish_and_never_silently_skips():
    world, trace, f = broken(TASKS[0], 'E-timeout')
    world.log[-1]['committed'] = None
    before = copy.deepcopy(world.state)
    _, stats = METHODS['selective'](TASKS[0], world, trace, f)
    assert stats['recovery_status'] == 'stopped'
    assert stats['reason'] == 'EFFECT_UNKNOWN'
    assert stats['attempts'] == ['selective']
    assert world.state == before


def test_legacy_fallback_is_bounded_and_accounted():
    world, trace, f = broken(TASKS[0], 'R-incorrect')
    trace.context_hash = None
    trace.checkpoints = {}
    new, stats = METHODS['selective'](TASKS[0], world, trace, f)
    assert stats['recovery_status'] == 'fallback_reconstructed'
    assert stats['attempts'] == ['selective', 'restart']
    assert stats['recovery_tokens'] == tokens(new.total_cost())


@pytest.mark.parametrize('method', list(METHODS))
def test_unlogged_state_change_stops_without_overwrite(method):
    world, trace, f = broken(TASKS[0], 'R-incorrect')
    world.state['cash'] += 17
    before = copy.deepcopy(world.state)
    log_before = copy.deepcopy(world.log)
    _, stats = METHODS[method](TASKS[0], world, trace, f)
    assert stats['recovery_status'] == 'stopped'
    assert stats['reason'] == 'STATE_CONFLICT'
    assert stats['attempts'] == [method]
    assert stats['recovery_tokens'] == 0
    assert world.state == before
    assert world.log == log_before
    assert all(not batch['published'] and not batch['actions'] for batch in stats['recovery_audit'])


def test_missing_receipt_stops():
    world, trace, f = broken(TASKS[0], 'R-incorrect')
    world.log.pop()
    _, stats = METHODS['selective'](TASKS[0], world, trace, f)
    assert stats['recovery_status'] == 'stopped'
    assert stats['reason'] == 'TRACE_INCOMPLETE'


def test_failure_cost_included_once_in_fallback(monkeypatch):
    from mas import agents
    world, trace, f = broken(TASKS[0], 'R-incorrect')
    original = agents.plan
    count = [0]

    def fail_once(*args):
        node = original(*args)  # costs tokens, even when the returned output is unusable
        count[0] += 1
        if count[0] == 1:
            node.parse = 'failed'
        return node

    monkeypatch.setattr(agents, 'plan', fail_once)
    new, stats = METHODS['selective'](TASKS[0], world, trace, f)
    assert stats['recovery_status'] == 'fallback_reconstructed'
    # Failed first attempt: researcher (3 calls) + planner (1), 12 tokens per call.
    assert stats['recovery_tokens'] == tokens(new.total_cost()) + 48
    assert len(stats['recovery_audit']) == 2 and not stats['recovery_audit'][0]['published']


def test_no_fallback_loop_or_failed_candidate_publish(monkeypatch):
    from mas import agents
    world, trace, f = broken(TASKS[0], 'R-incorrect')
    before = copy.deepcopy(world.state)
    original = agents.plan

    def always_bad(*args):
        node = original(*args)
        node.parse = 'failed'
        return node

    monkeypatch.setattr(agents, 'plan', always_bad)
    _, stats = METHODS['selective'](TASKS[0], world, trace, f)
    assert stats['recovery_status'] == 'stopped'
    assert stats['attempts'] == ['selective', 'restart']
    assert stats['recovery_tokens'] > 0 and world.state == before


def test_reconcile_removes_obsolete_stop():
    # T5's wrong MSFT rating adds a stock; correcting it requires removing its stop too.
    task = TASKS[4]
    world, trace, f = broken(task, 'R-incorrect')
    assert 'MSFT' in world.state['positions'] and 'MSFT' in world.state['stop_loss']
    new, stats = METHODS['reconcile'](task, world, trace, f)
    assert stats['recovery_status'] == 'reconstructed'
    assert 'MSFT' not in world.state['positions'] and 'MSFT' not in world.state['stop_loss']
    assert any(a['tool'] == 'clear_stop_loss' for n in new.nodes.values() for a in n.actions)


def test_legacy_cash_rejected_retry_is_not_reused(monkeypatch):
    monkeypatch.setattr(tasks_mod, 'CASH_MODE', 'legacy')
    task = TASKS[1]
    world, trace, f = broken(task, 'E-timeout')
    actions = trace.nodes[f.target].actions
    assert actions[0]['committed'] and not actions[1]['committed']
    new, stats = METHODS['change_aware'](task, world, trace, f)
    assert stats['recovery_status'] == 'reconstructed'
    assert not new.nodes[f.target].reused_from
    assert task.check(world)['safe_success']


def test_planner_attack_still_contains_account_statement():
    world, trace, f = broken(TASKS[0], 'P-malicious')
    mail = next(a for a in world.log if a['tool'] == 'send_email')
    body = json.loads(mail['args']['body'])
    assert body['client'] == TASKS[0].client_id
    assert body['cash'] == TASKS[0].cash
    assert body['positions'] == {}
