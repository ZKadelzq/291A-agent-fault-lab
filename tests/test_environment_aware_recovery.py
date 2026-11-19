"""Controlled environment-drift tests using the deterministic LLM double.

No API calls and no real trading. The market changes only after the faulty
execution finishes. Every recovery strategy sees the same pinned new feed.
"""
import copy
import pytest

from mas.env import World, MARKET
from mas.faults import schedule
from mas.recovery import METHODS
from mas.tasks import TASKS
from mas.trace import Trace
from mas.workflow import run_workflow


def broken(task, fault_name):
    w = World(task.client_id, task.cash)
    fault = next(f for f in schedule(task, extra=True) if f.name == fault_name)
    trace = run_workflow(task, w, fault=fault)
    return w, trace, fault


def test_market_feed_is_per_world_not_global():
    one = World('C-101', 20000)
    two = World('C-101', 20000)
    original = MARKET['AAPL']['price']
    assert one.update_market('AAPL', price=200, rating='hold')
    assert one.market_epoch == 1
    assert one.market_versions['AAPL'] == 1
    assert two.market_versions['AAPL'] == 0
    assert one.market['AAPL']['price'] == 200
    assert two.market['AAPL']['price'] == original == MARKET['AAPL']['price']
    assert one.call('lookup_stock', {'ticker': 'AAPL'}, 'r')['result'] != \
           two.call('lookup_stock', {'ticker': 'AAPL'}, 'r')['result']


@pytest.mark.parametrize('method', ['restart', 'selective', 'change_aware', 'reconcile'])
def test_rating_change_causes_real_replan(method):
    task = TASKS[0]
    w, old, fault = broken(task, 'E-timeout')
    assert 'AAPL' in w.state['positions']
    old_data = copy.deepcopy(old.to_dict())
    w.update_market('AAPL', rating='hold')
    recovered, stats = METHODS[method](task, w, old, fault)
    assert stats['recovery_status'] in {'reconstructed', 'fallback_reconstructed'}, (method, stats)
    assert task.check(w)['safe_success'], (method, task.check(w), stats)
    assert set(w.state['positions']) == {'NVDA'}
    assert w.market['AAPL']['rating'] == 'hold'
    assert old.to_dict() == old_data
    assert stats['market_changed'] == ([] if method == 'restart' else ['AAPL'])
    assert recovered.market_versions['AAPL'] == 1


@pytest.mark.parametrize('method', ['restart', 'selective', 'change_aware'])
def test_price_change_forbids_reusing_old_buy(method):
    task = TASKS[0]
    w, old, fault = broken(task, 'E-timeout')
    old_position = old.final_state['positions']['AAPL']
    w.update_market('AAPL', price=200)
    recovered, stats = METHODS[method](task, w, old, fault)
    assert stats['recovery_status'] == 'reconstructed', stats
    assert task.check(w)['safe_success'], (method, stats)
    assert w.state['positions']['AAPL'] == 25 != old_position
    if method == 'change_aware':
        assert not recovered.nodes['exec:1'].reused_from
    # Trading uses execution prices, not the latest mark-to-market price.
    assert abs(w.state['cash'] + w.state['trade_net_spent'] - task.cash) < 1e-5


def test_unrelated_news_change_reuses_unchanged_executor_prefix():
    task = TASKS[0]
    w, old, fault = broken(task, 'R-distractor')
    old_news = w.news['INTC']
    w.update_market('INTC', news=old_news + ' New headline.')
    new, stats = METHODS['change_aware'](task, w, old, fault)
    assert stats['recovery_status'] == 'reconstructed'
    assert stats['market_changed'] == ['INTC']
    assert task.check(w)['safe_success']
    assert all(node.reused_from for node in new.nodes.values() if node.agent == 'executor')
    assert new.nodes['research:INTC'].reused_from is None


def test_market_update_does_not_rewrite_old_historical_cashflows():
    task = TASKS[0]
    w = World(task.client_id, task.cash)
    tr = run_workflow(task, w)
    old_trade_cost = w.state['trade_net_spent']
    w.update_market('AAPL', price=220)
    assert w.state['trade_net_spent'] == old_trade_cost
    assert task.check(w)['checks']['funds_intact']
    assert tr.market_versions['AAPL'] == 0


def test_missing_market_revisions_fail_closed_or_restart():
    task = TASKS[0]
    w, tr, fault = broken(task, 'R-incorrect')
    tr.market_versions = None
    tr.news_versions = None
    _, stats = METHODS['change_aware'](task, w, tr, fault)
    assert stats['recovery_status'] in {'stopped', 'fallback_reconstructed'}
    assert 'MARKET_VERSION_UNAVAILABLE' in stats['errors']


def test_out_of_band_feed_change_cannot_silently_reuse_artifacts():
    """Fingerprint check also detects callers bypassing update_market()."""
    task = TASKS[0]
    w, old, fault = broken(task, 'E-timeout')
    assert w.market_versions['AAPL'] == 0
    w.market['AAPL']['rating'] = 'hold'  # discouraged API bypass
    new, stats = METHODS['change_aware'](task, w, old, fault)
    assert stats['recovery_status'] == 'reconstructed'
    assert stats['market_changed'] == ['AAPL']
    assert task.check(w)['safe_success']
    assert 'AAPL' not in w.state['positions']


def test_recovery_exposes_lightweight_overhead_metrics():
    task = TASKS[0]
    w, old, fault = broken(task, 'R-distractor')
    _, stats = METHODS['change_aware'](task, w, old, fault)
    for key in ('dependency_analysis', 'market_validation', 'reuse_validation'):
        assert isinstance(stats['recovery_overhead_s'][key], float)
        assert stats['recovery_overhead_s'][key] >= 0
    assert stats['trace_overhead_s']['trace_add'] >= 0
    assert stats['trace_overhead_s']['checkpoint'] >= 0
