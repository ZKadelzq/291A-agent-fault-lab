"""Phase 1 figures (PNG + PDF), drawn from runs/<exp>/results.jsonl and trace.json files.

  python figures.py runs/phase1-glm                       # figures for one run -> runs/phase1-glm/figs/
  python figures.py runs/phase1-glm runs/phase1-muse      # + cross-model comparison -> figs/fig7_models.*

fig1_architecture  pipeline, shared state, fault injection points, provenance trace
fig2_path          one real propagation path (which nodes the fault reached)
fig3_outcomes      what each fault did to the outcome (accidental and adversarial in separate panels)
fig4_cost          end-to-end cost vs. correctness for none / restart / verify
fig5_precision     how many nodes each tracking granularity would invalidate vs. how many really changed
fig6_heatmap       task x fault outcome grid
fig7_models        harm rate per fault for several models (only with >= 2 run dirs)

Colours: the validated reference categorical palette (blue/orange/aqua) plus neutrals.
Every coloured mark is also labelled directly (counts, names), so colour is never the only cue.
"""
import os
import sys
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from analysis import (ACCIDENTAL, ADVERSARIAL, CONTROL, OUTCOMES, faults_in, invalidation_sets, load,  # noqa: E402
                      load_trace, mean, outcome_counts, METHOD_ORDER)

INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"
BLUE, ORANGE, AQUA, GRAY, NEUTRAL = "#2a78d6", "#eb6834", "#1baf7a", "#8f8e88", "#d4d3cd"
OUT_COL = {"propagated": ORANGE, "masked": "#f5b48f", "absorbed": AQUA, "prevented": BLUE, "ignored": NEUTRAL}
SHOWN = ("propagated", "masked", "absorbed", "ignored")
METH_COL = {"none": GRAY, "restart": BLUE, "verify": ORANGE, "selective": AQUA}
for index, name in enumerate(METHOD_ORDER):
    METH_COL.setdefault(name, ['#8856a7', '#a6761d', '#e7298a'][index % 3])
MODEL_COLS = [BLUE, ORANGE, AQUA]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 10,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "legend.frameon": False,
})


def save(fig, outdir, name):
    os.makedirs(outdir, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(outdir, f"{name}.{ext}"), dpi=200, bbox_inches="tight")
    plt.close(fig)
    print("wrote", os.path.join(outdir, name + ".png"))


def split_faults(rows):
    present = faults_in(rows)
    return [("Accidental faults", [x for x in present if x in ACCIDENTAL or x not in ADVERSARIAL + CONTROL]),
            ("Control", [x for x in present if x in CONTROL]),
            ("Adversarial faults", [x for x in present if x in ADVERSARIAL])]


# ---------------------------------------------------------------- fig 3
def fig_outcomes(rows, outdir):
    panels = [(t, fs) for t, fs in split_faults(rows) if fs]
    fig, axes = plt.subplots(1, len(panels), figsize=(3.2 * sum(len(f) for _, f in panels) / 1.6 + 1, 3.4),
                             gridspec_kw={"width_ratios": [len(f) + 0.6 for _, f in panels]}, squeeze=False)
    for ax, (title, faults) in zip(axes[0], panels):
        for i, fault in enumerate(faults):
            rs = [r for r in rows if r["fault"] == fault and r["method"] == "none"]
            c, n, bottom = outcome_counts(rs), len(rs), 0
            for oc in OUTCOMES + ["not_fired", "clean_failed"]:
                k = c.get(oc, 0)
                if not k:
                    continue
                col = OUT_COL.get(oc, "#ffffff")
                ax.bar(i, k / n * 100, bottom=bottom, width=0.62, color=col, edgecolor=SURFACE, linewidth=2,
                       hatch="///" if oc in ("not_fired", "clean_failed") else None)
                ax.text(i, bottom + k / n * 50, f"{k}", ha="center", va="center", fontsize=9,
                        color="white" if oc in ("propagated", "prevented") else INK, fontweight="bold")
                bottom += k / n * 100
            ax.text(i, 103, f"n={n}", ha="center", fontsize=7.5, color=INK2)
        ax.set_xticks(range(len(faults)), faults, rotation=25, ha="right")
        ax.set_ylim(0, 110)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.yaxis.grid(True, color=GRID)
        ax.set_axisbelow(True)
        ax.set_title(title)
    axes[0][0].set_ylabel("% of tasks")
    handles = [Patch(facecolor=OUT_COL[o], label=o) for o in SHOWN]
    fig.legend(handles=handles, loc="upper right", ncol=4, bbox_to_anchor=(1, 1.04))
    fig.suptitle("What each injected fault did to the task outcome (no recovery)", x=0.01, ha="left", y=1.04,
                 fontsize=10, fontweight="bold")
    fig.text(0.01, -0.06, "propagated = outcome harmed; masked = duplicate side effect attempted, rejected only by the "
             "environment; absorbed = work changed but outcome right;\nignored = nothing changed (not evidence of "
             "robustness).", fontsize=7.5, color=INK2)
    fig.tight_layout()
    save(fig, outdir, "fig3_outcomes")


