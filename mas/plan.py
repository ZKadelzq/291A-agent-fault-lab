"""Structured execution contracts. No model calls, state mutation or task oracle."""
import copy
import hashlib
import json
import math
import uuid

from .env import TOOLS

SCHEMA_VERSION = 3


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def normalize_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 24:
        raise ValueError('expected 1..24 structured steps')
    result = []
    for i, source in enumerate(steps):
        if not isinstance(source, dict) or source.get('op') not in TOOLS:
            raise ValueError('step requires a known op')
        if set(source) - {'op', 'args', 'requires', 'depends_on', 'uses', 'logical_id'}:
            raise ValueError('unexpected step fields; instruction must be derived from op/args')
        step = copy.deepcopy(source)
        op, args = step['op'], step.get('args')
        params = TOOLS[op]['params']
        if not isinstance(args, dict) or set(args) != set(params):
            raise ValueError(f'{op}: expected arguments {list(params)}')
        for k, (typ, _) in params.items():
            value = args[k]
            if typ == 'string' and not isinstance(value, str):
                raise ValueError(f'{op}.{k} must be a string')
            if typ == 'number' and (isinstance(value, bool) or not isinstance(value, (int, float))
                                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f'{op}.{k} must be finite and positive')
        if 'ticker' in args:
            args['ticker'] = args['ticker'].upper().strip()
        for key in ('requires', 'uses'):
            values = step.setdefault(key, [])
            if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
                raise ValueError(f'{key} must be a string list')
        deps = step.setdefault('depends_on', [])
        if not isinstance(deps, list) or any(type(d) is not int or d < 0 or d >= i for d in deps):
            raise ValueError('depends_on must refer to earlier step indices')
        if op == 'buy':
            step['requires'].append('kyc')
        if op == 'set_stop_loss':
            step['requires'].append('position:' + args['ticker'])
        step['requires'] = sorted(set(step['requires']))
        if any(r != 'kyc' and not r.startswith('position:') for r in step['requires']):
            raise ValueError('unknown precondition')
        step['depends_on'] = sorted(set(deps))
        step['logical_id'] = step.get('logical_id') or uuid.uuid4().hex
        result.append(step)
    if len({s['logical_id'] for s in result}) != len(result):
        raise ValueError('logical operation IDs must be unique within a plan')
    return result


def contract(step):
    """Only trusted, executor-visible input; never weak text/number similarity."""
    return {k: step.get(k, []) for k in ('op', 'args', 'requires', 'depends_on')}


def same_step(a, b):
    return contract(a) == contract(b)


def instruction(step):
    return 'Execute this structured operation: ' + json.dumps(contract(step), sort_keys=True)


def plan_error(data):
    try:
        normalize_steps(data.get('steps') if isinstance(data, dict) else None)
    except (ValueError, TypeError) as e:
        return str(e)
