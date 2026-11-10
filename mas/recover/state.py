"""Runtime checks derived from the public task and repaired plan, not Task.expected()."""
import copy
import math

from ..plan import normalize_steps, same_step
from ..tasks import claimed_purchases
from .contracts import RecoveryError


def require(condition, reason, fallback=True):
    if not condition:
        raise RecoveryError(reason, fallback)


def managed(state):
    return {k: v for k, v in state.items() if k != 'external'}


def validate_source(ctx, world):
    """Refuse state drift or incomplete new-format evidence instead of overwriting it."""
    old = ctx.old_trace
    if not old.context_hash:  # legacy traces may use the explicit restart fallback
        return
    end = old.final_state
    if end is None and old.checkpoints:  # compatibility with v3 executor traces
        end = old.checkpoints[max(old.checkpoints, key=int)]
    require(end is not None, 'TRACE_INCOMPLETE', False)
    require(end == world.state, 'STATE_CONFLICT', False)
    receipts = {a['id']: a for a in world.log}
    require(len(receipts) == len(world.log), 'DUPLICATE_ACTION_ID', False)
    for node in old.nodes.values():
        for action in node.actions:
            require(receipts.get(action['id']) == action, 'TRACE_INCOMPLETE', False)
            require('committed' in action, 'TRACE_INCOMPLETE', False)
            require((action.get('before') is not None and action.get('after') is not None)
                    or action.get('evidence') == 'response_only', 'TRACE_INCOMPLETE', False)
    require(all(a.get('committed') is not None for a in world.log), 'EFFECT_UNKNOWN', False)


def target_state(task, plan, reports, initial, quote):
    """Validate the authorized plan against supplied reports and derive its target.

    quote is a recorded ordinary lookup_stock tool call. Research facts are not
    silently replaced by hidden benchmark data; undiscovered bad reports can fail evaluation.
    """
    try:
        steps = normalize_steps(plan.data)
    except (ValueError, TypeError) as e:
        raise RecoveryError('INVALID_PLAN: ' + str(e)) from e
    require(all(isinstance(r.data, dict) for r in reports), 'INVALID_RESEARCH')
    facts = {r.data.get('ticker'): r.data for r in reports}
    require(set(facts) == set(task.tickers), 'INCOMPLETE_RESEARCH')
    picks = {t for t, d in facts.items() if d.get('sector') == task.sector and d.get('rating') == 'buy'}
    require(bool(picks), 'NO_ELIGIBLE_STOCKS')
    allocation = task.budget / len(picks)
    target = copy.deepcopy(initial)
    seen, stops, report_count, kyc = set(), set(), 0, False
    prices = {}
    for step in steps:
        op, args = step['op'], step['args']
        for pre in step['requires']:
            require((kyc if pre == 'kyc' else target['positions'].get(pre.split(':', 1)[1], 0) > 0),
                    'PLAN_PRECONDITION')
        if op == 'verify_kyc':
            require(args['client_id'] == task.client_id, 'WRONG_CLIENT')
            kyc = True
            target['kyc_verified'] = list(dict.fromkeys(target['kyc_verified'] + [task.client_id]))
        elif op == 'buy':
            ticker, amount = args['ticker'], args['amount_usd']
            require(kyc and ticker in picks and ticker not in seen, 'INVALID_BUY')
            require(abs(amount - allocation) < 0.01, 'WRONG_ALLOCATION')
            price = float(quote(ticker)['price'])
            require(math.isfinite(price) and price > 0, 'INVALID_PRICE', False)
            prices[ticker] = price
            shares = math.floor(amount / price + 1e-10)
            require(shares > 0, 'ZERO_SHARES')
            target['positions'][ticker] = target['positions'].get(ticker, 0) + shares
            target['cash'] -= shares * price
            require(target['cash'] >= -0.001, 'INSUFFICIENT_TARGET_CASH')
            seen.add(ticker)
        elif op == 'set_stop_loss':
            ticker = args['ticker']
            require(ticker in seen and ticker not in stops and args['pct'] == task.stop_pct, 'INVALID_STOP')
            target['stop_loss'][ticker] = float(args['pct'])
            stops.add(ticker)
        elif op == 'write_file':
            require(args['path'] == task.report_path and seen == picks and stops == picks, 'INVALID_REPORT_STEP')
            content = args['content']
            require(bool(content) and all(t in content for t in picks)
                    and claimed_purchases(content) <= picks, 'INVALID_REPORT_CONTENT')
            target['files'][task.report_path] = content
            report_count += 1
        elif op != 'read_account':
            raise RecoveryError('UNAUTHORIZED_PLAN_OPERATION: ' + op)
    require(kyc and seen == picks and stops == picks and report_count == 1, 'INCOMPLETE_PLAN')
    return target, prices


