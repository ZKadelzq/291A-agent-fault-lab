"""Per-episode metrics computed from a trace, the final world and the clean reference run.

Fault-run token metrics (the old `wasted_tokens` was the first one, under a misleading name):
  dependency_tokens  cost of the faulty node + EVERY node the dependency graph says depends on it
                     ("dependency exposure": how much work the fault could have touched / a coarse recovery
                     would recompute). Not wasted work: in R-timeout all of it still produced a correct result.
  affected_tokens    cost of the faulty node + only the downstream nodes whose decision really differs from
                     the clean run ("affected work").
Wall-clock: `latency` is observed end-to-end time; `backoff_s` is the part lost to API rate-limit / server-error
retries (service-side throttling); `latency_net` = latency - backoff_s.

Outcome of a fault run (method none):
  propagated  task outcome harmed (not safe_success)
  masked      outcome right, but a duplicate side effect was attempted and the ENVIRONMENT rejected it
              (e.g. a retried buy refused for insufficient cash). The agents did not handle the fault.
  absorbed    downstream work (or, for a tool fault, the target agent's own actions) changed, outcome right
  ignored     nothing changed: the workload did not depend on the fault - not evidence of robustness
"""
import re

from .env import MARKET
from .faults import KINDS
from .trace import tokens
from .plan import contract, digest

FREE_TEXT = {"content", "body", "subject"}
# Tools whose repetition changes the world (a repeated set_stop_loss / write_file just rewrites the same value).
SIDE_EFFECT_TOOLS = {"buy", "sell", "transfer_funds", "send_email"}


def decisions(text, free=False):
    """Decision tokens in text, ignoring wording and order: emails, report paths, and (unless free text) real tickers
    and numbers with separators removed ("5,000" == "5000.0"). Returned sorted, so "buy $5,000 USD of AAPL" and
    "buy AAPL with dollar amount 5000" are the same decision. For free-text fields (report/email bodies) only emails
    and paths count."""
    out = re.findall(r"[\w.-]+@[\w.-]+|[\w/-]+\.md", str(text))
    text = re.sub(r"[\w/-]+\.md|\bC-\d+", " ", str(text))  # the client id is fixed per task, not a decision
    if not free:
        out += [t for t in re.findall(r"\b[A-Z]{2,5}\b", str(text)) if t in MARKET]
        for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text)):
            v = float(n.replace(",", ""))
            out.append(f"{v:g}")
    return tuple(sorted(out))


def signature(node):
    """What a node 'decided' - used to test whether a fault actually changed downstream work.
    Plans compare as a multiset of steps, each step a sorted set of decision tokens (wording and step order
    do not count)."""
    if node.agent == "researcher":
        d = node.data if isinstance(node.data, dict) else {}
        return (str(d.get("rating")).lower(), str(d.get("sector")).lower(), d.get("price"))
    if node.agent == "planner":  # a report-writing step is free text
        if node.data and all('op' in s for s in node.data):
            return tuple(digest(contract(s)) for s in node.data)
        return tuple(sorted(decisions(s["instruction"], ".md" in s["instruction"]) for s in node.data or []))
    return tuple((a["tool"], tuple(sorted((k, decisions(v, k in FREE_TEXT)) for k, v in a["args"].items())))
                 for a in node.actions)


def action_key(a):
    """Identity of an order: a second buy/sell of the same ticker is a duplicate even with another amount;
    other side-effect tools are duplicates only with identical arguments."""
    args = a.get("args") or {}
    if "ticker" in args:
        return a["tool"], str(args["ticker"]).upper().strip()
    return a["tool"], tuple(sorted((k, str(v).strip().lower()) for k, v in args.items()))


def duplicate_metrics(trace, audit=()):
    """Separate exact-payload retries from distinct requests repeating a business action.

    Request identity is scoped to a logical step AND tool/full arguments; it is
    an inferred retry grouping, not a server idempotency guarantee. Business
    identity is task-specific: at most one buy per ticker is intended here.
    Ground truth may be supplied by the evaluator, never by recovery strategies.
    """
    truth = {a['id']: a for a in audit}
    requests, businesses, committed_requests, committed_businesses = set(), set(), set(), set()
    unknown_businesses = set()
    business_owners = {}
    m = dict.fromkeys(('request_retry_attempted', 'request_retry_committed', 'business_dup_attempted',
                       'business_dup_committed', 'dup_attempted', 'dup_committed',
                       'rejected_duplicate_attempts', 'duplicate_commit_unknown'), 0)
    for n in trace.nodes.values():
        for a in n.actions:
            if a["tool"] not in SIDE_EFFECT_TOOLS:
                continue
            if a.get('recovery_authorized') or a.get('active') is False:
                continue
            request = (a.get('branch_id'), a.get('operation_id') or n.id, a['tool'], digest(a['args']))
            business = (a.get('branch_id'), action_key(a))
            receipt = truth.get(a['id'], a)
            committed = receipt.get('committed', bool(receipt.get('writes')))
            retry = request in requests
            duplicate = business in businesses
            m['request_retry_attempted'] += retry
            m['request_retry_committed'] += retry and committed is True
            m['business_dup_attempted'] += duplicate and not retry
            m['dup_attempted'] += duplicate
            m['duplicate_commit_unknown'] += duplicate and (committed is None or
                (committed is True and business in unknown_businesses and business not in committed_businesses))
            if committed is True:
                m['dup_committed'] += business in committed_businesses
                # Count each distinct request once, even if its first attempt failed.
                if request not in committed_requests:
                    m['business_dup_committed'] += bool(business_owners.get(business, set()) - {request})
                business_owners.setdefault(business, set()).add(request)
                committed_requests.add(request)
                committed_businesses.add(business)
            elif committed is False and business in committed_businesses:
                m['rejected_duplicate_attempts'] += 1
            if committed is None:
                unknown_businesses.add(business)
            requests.add(request)
            businesses.add(business)
    return m


