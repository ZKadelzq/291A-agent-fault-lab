import uuid

from mas.metrics import duplicate_metrics
from mas.trace import Node, Trace


def action(step, tool='buy', args=None, committed=True, branch='one', **extra):
    return {'id': uuid.uuid4().hex, 'tool': tool, 'args': args or {'ticker': 'AAPL', 'amount_usd': 1000},
            'operation_id': step, 'branch_id': branch, 'committed': committed,
            'writes': ['cash'] if committed else [], **extra}


def metrics(*actions):
    trace = Trace('test')
    for i, a in enumerate(actions):
        trace.add(Node(str(i), 'executor', [], actions=[a]))
    return duplicate_metrics(trace)


def test_different_side_effects_within_one_step_are_not_retries():
    result = metrics(action('step'), action('step', 'send_email', {'to': 'x', 'subject': 'y', 'body': 'z'}))
    assert result['request_retry_attempted'] == result['business_dup_attempted'] == result['dup_attempted'] == 0


def test_exact_payload_reissue_is_a_request_retry():
    result = metrics(action('step'), action('step'))
    assert result['request_retry_attempted'] == result['request_retry_committed'] == 1
    assert result['business_dup_attempted'] == result['business_dup_committed'] == 0
    assert result['dup_committed'] == 1


def test_duplicate_buy_in_distinct_steps_is_a_business_duplicate():
    result = metrics(action('step1'), action('step2'))
    assert result['request_retry_attempted'] == 0
    assert result['business_dup_attempted'] == result['business_dup_committed'] == 1


def test_changed_amount_within_step_is_not_same_request():
    result = metrics(action('step'), action('step', args={'ticker': 'AAPL', 'amount_usd': 2000}))
    assert result['request_retry_attempted'] == 0 and result['business_dup_committed'] == 1


def test_retry_after_non_submission_is_not_duplicate_commit_or_masked():
    result = metrics(action('step', committed=False), action('step'))
    assert result['request_retry_attempted'] == result['request_retry_committed'] == 1
    assert result['dup_committed'] == result['rejected_duplicate_attempts'] == 0


def test_cash_rejected_duplicate_is_counted_separately():
    result = metrics(action('step'), action('step', committed=False))
    assert result['request_retry_attempted'] == result['rejected_duplicate_attempts'] == 1
    assert result['request_retry_committed'] == result['dup_committed'] == 0


def test_compensation_and_inactive_branches_are_excluded():
    result = metrics(action('step'), action('step', recovery_authorized=True), action('step', active=False))
    assert not any(result.values())


def test_separate_branches_do_not_count_as_retries_or_business_duplicates():
    result = metrics(action('step'), action('step', branch='two'))
    assert not any(result.values())


def test_unknown_commit_does_not_claim_duplicate_submission_succeeded():
    result = metrics(action('step'), action('step', committed=None))
    assert result['request_retry_attempted'] == result['duplicate_commit_unknown'] == 1
    assert result['dup_committed'] == 0


def test_successful_retry_does_not_settle_unknown_first_submission():
    result = metrics(action('step', committed=None), action('step'))
    assert result['request_retry_committed'] == result['duplicate_commit_unknown'] == 1
    assert result['dup_committed'] == 0  # lower bound, with uncertainty explicitly reported