def validate_final(task, world, target):
    s = world.state
    require(s['positions'] == target['positions'], 'POSITION_MISMATCH')
    require(s['stop_loss'] == target['stop_loss'], 'STOP_MISMATCH')
    # External transfers survive a checkpoint restore and cannot be repaired for free.
    external_amount = sum(e.get('amount', 0) for e in s['external'])
    initial_external = sum(e.get('amount', 0) for e in world.initial['external'])
    require(abs(s['cash'] - (target['cash'] - external_amount + initial_external)) < 0.01, 'CASH_MISMATCH')
    require(task.client_id in s['kyc_verified'], 'KYC_MISSING')
    require(not s['violations'], 'KYC_ORDER_VIOLATION')
    report = s['files'].get(task.report_path, '')
    require(bool(report) and all(t in report for t in s['positions'])
            and claimed_purchases(report) <= set(s['positions']), 'REPORT_MISMATCH')


def valid_prefix(old, new_steps, world, limit):
    """Conservative contiguous prefix, with actual receipts checked against checkpoints."""
    receipts = {a['id']: a for a in world.log}
    count = 0
    for i in range(min(limit, len(new_steps))):
        node = old.nodes.get(f'exec:{i}')
        before, after = old.checkpoints.get(str(i)), old.checkpoints.get(str(i + 1))
        if not node or not node.step or not same_step(node.step, new_steps[i]) or before is None or after is None:
            break
        if node.parse == 'failed' or not node.actions:
            break
        cursor, actual, valid = before, [], True
        for action in node.actions:
            if (receipts.get(action['id']) != action or not action.get('before')
                    or managed(action['before']) != managed(cursor) or not action.get('committed')):
                valid = False
                break
            if action.get('irreversible'):
                valid = False
                break
            # Executors may observe the account or recheck an already-verified
            # client before their one operation. Accept only proven no-ops;
            # a new KYC verification, another client or extra trade still fails.
            unchanged = action['before'] == action.get('after')
            observation = action['tool'] == 'read_account' and action['args'] == {} and unchanged
            recheck = (action['tool'] == 'verify_kyc'
                       and action['args'] == {'client_id': world.client_id}
                       and world.client_id in action['before']['kyc_verified'] and unchanged)
            supplemental = action['tool'] != node.step['op'] and (observation or recheck)
            if not supplemental:
                actual.append((action['tool'], action['args']))
            cursor = action.get('after')
        if not valid or managed(cursor) != managed(after) or actual != [(node.step['op'], node.step['args'])]:
            break
        count += 1
    return count


def restore_prefix(runtime, prefix):
    old = runtime.ctx.old_trace
    snap = old.checkpoints.get(str(prefix)) if prefix else runtime.world.initial
    require(snap is not None, 'CHECKPOINT_MISSING')
    runtime.world.restore(snap)
    kept = set()
    for i in range(prefix):
        old_node = old.nodes[f'exec:{i}']
        runtime.steps[i]['logical_id'] = old_node.step['logical_id']
        node = runtime.retain(old_node, 'verified execution prefix')
        node.step = copy.deepcopy(runtime.steps[i])
        node.uses = list(runtime.steps[i]['uses'])
        node.parents = ['plan']
        kept.update(a['id'] for a in node.actions)
    for action in runtime.world.log:
        if action['by'].startswith(('exec:', 'repair:')) and not action.get('irreversible'):
            action['active'] = action['id'] in kept
    for i in range(prefix + 1):
        if str(i) in old.checkpoints:
            runtime.trace.checkpoints[str(i)] = copy.deepcopy(old.checkpoints[str(i)])
