"""Shared lifecycle: isolate, repair, validate, publish; account only new model work."""
import copy
import json
import time
import uuid

from .. import agents
from ..plan import normalize_steps
from ..trace import Node, Trace, add_cost, zero_cost, capture_cost, tokens
from ..workflow import execute_steps
from .contracts import RecoveryCandidate, RecoveryContext, RecoveryError, RecoveryResult
from .state import require, target_state, validate_final, restore_prefix, validate_source, valid_prefix
from .evidence import resolve_outcomes


class RecoveryRuntime:
    def __init__(self, ctx, source):
        self.ctx = ctx
        self.world = copy.deepcopy(source)
        self.world.tool_hook = None
        self.world.recovery_mode = True
        self.world.branch_id = uuid.uuid4().hex
        self.trace = Trace(ctx.task.id)
        self.trace.source_trace_id = ctx.old_trace.run_id
        self.trace.context_hash = agents.config_signature(ctx.task)
        self.decisions = []
        self.invalid = {ctx.target} | ctx.old_trace.descendants(ctx.target)
        self.reports, self.steps, self.target, self.prices = [], [], None, {}
        self.new_actions_start = len(self.world.log)

    def retain(self, old, reason):
        node = copy.deepcopy(old)
        node.reused_from = f'{self.ctx.old_trace.run_id}/{old.id}'
        self.trace.add(node)
        self.decisions.append({'node': node.id, 'decision': 'retained', 'reason': reason})
        return node

    def produce(self, nid, make):
        node = make()
        node.supersedes = f'{self.ctx.old_trace.run_id}/{nid}' if nid in self.ctx.old_trace.nodes else None
        self.trace.add(node)
        self.decisions.append({'node': nid, 'decision': 'recomputed', 'reason': 'new computation'})
        require(node.parse != 'failed', 'UNUSABLE_AGENT_OUTPUT')
        return node

    def prepare(self, restart=False):
        ctx, old = self.ctx, self.ctx.old_trace
        node = old.nodes.get(ctx.target)
        require(node is not None or restart, 'UNKNOWN_TARGET', False)
        compatible = old.context_hash == self.trace.context_hash
        if not restart:
            require(compatible, 'CONTEXT_OR_SCHEMA_CHANGED')
        for ticker in ctx.task.tickers:
            nid = f'research:{ticker}'
            prior = old.nodes.get(nid)
            if not restart and nid not in self.invalid and prior and prior.parse != 'failed':
                report = self.retain(prior, 'independent research')
            else:
                report = self.produce(nid, lambda t=ticker: agents.research(ctx.task, t, self.world))
            self.reports.append(report)
        if not restart and 'plan' not in self.invalid and node.agent == 'executor' and old.nodes.get('plan'):
            plan = self.retain(old.nodes['plan'], 'executor fault leaves plan input unchanged')
        else:
            plan = self.produce('plan', lambda: agents.plan(ctx.task, self.reports))
        try:
            self.steps = normalize_steps(plan.data)
        except (ValueError, TypeError) as e:
            raise RecoveryError('INVALID_PLAN') from e
        plan.data = self.steps
        self.target, self.prices = target_state(ctx.task, plan, self.reports, self.world.initial, self.quote)

    def observe(self, tool, args):
        nid = 'recovery:observe'
        if nid not in self.trace.nodes:
            self.trace.add(Node(nid, 'observer', ['plan'], parse='n/a'))
        rec = self.world.call(tool, args, nid)
        self.trace.nodes[nid].actions.append(rec)
        require(rec['committed'], 'OBSERVATION_FAILED', False)
        return json.loads(rec['result'])

    def quote(self, ticker):
        return self.observe('lookup_stock', {'ticker': ticker})

    def rerun_suffix(self, prefix):
        restore_prefix(self, prefix)
        execute_steps(self.ctx.task, self.world, self.trace, self.steps, self.produce, start=prefix)

    def compensate(self, tool, args, reason):
        """Only strategy code can authorize an exact, already-computed delta."""
        nid = f'repair:{sum(n.agent == "recovery" for n in self.trace.nodes.values())}'
        self.world.operation_id = uuid.uuid4().hex
        self.world._permit = (tool, copy.deepcopy(args))
        try:
            action = self.world.call(tool, args, nid)
        finally:
            self.world.operation_id = None
            self.world._permit = None
        self.trace.add(Node(nid, 'recovery', ['plan'], content=reason, actions=[action], parse='n/a'))
        self.decisions.append({'node': nid, 'decision': 'compensated', 'reason': reason})
        require(action['committed'], 'COMPENSATION_FAILED')

    def candidate(self):
        return RecoveryCandidate(self.trace, self.decisions)

    def validate(self):
        for action in self.world.log[self.new_actions_start:]:
            require(not action.get('irreversible'), 'EXTERNAL_OPERATION_BLOCKED', False)
            require(action.get('committed'), 'RECOVERY_ACTION_FAILED')
        if any(n.agent == 'executor' for n in self.trace.nodes.values()):
            require(valid_prefix(self.trace, self.steps, self.world, len(self.steps)) == len(self.steps),
                    'EXECUTION_CONTRACT_MISMATCH')
        validate_final(self.ctx.task, self.world, self.target)


