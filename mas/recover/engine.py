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
        self.trace.market_versions = copy.deepcopy(self.world.market_versions)
        self.trace.news_versions = copy.deepcopy(self.world.news_versions)
        self.trace.feed_fingerprints = self.world.feed_fingerprints()
        self.decisions = []
        self.market_changed = set()
        t0 = time.perf_counter()
        self.invalid = {ctx.target} | ctx.old_trace.descendants(ctx.target)
        graph_time = time.perf_counter() - t0
        self._overhead_s = {'market_validation': 0.0, 'reuse_validation': 0.0,
                            'dependency_analysis': graph_time}
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

    def _refresh_market_dependencies(self):
        """Invalidate old artifacts whose ordinary input revisions changed.

        The feed revision is a simulator-visible ETag, NOT a task checker oracle.
        Missing revision evidence is fail-closed rather than accepting stale reuse.
        """
        start = time.perf_counter()
        old = self.ctx.old_trace
        if (old.market_versions is None or old.news_versions is None
                or old.feed_fingerprints is None):
            raise RecoveryError('MARKET_VERSION_UNAVAILABLE')
        for ticker in self.ctx.task.tickers:
            changed = (old.market_versions.get(ticker) != self.world.market_versions.get(ticker)
                       or old.news_versions.get(ticker) != self.world.news_versions.get(ticker)
                       or old.feed_fingerprints.get(ticker)
                          != self.world.feed_fingerprints([ticker])[ticker])
            if changed:
                self.market_changed.add(ticker)
                nid = f'research:{ticker}'
                self.invalid.add(nid)
                self.invalid.update(old.descendants(nid))
        self._overhead_s['market_validation'] += time.perf_counter() - start

    def prefix(self, limit):
        """Validate the reusable prefix, including previous market price reads."""
        start = time.perf_counter()
        prefix = valid_prefix(self.ctx.old_trace, self.steps, self.world, limit)
        if self.market_changed:
            for i in range(prefix):
                node = self.ctx.old_trace.nodes[f'exec:{i}']
                if any(any(key == f'market.{t}' for t in self.market_changed)
                       for a in node.actions for key in a.get('reads', [])):
                    prefix = i
                    break
        self._overhead_s['reuse_validation'] += time.perf_counter() - start
        return prefix

    def prepare(self, restart=False):
        ctx, old = self.ctx, self.ctx.old_trace
        node = old.nodes.get(ctx.target)
        require(node is not None or restart, 'UNKNOWN_TARGET', False)
        compatible = old.context_hash == self.trace.context_hash
        if not restart:
            require(compatible, 'CONTEXT_OR_SCHEMA_CHANGED')
            self._refresh_market_dependencies()
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
            if action.get('rejected_contract'):
                require(action['before'] == action['after'] and action['committed'] is False,
                        'REJECTED_ACTION_MUTATED', False)
                continue
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
        violations = []
        for action in events:
            if action.get('rejected_contract'):
                node_id = action['by']
                expected = (runtime.steps[int(node_id.split(':')[1])]
                            if node_id.startswith('exec:') else None)
                actual_args = action['args'] if isinstance(action['args'], dict) else {}
                want_args = expected['args'] if expected else {}
                changed_keys = sorted(k for k in set(actual_args) | set(want_args)
                                      if actual_args.get(k) != want_args.get(k))
                violations.append({'node': node_id, 'expected_op': expected['op'] if expected else None,
                                   'actual_op': action['tool'], 'different_args': changed_keys,
                                   'no_effect': action['before'] == action['after']})
        audit.append({'strategy': selected.name, 'published': failure is None, 'actions': events,
                      'contract_violations': violations, 'market_changed': sorted(runtime.market_changed),
                      'overhead_s': dict(runtime._overhead_s)})
        if failure:
            errors.append(failure.reason)
            if not failure.fallback:
                break
            continue
        # World-scoped market revisions are pinned for the candidate. If a concurrent
        # updater bypassed the per-World protocol, never silently publish its result.
        if (source.market_versions != runtime.world.market_versions
                or source.news_versions != runtime.world.news_versions
                or source.feed_fingerprints() != runtime.world.feed_fingerprints()):
            # Never publish a candidate made against a different feed revision.
            errors.append('MARKET_CHANGED_DURING_RECOVERY')
            break
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
             'market_changed': sorted({t for a in audit for t in a.get('market_changed', [])}),
             'recovery_overhead_s': dict(runtime._overhead_s),
             'trace_overhead_s': dict(runtime.trace.overhead_s),
             'recovery_status_queries': queries,
             'recovery_audit': audit, 'source_trace_id': ctx.old_trace.run_id,
             'contract_violations': [v for entry in audit for v in entry['contract_violations']]}
    return RecoveryResult(result_trace, stats)
