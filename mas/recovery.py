"""Public strategy registry and compatibility adapters for the experiment harness."""
from .recover.contracts import RecoveryContext
from .recover.engine import recover
from .recover.evidence import recovery_inputs
from .recover.selective import Restart, Selective
from .recover.change_aware import ChangeAware
from .recover.reconcile import Reconcile

STRATEGIES = {}
METHODS = {}


def register(strategy):
    if strategy.name in METHODS:
        raise ValueError('duplicate recovery strategy: ' + strategy.name)
    STRATEGIES[strategy.name] = strategy

    def run(task, world, trace, fault):
        old, source = recovery_inputs(trace, world)
        result = recover(RecoveryContext(task, old, fault.target), source, strategy,
                         status_reader=world.query_order_status)
        if result.stats['recovery_status'] != 'stopped':
            world.state, world.log = source.state, source.log
            world.audit_log.extend(source.audit_log)
            world._order_outcomes.update(source._order_outcomes)
        return result.trace, result.stats

    METHODS[strategy.name] = run
    return run


full_restart = register(Restart())
selective = register(Selective())
change_aware = register(ChangeAware())
reconcile = register(Reconcile())
