"""Typeset the course report from audited experiment artifacts (no inference)."""
import argparse
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "runs/phase2-final-muse-20261117"
HOLDOUT = ROOT / "runs/phase2-holdout-muse-20261117"
INK = colors.HexColor("#152c35")
TEAL = colors.HexColor("#12595d")
LIGHT = colors.HexColor("#e8f1ef")
GRAY = colors.HexColor("#536169")
X, WIDTH, COL, RIGHT = 54, 504, 243, 315


def read(path):
    return json.loads(path.read_text())


class Paper:
    def __init__(self, file):
        self.c = Canvas(str(file), pagesize=letter, pageCompression=1)
        self.c.setTitle(
            "Recovering stateful multi-agent workflows after a localized fault"
        )
        self.c.setSubject("CSE 291A Project 5 final report")

    def text(self, x, y, s, size=10, font="Times-Roman", color=INK):
        self.c.setFillColor(color)
        self.c.setFont(font, size)
        self.c.drawString(x, y, s)

    def page(self, num, label):
        self.text(X, 709, "UC SAN DIEGO  /  CSE 291A", 9, "Helvetica-Bold", TEAL)
        self.c.setFont("Helvetica", 9)
        self.c.drawRightString(558, 709, label)
        self.c.setStrokeColor(TEAL)
        self.c.line(X, 703, 558, 703)
        self.c.setStrokeColor(colors.lightgrey)
        self.c.line(X, 89, 558, 89)
        self.text(X, 78, "CSE 291A  /  Fall 2026  /  Project 5", 9, "Times-Roman", GRAY)
        self.c.drawRightString(558, 78, str(num))

    def title(self, s, y=677):
        self.text(X, y, s, 19, "Times-Bold")

    def para(self, x, y, s, w=COL, heading=False):
        style = ParagraphStyle(
            "body",
            fontName="Times-Bold" if heading else "Times-Roman",
            fontSize=12 if heading else 10,
            leading=14 if heading else 12,
            textColor=TEAL if heading else INK,
        )
        para = Paragraph(s, style)
        _, h = para.wrap(w, 640)
        if y - h < 92:
            raise ValueError(f"Column overflow at {y-h:.1f}: {s[:65]}")
        para.drawOn(self.c, x, y - h)
        return y - h - (5 if heading else 7)

    def column(self, x, y, sections):
        for head, paras in sections:
            if head:
                y = self.para(x, y, head, heading=True)
            for s in paras:
                y = self.para(x, y, s)
        return y

    def table(self, y, headers, rows, widths):
        assert sum(widths) == WIDTH
        for i, row in enumerate([headers, *rows]):
            if i == 0 or i % 2 == 0:
                self.c.setFillColor(LIGHT if i == 0 else colors.HexColor("#f5f7f6"))
                self.c.rect(X, y - 17, WIDTH, 17, stroke=0, fill=1)
            left = X
            for j, (cell, w) in enumerate(zip(row, widths)):
                font = "Times-Bold" if i == 0 else "Times-Roman"
                if self.c.stringWidth(str(cell), font, 10) > w - 9:
                    raise ValueError(f"Table overflow: {cell}")
                self.c.setFont(font, 10)
                self.c.setFillColor(INK)
                if j == 0:
                    self.c.drawString(left + 4, y - 12, str(cell))
                else:
                    self.c.drawRightString(left + w - 4, y - 12, str(cell))
                left += w
            y -= 17
        self.c.setStrokeColor(TEAL)
        self.c.line(X, y, 558, y)
        return y - 10

    def pipeline(self, y):
        nodes = [
            ("Research", "per candidate"),
            ("Plan", "typed operations"),
            ("Execute", "state + receipts"),
            ("Validate", "publish or stop"),
        ]
        for i, (title, desc) in enumerate(nodes):
            x = X + i * 130
            self.c.setFillColor(LIGHT)
            self.c.roundRect(x, y - 40, 110, 40, 4, stroke=0, fill=1)
            self.text(x + 8, y - 15, title, 12, "Times-Bold", TEAL)
            self.text(x + 8, y - 30, desc)
            if i < 3:
                self.c.setStrokeColor(TEAL)
                self.c.line(x + 113, y - 21, x + 126, y - 21)
                self.c.line(x + 126, y - 21, x + 122, y - 18)
                self.c.line(x + 126, y - 21, x + 122, y - 24)


