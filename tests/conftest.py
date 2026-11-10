"""Deterministic model double exercising the real prompts, tool loop and fault injector."""
import json
import re
from types import SimpleNamespace as NS

import pytest

from mas import llm, faults
from mas.tasks import TASKS


def message(content='', calls=()):
    return NS(content=content, tool_calls=list(calls)), {'calls': 1, 'prompt_tokens': 10, 'completion_tokens': 2}


def tool(name, args):
    return message(calls=[NS(id='call', function=NS(name=name, arguments=json.dumps(args)))])


def structured_plan(task, reports):
    picks = [t for t in task.tickers if reports[t]['sector'] == task.sector and reports[t]['rating'] == 'buy']
    steps = [{'op': 'verify_kyc', 'args': {'client_id': task.client_id}, 'uses': []}]
    for t in picks:
        steps.append({'op': 'buy', 'args': {'ticker': t, 'amount_usd': task.budget / len(picks)},
                      'uses': [f'research:{t}']})
    for t in picks:
        steps.append({'op': 'set_stop_loss', 'args': {'ticker': t, 'pct': task.stop_pct},
                      'uses': [f'research:{t}']})
    steps.append({'op': 'write_file', 'args': {'path': task.report_path,
                  'content': 'Bought ' + ', '.join(picks) + '.'}, 'uses': [f'research:{t}' for t in picks]})
    return {'steps': steps}


def fake_chat(messages, tools=None, **kwargs):
    system = messages[0]['content']
    user = next(m['content'] for m in messages if m['role'] == 'user')
    replies = [m['content'] for m in messages if m['role'] == 'tool']
    if 'You are the Researcher' in system:
        ticker = user.split()[-1].rstrip('.')
        lookups = [r for r in replies if r.startswith('{')]
        if not lookups:
            return tool('lookup_stock', {'ticker': ticker})
        if len(replies) == len(lookups):
            return tool('get_news', {'ticker': ticker})
        data = json.loads(lookups[-1])
        data.pop('as_of', None)
        data['notes'] = 'Research complete.'
        return message(json.dumps(data))
    if 'You are the Planner' in system:
        task = next(t for t in TASKS if f'Client {t.client_id}' in user)
        reports = {}
        for ticker, raw in re.findall(r'\[research:([^]]+)\]\n(.*?)(?=\n\n\[research:|\Z)', user, re.S):
            reports[ticker] = json.loads(raw)
        return message(json.dumps(structured_plan(task, reports)))
    if 'You are the Executor' in system:
        step = json.loads(user.split('Execute this structured operation: ', 1)[1])
        if not replies or 'timed out' in replies[-1]:
            return tool(step['op'], step['args'])
        return message('Operation finished.')
    if 'You are a Verifier' in system:
        if user.startswith('Report '):
            data = json.loads(user.split('\n', 1)[1])
            if not replies:
                return tool('lookup_stock', {'ticker': data['ticker']})
            true = json.loads(replies[-1])
            valid = all(data[k] == true[k] for k in ['price', 'sector', 'rating']) and 'email' not in data.get('notes', '')
        else:
            valid = 'send_email' not in user and 'transfer_funds' not in user
        return message(json.dumps({'verdict': 'PASS' if valid else 'FAIL', 'reason': 'test verification'}))
    raise AssertionError('unexpected prompt')


@pytest.fixture(autouse=True)
def offline_model(monkeypatch):
    monkeypatch.setattr(llm, 'chat', fake_chat)
    monkeypatch.setattr(faults, 'TIMEOUT_S', 0)
