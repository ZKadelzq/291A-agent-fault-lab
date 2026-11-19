"""Focused regression tests for T6's report paraphrase during recovery.

Offline tests: no Muse API calls and no real financial transactions.
"""
import copy
import json

from mas import agents, llm
from mas.env import World
from mas.plan import action_matches, normalize_steps
from mas.tasks import TASKS
from mas.trace import Node, Trace
from mas.recover.state import valid_prefix


def t6_report_step():
    task = next(t for t in TASKS if t.id == 'T6')
    return task, normalize_steps([{'op': 'write_file', 'args': {
        'path': task.report_path,
        'content': 'Client C-106 investment report. Bought XOM. Stop-loss 10%.\n',
    }}])[0]


def test_t6_wrong_report_is_rejected_then_corrected(monkeypatch):
    from conftest import message, tool

    task, step = t6_report_step()
    w = World(task.client_id, task.cash)
    w.recovery_mode = True

    def changed_report(messages, tools=None, **kwargs):
        replies = [m for m in messages if m['role'] == 'tool']
        asked_to_correct = any(m['role'] == 'user' and 'rejected BEFORE execution' in m['content']
                               for m in messages)
        if not replies:
            # Model paraphrases the approved report body; this must not write a file.
            return tool('write_file', {'path': task.report_path, 'content': 'Bought XOM.'})
        if asked_to_correct and len(replies) == 1:
            return tool('write_file', step['args'])
        return message('Done.')

    monkeypatch.setattr(llm, 'chat', changed_report)
    node = agents.execute(task, 0, step, w)
    assert len(node.actions) == 2
    refused, accepted = node.actions
    assert refused['rejected_contract'] and refused['committed'] is False
    assert refused['before'] == refused['after']
    assert not accepted['rejected_contract'] and accepted['committed'] is True
    assert action_matches(step, accepted['tool'], accepted['args'])
    assert w.state['files'][task.report_path] == step['args']['content']

    trace = Trace(task.id)
    trace.checkpoints['0'] = copy.deepcopy(w.initial)
    trace.checkpoints['1'] = w.snapshot()
    trace.add(node)
    assert valid_prefix(trace, [step], w, 1) == 1


def test_t6_wrong_report_without_correction_fails_closed(monkeypatch):
    from conftest import message, tool

    task, step = t6_report_step()
    w = World(task.client_id, task.cash)
    w.recovery_mode = True

    def never_correct(messages, tools=None, **kwargs):
        if not any(m['role'] == 'tool' for m in messages):
            return tool('write_file', {'path': task.report_path, 'content': 'Different content.'})
        return message('Done without retrying.')

    monkeypatch.setattr(llm, 'chat', never_correct)
    node = agents.execute(task, 0, step, w)
    assert all(not a['committed'] for a in node.actions)
    assert task.report_path not in w.state['files']
    trace = Trace(task.id)
    trace.checkpoints['0'] = copy.deepcopy(w.initial)
    trace.checkpoints['1'] = w.snapshot()
    trace.add(node)
    assert valid_prefix(trace, [step], w, 1) == 0


def test_t6_strict_trades_and_report_formatting_only():
    _, step = t6_report_step()
    assert action_matches(step, 'write_file', {
        'path': step['args']['path'], 'content': step['args']['content'].replace('\n', '\r\n').rstrip('\r\n')})
    assert not action_matches(step, 'write_file', {'path': step['args']['path'], 'content': 'Bought XOM.'})
    buy = normalize_steps([{'op': 'buy', 'args': {'ticker': 'XOM', 'amount_usd': 7000}}])[0]
    assert not action_matches(buy, 'buy', {'ticker': 'XOM', 'amount_usd': 6999})
    assert not action_matches(buy, 'sell', {'ticker': 'XOM', 'shares': 1})


def test_wrong_trade_amount_cannot_commit_before_bounded_correction(monkeypatch):
    """The optional contract guard is exercised with a forced Executor deviation.

    No A/B feature flag or extra official fault category is needed.
    """
    from conftest import message, tool

    task = TASKS[0]
    step = normalize_steps([{'op': 'buy', 'args': {'ticker': 'AAPL', 'amount_usd': 5000}}])[0]
    w = World(task.client_id, 20000)
    w.call('verify_kyc', {'client_id': task.client_id}, 'setup')
    w.recovery_mode = True

    def wrong_then_correct(messages, tools=None, **kwargs):
        replies = [m for m in messages if m['role'] == 'tool']
        correction = any(m['role'] == 'user' and 'rejected BEFORE execution' in m['content']
                         for m in messages)
        if not replies:
            return tool('buy', {'ticker': 'AAPL', 'amount_usd': 6000})
        if correction and len(replies) == 1:
            return tool('buy', step['args'])
        return message('Completed.')

    monkeypatch.setattr(llm, 'chat', wrong_then_correct)
    node = agents.execute(task, 0, step, w)
    assert [a['rejected_contract'] for a in node.actions] == [True, False]
    assert node.actions[0]['before'] == node.actions[0]['after']
    assert not node.actions[0]['committed'] and node.actions[1]['committed']
    assert w.state['positions']['AAPL'] == 26  # $5000 / $190, NOT $6000 / $190
    assert w.state['cash'] == 20000 - 26 * 190
