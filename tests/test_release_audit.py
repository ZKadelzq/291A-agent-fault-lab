"""The release gate must reject damaged evidence, not just parse happy paths."""
import json
import pytest
from mas.tasks import TASKS
from mas.faults import schedule
from run import run_clean, run_fault
from reporting.final_report import validate_trace


@pytest.fixture
def saved_recovery(tmp_path):
    task = TASKS[0]
    methods = {'none', 'restart'}
    clean, ok, _ = run_clean(task, methods, str(tmp_path))
    fault = next(f for f in schedule(task) if f.name == 'R-incorrect')
    run_fault(task, fault, clean, ok, methods, str(tmp_path))
    path = tmp_path / 'T1/R-incorrect/restart/trace.json'
    validate_trace(path)
    return path


@pytest.mark.parametrize('corruption', ['state', 'publication', 'overhead', 'metric'])
def test_release_gate_rejects_damaged_evidence(saved_recovery, corruption):
    data = json.loads(saved_recovery.read_text())
    if corruption == 'state':
        data['state']['positions']['AAPL'] += 1
    elif corruption == 'publication':
        data['metrics']['recovery_audit'][0]['published'] = False
    elif corruption == 'overhead':
        data['metrics']['recovery_overhead_s']['dependency_analysis'] += 1
    else:
        data['metrics']['safe_success'] = not data['metrics']['safe_success']
    saved_recovery.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        validate_trace(saved_recovery)
