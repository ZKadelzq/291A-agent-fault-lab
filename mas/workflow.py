"""Stage interfaces shared by normal execution and recovery strategies."""
import copy

from . import agents
from .faults import mutate, tool_hook
from .trace import Trace, add_cost


def research_all(task, world, trace, produce):
    return [produce(f'research:{t}', lambda t=t: agents.research(task, t, world)) for t in task.tickers]


def build_plan(task, reports, produce):
    return produce('plan', lambda: agents.plan(task, reports))


def execute_steps(task, world, trace, steps, produce, start=0):
    trace.checkpoints[str(start)] = world.snapshot()
    for i in range(start, len(steps)):
        produce(f'exec:{i}', lambda i=i: agents.execute(task, i, steps[i], world))
        trace.checkpoints[str(i + 1)] = world.snapshot()


def run_workflow(task, world, fault=None, verify=False, reuse=None):
    """Ordinary workflow. Executor reuse must go through the validated recovery runtime."""
    reuse = reuse or {}
    if any(n.agent == 'executor' for n in reuse.values()):
        raise ValueError('executor reuse requires recovery state/contract validation')
    trace = Trace(task.id)
    trace.context_hash = agents.config_signature(task)
    world.tool_hook = tool_hook(fault) if fault else None

    def produce(nid, make):
        if nid in reuse:
            node = copy.deepcopy(reuse[nid])
        else:
            node = make()
            if fault and fault.target == nid:
                mutate(fault, node, task.client_id, world=world)
                node.fault = fault.name if fault.fired else None
                fault.done = True
            if verify and node.agent in ('researcher', 'planner'):
                verdict, vcost = agents.verify(task, node, world, trace)
                if verdict['verdict'] in ('FAIL', 'ERROR'):
                    old, node = node, make()
                    node.cost, node.attempt = add_cost(old.cost, node.cost), 1
                    node.fault, node.original = old.fault, old.original
                node.verdict, node.verify_cost = verdict, vcost
        trace.add(node)
        return node

    try:
        reports = research_all(task, world, trace, produce)
        plan = build_plan(task, reports, produce)
        execute_steps(task, world, trace, plan.data or [], produce)
        trace.final_state = world.snapshot()
    finally:
        world.tool_hook = None
    return trace
