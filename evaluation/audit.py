"""Audit frozen holdout rows and produce the descriptive statistics for the PDF."""
import argparse
import hashlib
import json
import statistics as st
from collections import Counter
from pathlib import Path
import math

from analysis import load
from analyze_phase2_provenance import check_reuse
from reporting import final_report as release
from .holdout import SPEC, config
from mas import tasks


def summarize(rows):
    n = len(rows)
    recovered = [r for r in rows if "recovery_status" in r]
    repair = (
        {
            k: st.mean(r[k] for r in recovered)
            for k in ("recomputed", "retained", "recovery_tokens", "recovery_latency")
        }
        if recovered
        else {}
    )
    return dict(
        n=n,
        repair=repair,
        unauthorized=sum(r["unauthorized"] for r in rows),
        detected=sum(bool(r.get("detected")) for r in rows),
        false_alarms=sum(r.get("false_alarms", 0) for r in rows),
        correct=sum(r["correct"] for r in rows),
        safe=sum(release.completed(r) for r in rows),
        tokens=st.mean(r["total_tokens"] for r in rows),
        latency=st.mean(r["total_latency"] for r in rows),
        status=dict(Counter(r.get("recovery_status", "workflow") for r in rows)),
        not_fired=sum(r.get("outcome") == "not_fired" for r in rows),
    )


def audit_tracking(root, matrix):
    artifact = release.read(root / "tracking.json")
    modes = ("off", "message", "raw", "checkpoint")
    expected = {
        (f"T{i}", block, m) for i in range(1, 7) for block in range(30) for m in modes
    }
    samples = artifact["samples"]
    by_key = {(x["task"], x["block"], x["mode"]): x for x in samples}
    assert len(samples) == len(expected) and set(by_key) == expected
    missed = Counter()
    for task, digest in artifact["source_sha256"].items():
        data = (matrix / task / "clean/none/trace.json").read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        trace = json.loads(data)["trace"]
        nodes = {n["id"] for n in trace["nodes"]}
        messages = {
            (p, n["id"]) for n in trace["nodes"] for p in n["parents"] if p in nodes
        }
        reference = {tuple(edge) for edge in trace["edges"]}
        assert messages <= reference
        for mode, count in zip(
            modes, (len(reference), len(reference - messages), 0, 0)
        ):
            assert artifact["graph"][task][mode] == {
                "missed": count,
                "reference": len(reference),
            }
            missed[mode] += count
    assert set(artifact["source_sha256"]) == {f"T{i}" for i in range(1, 7)}
    for mode in modes:
        subset = [x for x in samples if x["mode"] == mode]
        deltas = sorted(
            x["cpu_us"] - by_key[x["task"], x["block"], "off"]["cpu_us"] for x in subset
        )
        assert artifact["summary"][mode] == dict(
            cpu_us=st.median(x["cpu_us"] for x in subset),
            paired_us=st.median(deltas),
            paired_p95_us=deltas[math.ceil(0.95 * len(deltas)) - 1],
            peak_bytes=st.median(x["peak_bytes"] for x in subset),
            missed=missed[mode],
        )
    return len(samples)


def main(root, matrix):
    blocks = audit_tracking(root, matrix)
    workloads = {t[0]: tasks.Task(*t) for t in SPEC["tasks"]}
    tasks.CASH_MODE = "ample"
    release.TASKS_BY_ID.update(workloads)
    keys = {(t, "clean", m) for t in workloads for m in ("none", "verify")}
    keys |= {
        (t, f, m) for t in workloads for f in SPEC["faults"] for m in SPEC["methods"]
    }
    all_rows, reuse, source_ids = [], Counter(), set()
    for i in range(1, SPEC["repetitions"] + 1):
        directory = root / f"repeat-{i}"
        manifest = release.read(directory / "manifest.json")
        assert manifest == config(), "auditor and runner configs differ"
        raw = [
            json.loads(s)
            for s in (directory / "results.jsonl").read_text().splitlines()
        ]
        assert (
            len(raw) == len(keys)
            and {(r["task"], r["fault"], r["method"]) for r in raw} == keys
        )
        assert len(list(directory.glob("*/*/*/trace.json"))) == len(keys)
        for r in raw:
            t, f, m = r["task"], r["fault"], r["method"]
            data = release.validate_trace(directory / t / f / m / "trace.json")
            assert r == data["metrics"] and r["config"] == manifest
            if m == "none":
                assert data["trace"]["run_id"] not in source_ids
                source_ids.add(data["trace"]["run_id"])
            if m in release.RECOVERY:
                source = release.read(directory / t / f / "none/trace.json")["trace"]
                clean = release.read(directory / t / "clean/none/trace.json")["trace"]
                assert r["source_trace_id"] == source["run_id"]
                reuse[m] += check_reuse(directory, t, f, m, source, clean)[0]
        all_rows.extend(dict(row, repeat=i) for row in load(str(directory)))
    faulty = [r for r in all_rows if r["fault"] != "clean"]
    methods = {
        m: summarize([r for r in faulty if r["method"] == m]) for m in SPEC["methods"]
    }
    controls = {
        m: summarize(
            [r for r in all_rows if r["fault"] == "clean" and r["method"] == m]
        )
        for m in ("none", "verify")
    }
    by_fault = {
        f: {
            m: summarize([r for r in faulty if r["fault"] == f and r["method"] == m])
            for m in SPEC["methods"]
        }
        for f in SPEC["faults"]
    }
    base = {
        (r["repeat"], r["task"], r["fault"]): r
        for r in faulty
        if r["method"] == "restart"
    }
    none = {
        (r["repeat"], r["task"], r["fault"]): r for r in faulty if r["method"] == "none"
    }
    paired = {}
    for m in ("restart", "selective", "change_aware"):
        rows = [r for r in faulty if r["method"] == m]

        def key(r):
            return r["repeat"], r["task"], r["fault"]

        def saving(subset):
            return 100 * (
                1
                - sum(r["total_tokens"] for r in subset)
                / sum(base[key(r)]["total_tokens"] for r in subset)
            )

        paired[m] = dict(
            saving=saving(rows),
            by_task={t: saving([r for r in rows if r["task"] == t]) for t in workloads},
            by_repeat=[
                saving([r for r in rows if r["repeat"] == i]) for i in range(1, 4)
            ],
            rescued=sum(
                release.completed(r) and not none[key(r)]["safe_success"] for r in rows
            ),
            lower=sum(r["total_tokens"] < base[key(r)]["total_tokens"] for r in rows),
        )
    report = dict(
        rows=len(all_rows),
        tracking_measurements=blocks,
        reuse=dict(reuse),
        controls=controls,
        methods=methods,
        by_fault=by_fault,
        paired=paired,
    )
    (root / "AUDIT.json").write_text(json.dumps(report, indent=2) + "\n")
    hashes = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "SHA256SUMS.json"
    }
    (root / "SHA256SUMS.json").write_text(json.dumps(hashes, indent=1) + "\n")
    print(
        "Audited",
        len(all_rows),
        "episodes across",
        len(base),
        "paired fault exposures.",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--tracking-matrix",
        type=Path,
        default=Path("/work/final-1117/release/committed"),
    )
    args = parser.parse_args()
    main(args.root, args.tracking_matrix)
