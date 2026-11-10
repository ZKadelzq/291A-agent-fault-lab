"""Small strategy contract. Recovery receives diagnosis, never evaluation answers."""
from dataclasses import dataclass, field
from typing import Protocol

from ..trace import Trace


class RecoveryError(Exception):
    def __init__(self, reason, fallback=True):
        super().__init__(reason)
        self.reason, self.fallback = reason, fallback


@dataclass(frozen=True)
class RecoveryContext:
    task: object
    old_trace: Trace
    target: str
    allow_fallback: bool = True


@dataclass
class RecoveryCandidate:
    trace: Trace
    decisions: list = field(default_factory=list)


@dataclass
class RecoveryResult:
    trace: Trace
    stats: dict


class RecoveryStrategy(Protocol):
    name: str

    def build(self, ctx: RecoveryContext, runtime) -> RecoveryCandidate: ...
