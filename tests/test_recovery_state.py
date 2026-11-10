import copy
import json

import pytest

from mas.env import World
from mas.plan import normalize_steps, same_step
from mas.trace import Trace, Node
from mas.tasks import TASKS
from mas.workflow import run_workflow
from mas.recovery import METHODS
from mas.faults import schedule


def test_cash_and_positions_are_dependencies():
    w = World('C-101', 15000)
    w.call('verify_kyc', {'client_id': 'C-101'}, 'kyc')
    a = w.call('buy', {'ticker': 'AAPL', 'amount_usd': 5000}, 'a')
    b = w.call('buy', {'ticker': 'NVDA', 'amount_usd': 5000}, 'b')
    tr = Trace('test')
    tr.add(Node('a', 'executor', [], actions=[a]))
    tr.add(Node('b', 'executor', [], actions=[b]))
    assert 'cash' in b['reads'] and 'positions.NVDA' in b['reads']
    assert ('a', 'b') in tr.edges()
    w.restore(a['before'])
    w.call('buy', {'ticker': 'NVDA', 'amount_usd': 5000}, 'b2')
    assert w.state['cash'] == 10080


def test_rollback_preserves_each_external_event_once():
    w = World('C-101', 15000)
    for _ in range(2):
        w.call('transfer_funds', {'to_account': 'external', 'amount_usd': 50}, 'x')
    w.restore(w.initial)
    w.restore(w.initial)
    assert w.state['cash'] == 14900
    assert len(w.state['external']) == 2
    assert len({e['id'] for e in w.state['external']}) == 2


def test_compensation_cannot_be_authorized_by_tool_arguments():
    w = World('C-101', 15000)
    w.call('verify_kyc', {'client_id': 'C-101'}, 'kyc')
    w.call('buy', {'ticker': 'AAPL', 'amount_usd': 5000}, 'a')
    before = w.snapshot()
    w.recovery_mode = True
    a = w.call('sell', {'ticker': 'AAPL', 'shares': 1, 'phase': 'recovery'}, 'attacker')
    assert not a['committed'] and not a['recovery_authorized']
    assert w.state == before
    assert not w.call('send_email', {'to': 'x', 'subject': 'x', 'body': 'x'}, 'attacker')['committed']


@pytest.mark.parametrize('shares', [-1, 0, 0.5, 1000])
def test_sell_invalid_quantity_does_not_mutate(shares):
    w = World('C-101', 15000)
    before = w.snapshot()
    assert not w.call('sell', {'ticker': 'AAPL', 'shares': shares}, 'x')['committed']
    assert w.state == before


def test_contract_rejects_contradictory_instructions():
    with pytest.raises(ValueError):
        normalize_steps([{'op': 'buy', 'args': {'ticker': 'AAPL', 'amount_usd': 5000},
                          'instruction': 'Do not buy AAPL'}])
    a = normalize_steps([{'op': 'buy', 'args': {'ticker': 'AAPL', 'amount_usd': 5000}}])[0]
    b = normalize_steps([{'op': 'sell', 'args': {'ticker': 'AAPL', 'shares': 5000}}])[0]
    assert not same_step(a, b)


def test_fault_history_survives_kyc_rollback():
    w = World('C-101', TASKS[0].cash)
    w.call('buy', {'ticker': 'AAPL', 'amount_usd': 190}, 'wrong')
    w.restore(w.initial)
    run_workflow(TASKS[0], w)
    result = TASKS[0].check(w)
    assert result['correct'] and not result['safe_success'] and result['historical_violations'] == 1


def test_multiple_strategies_order_independent():
    task = TASKS[0]
    world = World(task.client_id, task.cash)
    fault = next(f for f in schedule(task) if f.name == 'R-incorrect')
    trace = run_workflow(task, world, fault=fault)
    final = {}
    for names in [list(METHODS), list(reversed(METHODS))]:
        for name in names:
            w = copy.deepcopy(world)
            new, stats = METHODS[name](task, w, trace, fault)
            result = (w.state, stats['recovery_tokens'], stats['retained'])
            if name in final:
                assert final[name] == result
            final[name] = result


@pytest.mark.parametrize('verified,client,expected', [
    (['C-101'], 'C-101', 1),
    ([], 'C-101', 0),
    (['C-101'], 'C-102', 0),
    (['C-101', 'C-102'], 'C-102', 0),
])
def test_prefix_only_accepts_noop_kyc_for_the_same_client(verified, client, expected):
    from mas.recover.state import valid_prefix
    w = World('C-101', 15000)
    for prior in verified:
        w.call('verify_kyc', {'client_id': prior}, 'setup')
    step = normalize_steps([{'op': 'buy', 'args': {'ticker': 'AAPL', 'amount_usd': 5000}}])[0]
    trace = Trace('test')
    trace.checkpoints['0'] = w.snapshot()
    actions = [w.call('verify_kyc', {'client_id': client}, 'exec:0'),
               w.call('buy', step['args'], 'exec:0')]
    trace.add(Node('exec:0', 'executor', ['plan'], step=step, actions=actions, parse='n/a'))
    trace.checkpoints['1'] = w.snapshot()
    assert valid_prefix(trace, [step], w, 1) == expected


def test_realistic_executor_kyc_recheck_does_not_block_recovery(monkeypatch):
    import re
    from conftest import fake_chat, tool
    from mas import llm

    def chat(messages, tools=None, **kwargs):
        if 'You are the Executor' in messages[0]['content']:
            user = next(m['content'] for m in messages if m['role'] == 'user')
            step = json.loads(user.split('Execute this structured operation: ', 1)[1])
            replies = [m for m in messages if m['role'] == 'tool']
            if step['op'] == 'buy':
                if not replies:
                    client = re.search(r'C-\d+', messages[0]['content']).group()
                    return tool('verify_kyc', {'client_id': client})
                if len(replies) == 1:
                    return tool('buy', step['args'])
        return fake_chat(messages, tools, **kwargs)

    monkeypatch.setattr(llm, 'chat', chat)
    task = TASKS[0]
    world = World(task.client_id, task.cash)
    fault = next(f for f in schedule(task) if f.name == 'R-incorrect')
    trace = run_workflow(task, world, fault=fault)
    for method in METHODS.values():
        candidate = copy.deepcopy(world)
        _, stats = method(task, candidate, trace, fault)
        assert stats['recovery_status'] == 'reconstructed', stats['errors']
        assert task.check(candidate)['safe_success']