# ---------------------------------------------------------------- fig 4
def fig_cost(rows, outdir):
    panels = [(t, fs) for t, fs in split_faults(rows) if fs]
    markers = dict(zip(METHOD_ORDER, ['o', 's', '^', 'D', 'P', 'X']))
    clean = [r["tokens"] for r in rows if r["fault"] == "clean" and r["method"] == "none"]
    fig, axes = plt.subplots(1, len(panels), figsize=(4.0 * len(panels), 3.4), sharey=True, squeeze=False)
    for ax, (title, faults) in zip(axes[0], panels):
        for m in METHOD_ORDER:
            rs = [r for r in rows if r["fault"] in faults and r["method"] == m]
            if not rs:
                continue
            for fault in faults:  # one faint dot per fault
                fr = [r for r in rs if r["fault"] == fault]
                if fr:
                    ax.scatter(mean(r["total_tokens"] for r in fr) / 1000, mean(r["safe_success"] for r in fr),
                               s=22, color=METH_COL[m], marker=markers.get(m, 'o'), alpha=0.35, linewidths=0)
            x, y = mean(r["total_tokens"] for r in rs) / 1000, mean(r["safe_success"] for r in rs)
            ax.scatter(x, y, s=90, color=METH_COL[m], marker=markers.get(m, 'o'),
                       edgecolor=SURFACE, linewidth=1, zorder=3)
        if clean:
            ax.axvline(mean(clean) / 1000, color=INK2, linestyle=":", linewidth=1)
            ax.text(mean(clean) / 1000, 0.02, " clean run", fontsize=7.5, color=INK2, va="bottom")
        ax.set_ylim(-0.05, 1.1)
        ax.set_xlabel("end-to-end tokens per episode (thousands)")
        ax.yaxis.grid(True, color=GRID)
        ax.set_axisbelow(True)
        ax.set_title(title)
    axes[0][0].set_ylabel("safe success rate")
    fig.suptitle("Cost vs. safety (large marker = mean over faults, small = per fault)",
                 x=0.01, ha="left", y=1.03, fontsize=10, fontweight="bold")
    handles = [Line2D([], [], color=METH_COL[m], marker=markers.get(m, 'o'), linestyle='None', label=m)
               for m in METHOD_ORDER if any(r['method'] == m and r['fault'] != 'clean' for r in rows)]
    fig.legend(handles=handles, loc='lower center', ncol=min(6, len(handles)), fontsize=8)
    fig.tight_layout(rect=(0, 0.10, 1, 1))
    save(fig, outdir, "fig4_cost")


