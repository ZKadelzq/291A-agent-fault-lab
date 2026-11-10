"""Provenance trace: every intermediate result is a Node tagged with its sources."""
from dataclasses import asdict, dataclass, field
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import uuid

_cost_sink = ContextVar('recovery_cost_sink', default=None)


def record_usage(usage):
    sink = _cost_sink.get()
    if sink is not None:
        sink.append(copy.deepcopy(usage))


@contextmanager
def capture_cost():
    usage = []
    token = _cost_sink.set(usage)
    try:
        yield usage
    finally:
        _cost_sink.reset(token)


def zero_cost():
    # api_s = time inside successful LLM requests; backoff_s = time lost to rate-limit / server-error retries
    # (failed attempts + sleeps); retries = number of such retries. All three are 0 in traces made before v2.
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "latency": 0.0,
            "api_s": 0.0, "backoff_s": 0.0, "retries": 0}


def add_cost(a, b):
    """Sum two cost dicts. Missing keys count as 0, so old traces (without api_s/backoff_s) still load."""
    return {k: (a or {}).get(k, 0) + (b or {}).get(k, 0) for k in {**zero_cost(), **(a or {}), **(b or {})}}


def tokens(cost):
    return cost.get("prompt_tokens", 0) + cost.get("completion_tokens", 0)


@dataclass
class Node:
    id: str                 # "research:AAPL" | "plan" | "exec:3"
    agent: str              # researcher | planner | executor
    parents: list           # message/data dependencies (coarse lineage)
    content: str = ""       # message passed downstream
    data: object = None     # parsed content (research dict / list of plan steps)
    uses: list = field(default_factory=list)      # fine-grained: research ids a plan step declares
    actions: list = field(default_factory=list)   # tool-call records (with reads / writes)
    cost: dict = field(default_factory=zero_cost)
    messages: list = field(default_factory=list)  # full LLM conversation of this agent call
    fault: str = None       # name of the fault injected into this node, if any
    verdict: dict = None    # verifier output (verify baseline only)
    verify_cost: dict = field(default_factory=zero_cost)
    attempt: int = 0        # >0 if regenerated after a failed verification
    original: str = None    # content BEFORE an output fault mutated it (pre/post-fault evidence)
    parse: str = "ok"       # JSON protocol status of this agent reply: ok | repaired | reasked | failed | n/a
    step: dict = None
    reused_from: str = None
    supersedes: str = None


def _overlap(reads, writes):
    def match(a, b):
        return a == b or (a.endswith("*") and b.startswith(a[:-1])) or (b.endswith("*") and a.startswith(b[:-1]))
    return any(match(r, w) for r in reads for w in writes)


class Trace:
    def __init__(self, task_id):
        self.task_id = task_id
        self.nodes = {}  # insertion order == execution order
        self.run_id = uuid.uuid4().hex
        self.source_trace_id = None
        self.context_hash = None
        self.checkpoints = {}
        # Terminal observation is independent of executor-prefix checkpoints.
        self.final_state = None
        self.new_cost = None  # actual new work, excluding historical costs on reused nodes

    def add(self, node):
        self.nodes[node.id] = node

    def edges(self, state=True, conflicts=False):
        """(src, dst) pairs. Message edges come from `parents`; state edges link a node that
        reads a state key to every earlier node that wrote it (read-after-write)."""
        out = {(p, n.id) for n in self.nodes.values() for p in n.parents if p in self.nodes}
        if state:
            order = list(self.nodes.values())
            for j, dst in enumerate(order):
                reads = [k for a in dst.actions for k in a["reads"]]
                dst_writes = [k for a in dst.actions for k in a['writes']]
                for src in order[:j]:
                    writes = [k for a in src.actions for k in a["writes"]]
                    src_reads = [k for a in src.actions for k in a['reads']]
                    if _overlap(reads, writes) or (conflicts and
                            (_overlap(dst_writes, writes) or _overlap(dst_writes, src_reads))):
                        out.add((src.id, dst.id))
        return out

    def descendants(self, node_id, state=True):
        """All nodes that (transitively) depend on `node_id`."""
        children = {}
        for s, d in self.edges(state):
            children.setdefault(s, set()).add(d)
        seen, stack = set(), [node_id]
        while stack:
            for c in children.get(stack.pop(), ()):
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return seen

    def total_cost(self, ids=None):
        c = zero_cost()
        for n in self.nodes.values():
            if ids is None or n.id in ids:
                c = add_cost(add_cost(c, n.cost), n.verify_cost)
        return c

    def to_dict(self):
        return {"task_id": self.task_id, "nodes": [asdict(n) for n in self.nodes.values()],
                "edges": sorted(self.edges()), 'schema_version': 4, 'run_id': self.run_id,
                'source_trace_id': self.source_trace_id, 'context_hash': self.context_hash,
                'checkpoints': self.checkpoints, 'final_state': self.final_state, 'new_cost': self.new_cost}

    @classmethod
    def from_dict(cls, data):
        trace = cls(data['task_id'])
        for key in ('run_id', 'source_trace_id', 'context_hash', 'checkpoints', 'final_state', 'new_cost'):
            if key in data:
                setattr(trace, key, copy.deepcopy(data[key]))
        for n in data['nodes']:
            trace.add(Node(**{k: v for k, v in n.items() if k in Node.__dataclass_fields__}))
        return trace