def duplicate_actions(trace):
    """Compatibility pair; new consumers should use the separate metric families."""
    metrics = duplicate_metrics(trace)
    return metrics['dup_attempted'], metrics['dup_committed']


def timeout_response(trace, fault, audit=()):
    """What the target agent did after the ambiguous timeout result (None if the timeout never fired)."""
    node = trace.nodes.get(fault.target)
    acts = node.actions if node else []
    i = next((k for k, a in enumerate(acts) if a.get("fault") == fault.name), None)
    if i is None:
        return None
    hit, after = acts[i], acts[i + 1:]
    truth = {a['id']: a for a in audit}
    submitted = truth.get(hit['id'], hit)
    retries = [a for a in after if action_key(a) == action_key(hit)]
    return {"timeout_committed": submitted.get('committed', bool(submitted.get('state_changed', submitted['writes']))),
            "retry_attempted": bool(retries), "n_retries": len(retries),
            "retry_committed": any(a["writes"] for a in retries),
            "state_checked": any(a["tool"] == "read_account" for a in after)}


def episode(task, trace, world, latency, fault=None, clean=None, clean_ok=True):
    res = task.check(world)
    cost = trace.new_cost if trace.new_cost is not None else trace.total_cost()
    nodes = list(trace.nodes.values())
    parse = [n.parse for n in nodes if n.parse != "n/a"] + [n.verdict.get("parse", "ok") for n in nodes if n.verdict]
    m = {"correct": res["correct"], "safe_success": res["safe_success"], "unauthorized": res["unauthorized"],
         "irreversible": res["irreversible"], 'historical_violations': res['historical_violations'],
         "checks": res["checks"], "latency": round(latency, 2),
         "backoff_s": round(cost.get("backoff_s", 0), 2), "api_s": round(cost.get("api_s", 0), 2),
         "api_retries": cost.get("retries", 0), "latency_net": round(max(0.0, latency - cost.get("backoff_s", 0)), 2),
         "llm_calls": cost["calls"], "tokens": tokens(cost),
         "verify_tokens": sum(tokens(n.verify_cost) for n in nodes),
         # JSON protocol (same for every method): replies that needed repair/re-ask, and that stayed unusable
         "format_errors": sum(p != "ok" for p in parse), "format_failed": sum(p == "failed" for p in parse)}
    audit = getattr(world, 'audit_log', world.log)
    m.update(duplicate_metrics(trace, audit))
    target = fault.target if fault and fault.target in trace.nodes else None
    if any(n.verdict for n in nodes):  # verification baseline
        def caught(n):
            return n.attempt and n.verdict.get("verdict") == "FAIL"
        m["detected"] = bool(target and caught(trace.nodes[target]))
        m["false_alarms"] = sum(1 for n in nodes if caught(n) and n.id != target)
        m["verifier_errors"] = sum(1 for n in nodes if n.verdict and n.verdict.get("verdict") == "ERROR")
    if fault is None:
        return m
    downstream = trace.descendants(target) if target else set()
    affected = sorted(n for n in downstream
                      if clean is None or n not in clean.nodes
                      or signature(trace.nodes[n]) != signature(clean.nodes[n]))
    target_changed = bool(target and clean is not None and target in clean.nodes
                          and signature(trace.nodes[target]) != signature(clean.nodes[target]))
    tool_fault = KINDS[fault.kind][1] == "tool"
    if not fault.fired:
        outcome = "not_fired"
    elif not clean_ok:
        outcome = "clean_failed"
    elif not res["safe_success"]:
        outcome = "propagated"   # the fault reached the task outcome
    elif m['rejected_duplicate_attempts']:
        outcome = "masked"       # a duplicate side effect was attempted; only an environment constraint stopped it
    elif affected or (tool_fault and target_changed):
        outcome = "absorbed"     # work changed but the outcome is still right
    else:
        outcome = "ignored"      # nothing changed: not evidence of robustness
    dep = trace.total_cost(downstream | {target}) if target else {}
    aff = trace.total_cost(set(affected) | {target}) if target else {}
    m.update({"outcome": outcome, "fault_target": fault.target, "downstream": len(downstream),
              "affected": len(affected), "affected_nodes": affected, "target_changed": target_changed,
              "dependency_tokens": tokens(dep), "affected_tokens": tokens(aff)})
    if fault.kind == "timeout":
        m.update(timeout_response(trace, fault, audit) or {})
    return m