def main(output, authors):
    hold = read(HOLDOUT / "AUDIT.json")
    track = read(HOLDOUT / "tracking.json")
    dev = read(RELEASE / "RELEASE_AUDIT.json")
    if hold["rows"] != 204 or not dev["passed"] or dev["traces"] != 524:
        raise ValueError("Incomplete evidence")
    ms = hold["methods"]
    ps = hold["paired"]
    tr = track["summary"]
    ca = ps["change_aware"]
    ctl = hold["controls"]
    output.parent.mkdir(parents=True, exist_ok=True)
    p = Paper(output)
    p.c.setAuthor(authors)
    p.page(1, "FINAL PROJECT REPORT")
    p.title("Recovering stateful multi-agent workflows")
    p.title("after a localized fault", 655)
    p.text(X, 635, authors, 10, "Times-Italic")
    p.pipeline(617)
    p.para(
        X,
        569,
        "<b>Figure 1.</b> Live execution records messages and tool effects. Recovery starts from the original trace, rebuilds a candidate account, and validates its effects before publication.",
        WIDTH,
    )
    p.column(
        X,
        529,
        [
            (
                "Abstract",
                [
                    f'When an agent acts on a faulty intermediate result, retrying the entire workflow consumes inference and can repeat a committed action. We record message and shared-state dependencies and validate execution receipts before reusing a prefix. In six development tasks, change-aware recovery restored the correct account in 48/48 fault cases. Ten prior disclosures kept safe completion at 38/48. On four frozen task instances, change-aware recovery completed safely in {ms["change_aware"]["safe"]}/36 exposures and used {ca["saving"]:.1f}% fewer end-to-end tokens than restart. A separate replay quantifies the CPU and memory cost of the tracking decisions.'
                ],
            ),
            (
                "1  Problem and workload",
                [
                    "The client specifies a sector, candidate tickers, budget and stop-loss percentage. Researchers consult the market and news. The planner selects buy-rated candidates in-sector, splits the budget equally and emits typed steps. The executor verifies identity, trades, sets stops and writes a report. The environment simulates balances, positions, receipts, files and external effects.",
                    "A wrong rating can change both the selected stocks and the allocation for an otherwise unaffected stock. A lost acknowledgement creates a different problem: the order may already have changed the account. We ask which prior results can still be used and what evidence makes that decision defensible.",
                ],
            ),
        ],
    )
    p.column(
        RIGHT,
        529,
        [
            (
                "2  Recovery mechanism",
                [
                    "Each node stores its parents, outputs and tool actions. Each action records state keys read and written. The runtime follows message edges and read-after-write edges, including wildcard reads, from the supplied faulty node. Changes to market or news versions also invalidate affected research and downstream consumers.",
                    "Selective recovery reuses compatible research outside the invalidated set. For executor faults it can restore a verified prefix. Change-aware recovery also examines the repaired plan after an upstream fault. It keeps the longest contiguous prefix that passes operation, argument, prerequisite, receipt and checkpoint checks. From the first mismatch it executes the remaining steps. A changed price invalidates an earlier price-dependent action.",
                    "Recovery runs against isolated simulated state. It can restore internal state while preserving historical external effects. An ambiguous transaction requires status evidence. The runtime queries the service by request ID before repairing the account. An unavailable status stops publication. A rejected candidate can trigger one charged restart fallback.",
                ],
            ),
            (
                "What counts as success?",
                [
                    "The checker tests identity order, exact integer holdings, stops, report consistency and intact funds. Safety also requires a clean authorization history and no historical identity-order violations. Completed-and-safe additionally requires the runtime to publish its recovery. An irreversible disclosure remains in the audit even when the final account is correct."
                ],
            ),
        ],
    )
    p.c.showPage()
    p.page(2, "DEVELOPMENT EVALUATION")
    p.title("Where faults propagate and recovery helps")
    p.para(
        X,
        655,
        "<b>Table 1.</b> Six development tasks, eight scheduled conditions, one observation per cell. Means include source and recovery work. Verification is a separately sampled preventive run.",
        WIDTH,
    )
    p.table(
        618,
        [
            "Method",
            "Correct",
            "Safe",
            "Total tokens",
            "Time (s)",
            "Recomputed",
            "Retained",
        ],
        [
            ["No recovery", "30/48", "20/48", "25,011", "39.91", "-", "-"],
            ["Always verify", "42/48", "42/48", "35,429", "50.52", "-", "-"],
            ["Full restart", "48/48", "38/48", "49,325", "78.60", "8.83", "0.00"],
            ["Selective", "48/48", "38/48", "42,388", "70.31", "5.83", "3.00"],
            ["Change-aware", "48/48", "38/48", "35,784", "60.23", "3.79", "5.04"],
            ["Reconcile", "48/48", "38/48", "28,113", "46.78", "1.62", "2.88"],
        ],
        [93, 54, 50, 83, 66, 86, 72],
    )
    p.column(
        X,
        482,
        [
            (
                "3  Setup and fault schedule",
                [
                    "All Phase 2 runs use Muse Glimmer 30B at temperature zero. The call loop is bounded at eight turns, with one JSON re-ask and matched per-call settings. Each recovery executor has up to three correction turns. Verify may regenerate a rejected intermediate result once. The shared service can vary across calls, and tokens are a more stable compute measure than wall time.",
                    "In Phase 1 the historical v2 Muse clean baseline passed 6/6, averaging 20,672 tokens and 34.7 s. Always verifying increased clean tokens by 45%. These observations use legacy cash and an earlier harness. Table 1 uses ample cash and the frozen v5.1 implementation.",
                    "A decision-relevant wrong report propagated in 6/6 development tasks. The same type of error on an irrelevant ticker was absorbed in 6/6. Mean changed downstream nodes were 3.7 and 1.5, respectively. The relevant fault exposed 15,934 tokens along dependency paths; 13,394 belonged to the target and changed descendants. Exposure overstates necessary recomputation. Stale and incorrect reports can flip the same rating; stale data additionally change price and age.",
                ],
            ),
            (
                "A shared-state failure",
                [
                    "In all six committed execution timeouts, an unprotected retry bought twice. A message check before execution cannot see a future duplicate. Read-only lookup timeouts were harmless in these six source runs."
                ],
            ),
        ],
    )
    p.column(
        RIGHT,
        482,
        [
            (
                "4  Effects of prevention and repair",
                [
                    "The accidental subset contains 24 exposures. Six were safe without intervention. In the 18 adversarial cases, all final accounts were correct without recovery, yet only eight histories were safe. Six irrelevant-report controls were already safe. Combining these yields 20 safe histories in the 48 source runs.",
                    "Recovery rescued 18 of the 28 initially unsafe cases. All four recovery methods still had the ten historical disclosures. Verification prevented disclosures in its own runs but missed the six executor timeouts. These results identify two distinct intervention points.",
                    "Selective and change-aware recovery saved 14.1% and 27.5% of total tokens against restart. The audit matched 144/144 and 242/242 retained nodes to their paired sources. Change-aware revalidated 98 previously invalidated executor-prefix nodes. Reconcile saved 43.0% but includes deterministic execution, so its difference also reflects how operations were performed.",
                ],
            ),
            (
                "Changing environments",
                [
                    "Eight controlled price, rating or news changes were introduced between execution and recovery. Change-aware finished all eight directly. Reconcile fell back on all eight, averaging 62,412 tokens versus 57,887 for restart. Separately, all 24 recovery attempts with unavailable commit status stopped. A safe refusal to publish is counted as incomplete."
                ],
            ),
        ],
    )
    p.c.showPage()
    p.page(3, "INDEPENDENT TASK EVALUATION")
    p.title("Does reuse transfer to new task instances?")
    p.para(
        X,
        655,
        "<b>Table 2.</b> Frozen H5-H8 tasks. Each method has 36 fault exposures (four tasks, three faults, three repeats). Token savings use the ratio of paired totals versus restart.",
        WIDTH,
    )
    names = {
        "none": "No recovery",
        "verify": "Always verify",
        "restart": "Full restart",
        "selective": "Selective",
        "change_aware": "Change-aware",
    }
    rows = []
    for method, name in names.items():
        v = ms[method]
        count = v["status"]
        status = (
            "/".join(
                str(count.get(k, 0))
                for k in ("reconstructed", "fallback_reconstructed", "stopped")
            )
            if method in ps
            else "-"
        )
        rows.append(
            [
                name,
                f"{v['correct']}/36",
                f"{v['safe']}/36",
                f"{v['tokens']:,.0f}",
                f"{v['latency']:.1f}",
                status,
                f"{ps[method]['saving']:.1f}%" if method in ps else "-",
            ]
        )
    p.table(
        618,
        ["Method", "Correct", "Safe", "Tokens", "Time (s)", "Direct/FB/stop", "Saved"],
        rows,
        [97, 57, 47, 81, 70, 90, 62],
    )
    y = 505
    p.para(
        X,
        y,
        "<b>Figure 2.</b> Change-aware token savings versus restart, aggregated within task. Labels give the percent of restart tokens saved. Each row contains nine paired observations.",
        WIDTH,
    )
    for i, (t, v) in enumerate(ca["by_task"].items()):
        yy = y - 47 - i * 15
        p.text(X, yy, t, 10, "Times-Bold")
        start = X + 50
        zero = start + 55
        p.c.setStrokeColor(colors.lightgrey)
        p.c.line(zero, yy + 1, zero, yy + 9)
        length = v * 5
        p.c.setFillColor(TEAL if v >= 0 else GRAY)
        p.c.rect(min(zero, zero + length), yy + 1, abs(length), 8, stroke=0, fill=1)
        p.text(max(zero, zero + length) + 8, yy, f"{v:.1f}%")
    p.column(
        X,
        390,
        [
            (
                "5  Independent evaluation design",
                [
                    "Four new tasks reuse the finance simulator and registered clients but change candidate composition, order, budget and stop. H5 includes two eligible technology stocks. H6-H8 require energy, finance and healthcare selections. Each has positive eligible holdings. The policies, prompts, checker and model budget were frozen before these tasks ran.",
                    "Each repeat has two clean controls per task, then a report-rating error, a three-point increase in the first planned stop percentage, or a committed buy timeout. Each repair starts from the identical faulty trace for its task, fault and repeat. Verify runs independently. Failed controls and missed injections stay in the denominators.",
                    f'Clean controls were safe in {ctl["none"]["safe"]}/12 ordinary and {ctl["verify"]["safe"]}/12 verified runs. The original pilot used unregistered client IDs; all six saved pilot observations are archived separately. The replacement split was fixed before its first model response.',
                ],
            )
        ],
    )
    per_fault = hold["by_fault"]
    fmt = lambda m: ", ".join(
        f"{per_fault[f][m]['safe']}/12" for f in ("R-incorrect", "P-stop", "E-timeout")
    )
    repeat = ", ".join(f"{v:.1f}%" for v in ca["by_repeat"])
    p.column(
        RIGHT,
        390,
        [
            (
                "6  What transferred?",
                [
                    f'For research, plan and execution faults respectively, no recovery completed safely in {fmt("none")} exposures. Verify achieved {fmt("verify")}. Change-aware achieved {fmt("change_aware")}. The injection failed to fire in {ms["none"]["not_fired"]}/36 source runs. Verify flagged {ms["verify"]["detected"]}/36 target nodes and produced {ctl["verify"]["false_alarms"]} false alarms across its clean controls.',
                    f'Change-aware rescued {ca["rescued"]} initially unsafe runs and consumed fewer tokens than restart in {ca["lower"]}/36 pairs. Its savings by repeat were {repeat}. Selective saved {ps["selective"]["saving"]:.1f}% overall. The provenance audit checked {hold["reuse"]["selective"]} retained selective nodes and {hold["reuse"]["change_aware"]} retained change-aware nodes against their source records.',
                    "The effective sample is four new task instances. Repeats expose variation in model output within those tasks. The shared market schema, serial execution model and supplied fault identity limit the claim to transfer within this benchmark. Development and holdout results use different fault mixes and are reported separately.",
                ],
            )
        ],
    )
    p.c.showPage()
    p.page(4, "TRACKING COST AND LIMITS")
    p.title("What does dependency tracking cost?")
    p.para(
        X,
        655,
        "<b>Table 3.</b> Nautilus CPU replay on six recorded clean workflows. Medians across 180 trace-blocks per mode. CPU differences are paired against off; P95 is the 95th percentile of those differences.",
        WIDTH,
    )
    labels = {
        "off": "Off",
        "message": "Messages",
        "raw": "Messages + state RAW",
        "checkpoint": "RAW + checkpoints",
    }
    rows = []
    for k, name in labels.items():
        r = tr[k]
        rows.append(
            [
                name,
                f"{r['cpu_us']:.1f}",
                f"{r['paired_us']:.1f}",
                f"{r['paired_p95_us']:.1f}",
                f"{r['peak_bytes']/1024:.2f}",
                r["missed"],
            ]
        )
    p.table(
        618,
        [
            "Mode",
            "CPU (µs)",
            "Delta (µs)",
            "P95 delta (µs)",
            "Peak (KiB)",
            "Missed edges",
        ],
        rows,
        [127, 65, 70, 90, 73, 79],
    )
    p.column(
        X,
        517,
        [
            (
                "7  Paired replay",
                [
                    "Each block processes the same ordered nodes and receipts with tracking off, message edges only, message plus state reads/writes, and those edges plus checkpoint copies. The full graph is the runtime reference. Six clean traces each receive five warmups per mode and 30 measured blocks. A fixed random seed shuffles mode order within block; 20 replays amortize the clock read. Peak Python allocations are sampled separately.",
                    f'Message-only tracking omits {tr["message"]["missed"]} of {tr["off"]["missed"]} reference edges. Adding shared-state reads and writes finds every recorded reference edge for a median incremental CPU cost of {tr["raw"]["paired_us"]:.1f} microseconds versus off. Checkpointing adds state copies for rollback and raises that cost to {tr["checkpoint"]["paired_us"]:.1f} microseconds. Agreement with recorded edges measures fidelity to this implementation.',
                    "All modes receive already decoded events. The measurement excludes model calls, receipt acquisition, disk writes and interprocess transfer. Peak captures temporary Python allocations above the preloaded input. It is neither resident set size nor a production slowdown estimate.",
                ],
            ),
            (
                "Live validation",
                [
                    "For the development source workflow, live tool bookkeeping averaged 1.165 ms and trace insertion/checkpointing 0.125 ms. Across 524 development artifacts, mean archive serialization was 7.237 ms and writes 19.943 ms. Those timers measure different operations and cannot be added to the replay delta."
                ],
            ),
        ],
    )
    p.column(
        RIGHT,
        517,
        [
            (
                "8  Project experience",
                [
                    "An early executor contract was brittle about report text. T6 exposed a distinction between a harmless trailing newline and a changed purchase claim. The final checker normalizes line endings and terminal newlines for report arguments, preserves substantive content, and bounds correction attempts. Exact share counts exposed errors that looser amount checks masked.",
                    "Fallback accounting also changed the evaluation. A successful final attempt can conceal earlier model work. Each attempted repair now archives usage, overhead and publication status. Recomputing costs from that ledger makes the drift fallback penalty visible. Binding each recovered trace to its faulty source also prevents unrelated resumed runs from forming a pair.",
                ],
            ),
            (
                "Limits and conclusion",
                [
                    "The simulator gives recovery authoritative receipts, restorable internal state and a pinned view during repair. A real distributed service can have concurrent clients, unavailable snapshots and effects with no compensating action. Report text is scored heuristically. Tasks with no eligible stock lie outside this evaluation. Only one model is used for the final comparison.",
                    "A narrower restart boundary saves inference when evidence permits a verified prefix. External disclosure and unknown commitment mark two limits: recreating internal state cannot erase history, and the absence of a receipt can make stopping the only defensible measured outcome.",
                ],
            ),
        ],
    )
    p.c.showPage()
    p.page(5, "RECOVERY COST AND REFERENCES")
    p.title("Recovery-only work and related systems")
    p.para(
        X,
        655,
        "<b>Table 4.</b> Mean additional work after fault localization on the independent set. Time and tokens cover all repair attempts. The end-to-end measures in Table 2 also charge the initial faulty run.",
        WIDTH,
    )
    repair_rows = []
    for m in ("restart", "selective", "change_aware"):
        r = ms[m]["repair"]
        repair_rows.append(
            [
                names[m],
                f"{r['recomputed']:.2f}",
                f"{r['retained']:.2f}",
                f"{r['recovery_tokens']:,.0f}",
                f"{r['recovery_latency']:.2f}",
            ]
        )
    p.table(
        618,
        [
            "Method",
            "Recomputed nodes",
            "Retained nodes",
            "Repair tokens",
            "Repair time (s)",
        ],
        repair_rows,
        [102, 105, 96, 102, 99],
    )
    p.column(
        X,
        535,
        [
            (
                "9  Related work",
                [
                    "TAMAS [1] evaluates the robustness of multi-agent systems under adversarial interactions. A2ASecBench [2] evaluates security in agent-to-agent protocols. These works motivate tracking how a bad output reaches another agent. Our experiment studies the cost and safety of repairing a localized failure after tool effects have occurred, including ordinary timeouts and incorrect data.",
                    "Resilient Distributed Datasets [3] recover lost partitions by recording lineage and recomputing transformations. An agent workflow adds nondeterministic model calls and external writes. Reusing a purchase therefore requires evidence about the original action and the current account, in addition to a path through a graph.",
                    "AWS describes how duplicate requests and delayed responses complicate safe retries [4]. The distinction between committed, uncommitted and unknown order status is the corresponding issue here. A persistent receipt makes some retries decidable. An inaccessible receipt can force a stop even when recomputing the model output is inexpensive.",
                ],
            ),
            (
                "Artifact scope",
                [
                    "The repository includes the workflow, injection harness, strategies, bounded recovery runtime, paired raw traces, receipt and attempt ledgers, checksums and replay measurements. The course PDF is a summary; the archive preserves per-task failures, model usage and exact configurations."
                ],
            ),
        ],
    )
    p.column(
        RIGHT,
        535,
        [
            (
                "Reproducing the evidence",
                [
                    "Install the locked dependencies on Nautilus. The development release uses the v5.1 final experiment script. The independent study is specified in evaluation/holdout.json and evaluation/PROTOCOL.md. Run evaluation/remote.sh with a fresh output directory and the six archived clean traces available. The audit module rebuilds the scores, verifies source pairing and produces the tables consumed by this PDF.",
                    "The companion GitHub branch contains the reporting sources, results and data manifests. The Nautilus runner uses two CPU cores, four GiB of memory and the school-hosted Muse endpoint. Inference is served remotely. The CPU replay runs in the orchestration pod. The holdout service logged 129 API retries across three repeats. Concurrent backoff affects elapsed time; the paired token comparison charges all recorded model usage.",
                ],
            ),
            (
                "References",
                [
                    '[1] I. Kavathekar et al. <link href="https://aclanthology.org/2026.acl-long.1442/" color="#12595d">TAMAS: Benchmarking Adversarial Risks in Multi-Agent LLM Systems.</link> ACL, 2026.',
                    '[2] T. Li et al. <link href="https://github.com/SaFo-Lab/A2ASecBench" color="#12595d">A2ASecBench: A Protocol-Aware Security Benchmark for Agent-to-Agent Multi-Agent Systems.</link> ICLR, 2026.',
                    '[3] M. Zaharia et al. <link href="https://www.usenix.org/conference/nsdi12/technical-sessions/presentation/zaharia" color="#12595d">Resilient Distributed Datasets: A Fault-Tolerant Abstraction for In-Memory Cluster Computing.</link> NSDI, 2012.',
                    '[4] M. Featonby. <link href="https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/" color="#12595d">Making retries safe with idempotent APIs.</link> Amazon Builders\' Library.',
                    '<link href="https://github.com/Tobudase/291A-agent-fault-lab/tree/codex/phase2-final" color="#12595d">Project code and archived evidence (GitHub)</link>',
                ],
            ),
        ],
    )
    p.c.showPage()
    p.c.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "docs/CSE291A-Project5-Final.pdf"
    )
    parser.add_argument("--authors", default="UC San Diego · CSE 291A Project 5 Team")
    args = parser.parse_args()
    main(args.out, args.authors)