# ---------------------------------------------------------------- fig 5
def fig_precision(exp, rows, outdir):
    data = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["method"] != "none" or r["fault"] == "clean" or r.get("outcome") in (None, "not_fired", "clean_failed"):
            continue
        d = load_trace(exp, r["task"], r["fault"])
        if not d:
            continue
        target = {"R": None}.get("R")
        # the faulty node is the first node carrying the fault name
        nodes = d["trace"]["nodes"]
        target = next((n["id"] for n in nodes if n.get("fault") == r["fault"]), None)
        if not target:
            continue
        sets = invalidation_sets(d["trace"], target)
        for k, v in sets.items():
            data[r["fault"]][k].append(len(v))
        data[r["fault"]]["affected"].append(r.get("affected", 0))
        data[r["fault"]]["total"].append(len(nodes) - 1)
    faults = [f for f in faults_in(rows) if f in data]
    if not faults:
        return
    series = [("coarse", "message edges only", GRAY), ("state", "+ shared-state edges", BLUE),
              ("fine", "declared uses + state", AQUA), ("affected", "really changed", ORANGE)]
    fig, ax = plt.subplots(figsize=(max(5.5, 1.35 * len(faults) + 1.5), 3.5))
    w = 0.2
    for j, (k, label, col) in enumerate(series):
        xs = [i + (j - 1.5) * (w + 0.02) for i in range(len(faults))]
        vals = [mean(data[f][k]) for f in faults]
        ax.bar(xs, vals, width=w, color=col, edgecolor=SURFACE, linewidth=1.5, label=label)
        for x, v in zip(xs, vals):
            ax.text(x, v + 0.08, f"{v:.1f}", ha="center", fontsize=7, color=INK2)
    ax.set_xticks(range(len(faults)), faults, rotation=25, ha="right")
    ax.set_ylabel("nodes (mean over tasks)")
    ax.set_ylim(0, max(max(mean(data[f][k]) for f in faults) for k, _, _ in series) * 1.25)
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.legend(ncol=4, loc="upper left", bbox_to_anchor=(0, 1.1), fontsize=8)
    ax.set_title("Dependency precision: nodes recovery would recompute vs. nodes that really changed", y=1.2)
    fig.tight_layout()
    save(fig, outdir, "fig5_precision")


# ---------------------------------------------------------------- fig 6
def fig_heatmap(rows, outdir):
    faults = faults_in(rows)
    tasks = sorted({r["task"] for r in rows})
    letter = {"propagated": "P", "masked": "M", "absorbed": "A", "ignored": "I", "not_fired": "NF",
              "clean_failed": "CF"}
    fig, ax = plt.subplots(figsize=(0.95 * len(faults) + 1.5, 0.5 * len(tasks) + 1.4))
    for i, t in enumerate(tasks):
        for j, f in enumerate(faults):
            r = next((r for r in rows if r["task"] == t and r["fault"] == f and r["method"] == "none"), None)
            if not r:
                continue
            oc = r.get("outcome") or "-"
            ax.add_patch(plt.Rectangle((j, i), 0.94, 0.9, color=OUT_COL.get(oc, "#ffffff"),
                                       hatch="///" if oc in ("not_fired", "clean_failed") else None,
                                       ec=SURFACE if oc in OUT_COL else GRID, lw=1))
            ax.text(j + 0.47, i + 0.45, letter.get(oc, oc), ha="center", va="center", fontsize=9, fontweight="bold",
                    color="white" if oc == "propagated" else INK)
    ax.set_xlim(0, len(faults))
    ax.set_ylim(len(tasks), 0)
    ax.set_xticks([j + 0.47 for j in range(len(faults))], faults, rotation=25, ha="right")
    ax.set_yticks([i + 0.45 for i in range(len(tasks))], tasks)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Outcome per task and fault (no recovery)", pad=24)
    handles = [Patch(facecolor=OUT_COL[o], label=f"{letter[o]} {o}") for o in SHOWN]
    ax.legend(handles=handles, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=8)
    save(fig, outdir, "fig6_heatmap")


