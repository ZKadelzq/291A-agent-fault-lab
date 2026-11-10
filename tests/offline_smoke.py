"""Run the actual experiment harness with deterministic replies, never an API.

Accepts the same arguments as run.py. Results are mechanism checks, not LLM
performance measurements; the model label makes this distinction explicit.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run
from mas import faults, llm
from conftest import fake_chat


if __name__ == '__main__':
    llm.chat = fake_chat
    llm.MODEL = 'deterministic-test-double'
    faults.TIMEOUT_S = 0
    run.main()
