"""Boundary between simulator ground truth and recovery-visible evidence.

The simulator/evaluator retains its audit log. Recovery receives response-level
receipts after an ambiguous trade and must explicitly query submission status.
Terminal account observations are allowed; they cannot establish whether an
individual request committed and never override an unresolved status query.
"""
import copy


def recovery_inputs(trace, world):
    old, source = copy.deepcopy(trace), copy.deepcopy(world)
    source.audit_log = []
    source._order_outcomes = {}
    source._unavailable_orders = set()
    source.tool_hook = None
    hidden, ambiguous, tail = set(), set(), False
    for action in source.log:
        uncertain = (action.get('outcome_unknown') or 'status unknown' in action.get('result', ''))
        if uncertain and action.get('status_source') != 'order_query':
            ambiguous.add(action['id'])
            if action['tool'] in {'buy', 'sell', 'transfer_funds', 'send_email'}:
                tail = True
        if tail or action['id'] in ambiguous:
            hidden.add(action['id'])

    def visible(action):
        action.pop('raw_result', None)
        action.pop('state_changed', None)
        if action['id'] in hidden:
            action['before'] = action['after'] = None
            action['evidence'] = 'response_only'
        if action['id'] in ambiguous:
            action['outcome_unknown'] = True
            action['committed'] = None
            action['writes'] = []

    for action in source.log:
        visible(action)
    for node in old.nodes.values():
        node.original = None
        for action in node.actions:
            visible(action)
    # The terminal observation is not a replay checkpoint or an order receipt.
    if old.final_state is None and old.checkpoints:
        old.final_state = copy.deepcopy(old.checkpoints[max(old.checkpoints, key=int)])
    boundaries = [int(n.id.split(':')[1]) for n in old.nodes.values() if n.agent == 'executor'
                  and any(a['id'] in ambiguous for a in n.actions)]
    if boundaries:
        old.checkpoints = {k: v for k, v in old.checkpoints.items() if int(k) <= min(boundaries)}
    return old, source


def resolve_outcomes(trace, source, status_reader):
    """Resolve only through the service capability; snapshots never settle status."""
    queries, statuses = [], {}
    for action in source.log:
        if action.get('committed') is not None or not action.get('outcome_unknown'):
            continue
        status, error = None, None
        try:
            status = status_reader(action['id']) if status_reader else None
        except Exception as exc:
            error = type(exc).__name__
        status = status if type(status) is bool else None
        statuses[action['id']] = status
        queries.append({'request_id': action['id'], 'status':
                        'committed' if status is True else 'not_committed' if status is False else 'unknown',
                        'error': error})
    for action in source.log + [a for n in trace.nodes.values() for a in n.actions]:
        if action['id'] in statuses:
            action['committed'] = statuses[action['id']]
            action['status_source'] = 'order_query'
    return queries