# ---------------------------------------------------------------- fig 2
def fig_path(exp, rows, outdir, fault="R-incorrect", task=None):
    cands = [r for r in rows if r["fault"] == fault and r["method"] == "none" and r.get("outcome") == "propagated"]
    if not cands:
        cands = [r for r in rows if r["fault"] == fault and r["method"] == "none" and r.get("outcome") != "not_fired"]
    if not cands:
        return
    r = next((c for c in cands if c["task"] == task), None) if task else None
    r = r or max(cands, key=lambda c: c.get("affected") or 0)
    d = load_trace(exp, r["task"], r["fault"])["trace"]
    nodes = {n["id"]: n for n in d["nodes"]}
    affected = set(r.get("affected_nodes") or [])
    cols = {"researcher": 0, "planner": 1, "executor": 2}
    by = defaultdict(list)
    for n in d["nodes"]:
        by[cols[n["agent"]]].append(n["id"])
    pos = {}
    for c, ids in by.items():
        for k, nid in enumerate(ids):
            pos[nid] = (c * 3.0, -(k - (len(ids) - 1) / 2) * 1.0)
    fig, ax = plt.subplots(figsize=(7.2, 0.62 * max(len(v) for v in by.values()) + 1.6))
    msg = {(p, n["id"]) for n in d["nodes"] for p in n["parents"] if p in nodes}
    for s, t in d["edges"]:
        (x1, y1), (x2, y2) = pos[s], pos[t]
        state = (s, t) not in msg
        ax.add_patch(FancyArrowPatch((x1 + 0.85, y1), (x2 - 0.85, y2), arrowstyle="-|>", mutation_scale=7,
                                     color=INK2 if not state else BLUE, lw=0.8, linestyle="--" if state else "-",
                                     connectionstyle="arc3,rad=0.35" if state else "arc3,rad=0", alpha=0.8))

    def label(n):
        if n["agent"] == "executor":
            acts = [f'{a["tool"]}({", ".join(str(v) for v in list(a["args"].values())[:2])})'
                    for a in n["actions"] if a["tool"] not in ("read_account",)]
            t = acts[0] if acts else "no action"
            return t if len(t) <= 22 else t[:21] + "…"
        if n["agent"] == "researcher" and isinstance(n.get("data"), dict):
            return f'rating: {n["data"].get("rating")}' + (" (wrong)" if n.get("fault") == "R-incorrect" else "")
        return "plan steps: %d" % len(n["data"]) if n["agent"] == "planner" and n.get("data") else ""

    for nid, (x, y) in pos.items():
        n = nodes[nid]
        faulty = bool(n.get("fault"))
        fc = ORANGE if faulty else ("#f8c9b5" if nid in affected else "#eceae4")
        ax.add_patch(FancyBboxPatch((x - 0.85, y - 0.34), 1.7, 0.68, boxstyle="round,pad=0.02,rounding_size=0.08",
                                    fc=fc, ec=INK2 if not faulty else ORANGE, lw=0.9))
        ax.text(x, y + 0.1, nid, ha="center", va="center", fontsize=7.5, fontweight="bold",
                color="white" if faulty else INK)
        ax.text(x, y - 0.14, label(n), ha="center", va="center", fontsize=6.5, color="white" if faulty else INK2)
    for c, name in enumerate(["Researcher", "Planner", "Executor"]):
        ax.text(c * 3.0, max(p[1] for p in pos.values()) + 0.75, name, ha="center", fontsize=9, color=INK2,
                fontweight="bold")
    ax.set_xlim(-1.1, 7.1)
    ax.set_ylim(min(p[1] for p in pos.values()) - 0.7, max(p[1] for p in pos.values()) + 1.1)
    ax.axis("off")
    ax.legend(handles=[Patch(fc=ORANGE, label="fault injected"), Patch(fc="#f8c9b5", label="decision changed"),
                       Patch(fc="#eceae4", label="unchanged")], ncol=3, loc="lower center",
              bbox_to_anchor=(0.5, -0.12), fontsize=8)
    ax.set_title(f"Propagation path of {fault} ({r['task']}); dashed blue = shared-state dependency", loc="left")
    save(fig, outdir, "fig2_path")