def recover(ctx, source, strategy, status_reader=None):
    from .selective import Restart

    start = time.monotonic()
    attempts, cost, errors, audit = [], zero_cost(), [], []
    queries = resolve_outcomes(ctx.old_trace, source, status_reader)
    result_trace, status, effective = ctx.old_trace, 'stopped', strategy.name
    # Each attempt starts from the same source, never from a failed candidate.
    choices = [strategy] + ([Restart()] if ctx.allow_fallback and strategy.name != 'restart' else [])
    for selected in choices:
        runtime = RecoveryRuntime(ctx, source)
        attempts.append(selected.name)
        failure = None
        with capture_cost() as usage:
            try:
                # Existing evidence may be absent (legacy -> restart), but unknown commit status cannot be hidden.
                require(not any(a.get('committed') is None for a in source.log if 'committed' in a),
                        'EFFECT_UNKNOWN', False)
                validate_source(ctx, source)
                candidate = selected.build(ctx, runtime)
                runtime.validate()
                runtime.trace.final_state = runtime.world.snapshot()
            except RecoveryError as e:
                failure = e
            except Exception as e:
                failure = RecoveryError('UNEXPECTED_' + type(e).__name__, False)
        for u in usage:
            cost = add_cost(cost, u)
        events = copy.deepcopy(runtime.world.log[runtime.new_actions_start:])
        audit.append({'strategy': selected.name, 'published': failure is None, 'actions': events})
        if failure:
            errors.append(failure.reason)
            if not failure.fallback:
                break
            continue
        source.state = copy.deepcopy(runtime.world.state)
        source.log = copy.deepcopy(runtime.world.log)
        source.audit_log = copy.deepcopy(runtime.world.audit_log)
        source._order_outcomes = copy.deepcopy(runtime.world._order_outcomes)
        source.tool_hook = None
        result_trace = candidate.trace
        status = 'reconstructed' if len(attempts) == 1 else 'fallback_reconstructed'
        effective = selected.name
        break
    elapsed = time.monotonic() - start
    cost['latency'] = elapsed
    # Never mutate the original failed trace, including a stopped result.
    result_trace = copy.deepcopy(result_trace)
    result_trace.new_cost = cost
    decisions = runtime.decisions if status != 'stopped' else []
    stats = {'recovery_status': status, 'requested_strategy': strategy.name, 'effective_strategy': effective,
             'reason': errors[-1] if errors else None, 'attempts': attempts, 'errors': errors,
             'recovery_latency': elapsed, 'recovery_cost': cost, 'recovery_tokens': tokens(cost),
             'recomputed': sum(d['decision'] == 'recomputed' for d in decisions),
             'retained': sum(d['decision'] == 'retained' for d in decisions),
             'compensations': sum(d['decision'] == 'compensated' for d in decisions),
             'unrecoverable': len(source.state['external']), 'decisions': decisions,
             'invalidated': sorted(runtime.invalid),
             'recovery_status_queries': queries,
             'recovery_audit': audit, 'source_trace_id': ctx.old_trace.run_id}
    return RecoveryResult(result_trace, stats)