# ---------------------------------------------------------------- fig 1
def fig_arch(outdir):
    fig, ax = plt.subplots(figsize=(7.6, 3.5))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 58)
    ax.axis("off")

    def box(x, y, w, h, title, sub="", fc="#eceae4", ls="-"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=1", fc=fc, ec=INK2, lw=1, ls=ls))
        ax.text(x + w / 2, y + h * (0.68 if sub else 0.5), title, ha="center", va="center", fontsize=8, fontweight="bold")
        if sub:
            ax.text(x + w / 2, y + h * 0.3, sub, ha="center", va="center", fontsize=6.3, color=INK2, linespacing=1.3)

    def arrow(a, b, col=INK2, ls="-"):
        ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=8, color=col, lw=1, linestyle=ls))

    box(0.5, 27, 13, 12, "Task", "client, tickers,\nsector, budget")
    box(18, 27, 19, 12, "Researcher", "one per ticker\nlookup_stock, get_news")
    box(42, 27, 16, 12, "Planner", "JSON steps with\n`uses` report ids")
    box(63, 27, 17, 12, "Executor", "one call per step\nbuy, set_stop_loss, ...")
    box(85, 22, 14.5, 22, "World state", "cash, positions,\nstop-loss, KYC,\nfiles, external\n(irreversible)", fc="#dbe8f8")
    for x1, x2 in ((13.8, 17.7), (37.3, 41.7), (58.3, 62.7), (80.3, 84.7)):
        arrow((x1, 33), (x2, 33))
    box(18, 12.5, 40, 7, "Verifier (always-verify baseline)", "checks each report and the plan; regenerates on FAIL", fc="#ffffff", ls="--")
    arrow((27.5, 19.9), (27.5, 26.7), ls="--")
    arrow((50, 19.9), (50, 26.7), ls="--")
    box(0.5, 1, 99, 7, "Provenance trace: every result is a Node tagged with its sources",
        "edges: message (research > plan > exec)  |  declared `uses`  |  shared-state read-after-write", fc="#e3f3ec")
    for x, y, t in ((27.5, 45, "R-incorrect, R-stale, R-timeout\nR-malicious, R-ipi"), (50, 45, "P-malicious"), (71.5, 45, "E-timeout")):
        ax.text(x, y, t, ha="center", va="bottom", fontsize=6.8, color=ORANGE, fontweight="bold", linespacing=1.25)
        arrow((x, y - 0.5), (x, 39.6), col=ORANGE)
    ax.text(0.5, 57.5, "Orange: injected fault (fires once, on one node)", fontsize=7.5, color=ORANGE, va="top", fontweight="bold")
    save(fig, outdir, "fig1_architecture")


# ---------------------------------------------------------------- fig 7
def fig_models(runs, outdir):
    """runs: [(label, rows)]. Harm rate (1 - safe_success) per fault, no recovery."""
    faults = [f for f in faults_in(runs[0][1]) if all(any(r["fault"] == f for r in rs) for _, rs in runs)]
    fig, ax = plt.subplots(figsize=(max(5.5, 1.5 * len(faults) + 1.5), 3.4))
    k = len(runs)
    w = 0.8 / k
    for j, (label, rows) in enumerate(runs):
        xs, vals = [], []
        for i, f in enumerate(faults):
            # tasks whose clean run already failed say nothing about the fault: leave them out
            rs = [r for r in rows if r["fault"] == f and r["method"] == "none" and r.get("outcome") != "clean_failed"]
            xs.append(i + (j - (k - 1) / 2) * w)
            vals.append(100 * (1 - mean(r["safe_success"] for r in rs)))
        ax.bar(xs, vals, width=w * 0.92, color=MODEL_COLS[j % 3], edgecolor=SURFACE, linewidth=1.5, label=label)
        for x, v in zip(xs, vals):
            ax.text(x, v + 1.5, f"{v:.0f}", ha="center", fontsize=7, color=INK2)
    ax.set_xticks(range(len(faults)), faults, rotation=25, ha="right")
    ax.set_ylabel("% of clean-correct tasks harmed")
    ax.set_ylim(0, 112)
    ax.yaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.legend(ncol=k, loc="upper left", bbox_to_anchor=(0, 1.12), fontsize=8)
    ax.set_title("Harm rate per fault by model (no recovery; tasks whose clean run failed excluded)", y=1.18)
    save(fig, outdir, "fig7_models")


def main(argv):
    out_cmp = "figs"
    if "--out" in argv:
        i = argv.index("--out")
        out_cmp = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    runs = []
    for exp in argv:
        rows = load(exp)
        figs = os.path.join(exp, "figs")
        fig_arch(figs)
        fig_path(exp, rows, figs)
        fig_outcomes(rows, figs)
        fig_cost(rows, figs)
        fig_precision(exp, rows, figs)
        fig_heatmap(rows, figs)
        runs.append((rows[0].get("model") or os.path.basename(exp.rstrip("/")), rows))
    if len(runs) > 1:
        fig_models(runs, out_cmp)


if __name__ == "__main__":
    main(sys.argv[1:])
