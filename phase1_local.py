"""Run Phase 1 with a LOCAL model (CPU, or the free Colab T4 GPU). No API key needed.

  python phase1_local.py --install                    # Linux/Colab: install Ollama (once per machine/session)
  python phase1_local.py --model qwen3-4b             # preflight -> smoke -> full -> report
  python phase1_local.py --model qwen3-4b --steps report
  python phase1_local.py --model mock                 # 30-second dry run of the whole pipeline (no model)

Model presets (--model); any other value is used as a raw Ollama tag / HF repo id:
  qwen3-4b     Qwen3-4B-Instruct-2507      recommended: Colab T4, or a laptop with >= 8 GB RAM
  qwen3-8b     Qwen3-8B (no-think mode)     GPU only (T4 ok via Ollama q4); slowest, most capable
  qwen2.5-7b   Qwen2.5-7B-Instruct          8B-class alternative without a thinking mode
  qwen3-1.7b   Qwen3-1.7B (no-think mode)   CPU-friendly; small models fail the clean task more often
  llama3.2-3b  Llama 3.2 3B Instruct        second model family (Ollama only)
  llama3.2-1b  Llama 3.2 1B Instruct        tiny; expect weak tool calling (Ollama only)

Backends (--backend): ollama (default; fast quantized CPU/GPU inference) | hf (transformers in-process) | mock.
Outputs: <out-root>/phase1-<model>/  results.jsonl, summary.md, figs/, <task>/<fault>/<method>/trace.json
Interrupted? Run the same command again: finished episodes are skipped (--resume).
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OLLAMA_URL = os.getenv("OLLAMA_HOST_URL", "http://127.0.0.1:11434")

PRESETS = {
    "qwen3-4b": {"ollama": "qwen3:4b-instruct", "no_think": False},
    "qwen3-8b": {"ollama": "qwen3:8b", "no_think": True},
    "qwen2.5-7b": {"ollama": "qwen2.5:7b", "no_think": False},
    "qwen3-1.7b": {"ollama": "qwen3:1.7b", "no_think": True},
    "llama3.2-3b": {"ollama": "llama3.2:3b", "no_think": False},
    "llama3.2-1b": {"ollama": "llama3.2:1b", "no_think": False},
}


def sh(cmd, env=None, check=True):
    print("$", cmd if isinstance(cmd, str) else " ".join(cmd), flush=True)
    r = subprocess.run(cmd, shell=isinstance(cmd, str), env=env, cwd=HERE)
    if check and r.returncode != 0:
        sys.exit(f"command failed (exit {r.returncode})")
    return r.returncode


def has_gpu():
    if shutil.which("nvidia-smi"):
        return subprocess.run(["nvidia-smi", "-L"], capture_output=True).returncode == 0
    return False


def slug(name):
    return "".join(c if c.isalnum() or c in ".-" else "-" for c in name).strip("-")


# ---------------------------------------------------------------- ollama management
def ollama_up():
    try:
        urllib.request.urlopen(OLLAMA_URL + "/api/version", timeout=3)
        return True
    except Exception:
        return False


def install_ollama():
    if shutil.which("ollama"):
        print("ollama already installed:", shutil.which("ollama"))
        return
    if sys.platform != "linux":
        sys.exit("Install Ollama from https://ollama.com/download (macOS/Windows app), start it, then rerun.")
    if shutil.which("apt-get") and os.geteuid() == 0:  # Colab runs as root; the installer needs zstd
        sh("apt-get -qq update && apt-get -qq install -y zstd pciutils lshw > /dev/null", check=False)
    sh("curl -fsSL https://ollama.com/install.sh | sh")


def start_ollama(parallel, ctx):
    if ollama_up():
        print(f"ollama server already running at {OLLAMA_URL} (its own settings apply; "
              f"for best results start it with OLLAMA_CONTEXT_LENGTH={ctx} OLLAMA_NUM_PARALLEL={parallel})")
        return
    exe = shutil.which("ollama")
    if not exe:
        sys.exit("Ollama is not installed. Run: python phase1_local.py --install   "
                 "(or get it from https://ollama.com/download), or use --backend hf.")
    env = {**os.environ, "OLLAMA_CONTEXT_LENGTH": str(ctx), "OLLAMA_NUM_PARALLEL": str(parallel),
           "OLLAMA_KEEP_ALIVE": "-1", "OLLAMA_MAX_LOADED_MODELS": "1"}
    log = open(os.path.join(HERE, "ollama.log"), "a")
    # new session: the server keeps running after this script exits (next notebook cell reuses it)
    # stdin=DEVNULL matters on Colab: a background process holding the cell's terminal keeps the cell "running" forever
    subprocess.Popen([exe, "serve"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                     close_fds=True)
    for _ in range(60):
        if ollama_up():
            print(f"started ollama serve (ctx={ctx}, parallel={parallel}); log: ollama.log")
            return
        time.sleep(1)
    sys.exit("ollama serve did not come up within 60 s; see ollama.log")


def pull(model):
    sh(["ollama", "pull", model])


# ---------------------------------------------------------------- steps
def child_env(a, model, cfg):
    env = {**os.environ, "LLM_MODEL": model, "LLM_NO_THINK": "1" if cfg.get("no_think") else "0",
           "LLM_TIMEOUT": str(a.timeout), "PYTHONUNBUFFERED": "1"}
    if a.backend == "ollama":
        # small local models: cap replies so a runaway generation cannot stall the run
        env.update(LLM_BASE_URL=f"{OLLAMA_URL}/v1", LLM_API_KEY="ollama",
                   LLM_MAX_TOKENS=os.getenv("LLM_MAX_TOKENS", "1024"))
    if a.backend == "api":
        env["LLM_MAX_TOKENS"] = os.getenv("LLM_MAX_TOKENS", "0")  # never cut off a hosted reasoning model
        if a.effort:
            env["LLM_REASONING_EFFORT"] = a.effort
    return env


def step_preflight(a, env):
    if sh([sys.executable, "preflight.py"], env, check=False) != 0:
        msg = "preflight FAILED: the model could not do a tool call + JSON round trip."
        if not a.force:
            sys.exit(msg + " Try another --model (qwen3-4b is the safest), or pass --force to continue anyway.")
        print(msg, "Continuing because of --force.")


def step_smoke(a, env, run_dir):
    """Clean baseline on every task (no faults, ~6 episodes). Faults on a task whose clean run fails are all
    `clean_failed`, so this shows up front how much of the full run will be usable."""
    out = os.path.join(os.path.dirname(run_dir), os.path.basename(run_dir).replace("phase1-", "smoke-", 1))
    cmd = [sys.executable, "run.py", "--tasks", a.tasks, "--clean-only", "--methods", "none",
           "--workers", str(a.workers), "--out", out]
    if os.path.exists(os.path.join(out, 'manifest.json')):
        cmd.append('--resume')
    sh(cmd, env)
    rows = [json.loads(l) for l in open(os.path.join(out, "results.jsonl"))]
    clean = sorted((r for r in rows if r["fault"] == "clean" and r["method"] == "none"), key=lambda r: r["task"])
    ok = [r for r in clean if r["correct"]]
    for r in clean:
        if not r["correct"]:
            print(f"\n#### {r['task']} clean run FAILED")
            explain_failure(os.path.join(out, r["task"], "clean", "none", "trace.json"))
    print(f"\nclean success: {len(ok)}/{len(clean)} tasks  "
          + " ".join(f"{r['task']}={'ok' if r['correct'] else 'FAIL'}" for r in clean))
    if ok:
        lat = sum(r["latency"] for r in ok) / len(ok)
        n_eps = 23 * len(clean)
        est = lat * n_eps * 1.3 / (a.workers * 0.6 if a.workers > 1 else 1)
        print(f"~{lat:.0f}s per clean episode -> rough estimate for the full run ({n_eps} episodes, "
              f"{a.workers} worker(s)): ~{est / 3600:.1f} h")
    need = (len(clean) + 1) // 2
    if len(ok) >= need:
        print(f"SMOKE GO ({len(ok)}/{len(clean)} >= {need}). Faults on the failed tasks will be reported as "
              f"`clean_failed`; Table 1 shows the clean success rate." if len(ok) < len(clean) else "SMOKE GO")
        return
    msg = (f"SMOKE NO-GO: only {len(ok)}/{len(clean)} clean runs are correct; most fault outcomes would be "
           f"`clean_failed`. Details above and in {out}/<task>/clean/none/trace.json.")
    if not a.force:
        sys.exit(msg + " Try a bigger --model, or --force to run anyway.")
    print(msg, "Continuing because of --force.")


def explain_failure(path):
    """Print what the plan said and what the executor did, so a failed clean run can be diagnosed."""
    try:
        d = json.load(open(path))
    except Exception:
        return
    print("\n--- why the clean run failed: plan steps and executor tool calls ---")
    for n in d["trace"]["nodes"]:
        if n["agent"] == "researcher" and not isinstance(n.get("data"), dict):
            print(f"{n['id']}: report is not valid JSON: {n['content'][:200]!r}")
        if n["agent"] == "planner":
            for i, st in enumerate(n.get("data") or []):
                print(f"plan step {i}: {st.get('instruction')}")
            if not n.get("data"):
                print("planner produced NO usable steps. Its raw reply (first 600 chars):")
                print("   ", repr(n["content"][:600]))
                print("   (empty or cut off -> thinking model hitting the token limit, or not JSON)")
    for act in d["log"]:
        if act["by"].startswith("exec"):
            args = {k: (v[:40] + "...") if isinstance(v, str) and len(v) > 40 else v for k, v in act["args"].items()}
            print(f"  {act['by']:7s} {act['tool']}({args}) -> {act['result'][:90]}")
    print("positions:", d["state"]["positions"], " cash:", round(d["state"]["cash"], 2))
    print("metrics checks:", d["metrics"].get("checks"))
    print("------------------------------------------------------------------\n")


def step_full(a, env, run_dir):
    cmd = [sys.executable, "run.py", "--methods", a.methods, "--workers", str(a.workers),
           "--out", run_dir, "--tasks", a.tasks, "--faults", a.faults]
    if os.path.exists(os.path.join(run_dir, 'manifest.json')):
        cmd.append('--resume')
    if a.extra_faults:
        cmd.append("--extra-faults")
    sh(cmd, env)


def step_report(a, env, run_dir):
    if not os.path.exists(os.path.join(run_dir, "results.jsonl")):
        sys.exit(f"no results yet in {run_dir}; run the full step first")
    sh([sys.executable, "summarize.py", run_dir], env)
    others = sorted(d for d in glob.glob(os.path.join(a.out_root, "phase1-*"))
                    if os.path.isdir(d) and d != run_dir and os.path.exists(os.path.join(d, "results.jsonl"))
    )
    sh([sys.executable, "figures.py", run_dir] + others + ["--out", os.path.join(a.out_root, "figs-compare")], env)
    # one zip with everything needed for the report (tables, figures, raw results, traces)
    archive = shutil.make_archive(run_dir.rstrip("/") + "_results", "zip", os.path.dirname(run_dir),
                                  os.path.basename(run_dir))
    print(f"\nsummary : {run_dir}/summary.md\nfigures : {run_dir}/figs/  (+ {a.out_root}/figs-compare/fig7_models.png "
          f"when 2+ runs exist)\nzip     : {archive}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3-4b", help="preset name (see above), raw Ollama tag, or API model id")
    ap.add_argument("--backend", default=None, choices=["ollama", "api"],
                    help="default: ollama. api = hosted OpenAI-compatible API (TritonAI): set OPENAI_BASE_URL and "
                         "OPENAI_API_KEY, pass the model id as --model (e.g. api-glm-5.3)")
    ap.add_argument("--effort", default=None, help="api backend: reasoning_effort (low|high) for reasoning models")
    ap.add_argument("--steps", default="all", help="comma list of preflight,smoke,full,report or 'all'")
    ap.add_argument("--install", action="store_true", help="install Ollama (Linux/Colab) and exit")
    ap.add_argument("--tasks", default="all", help="e.g. T1,T2,T3 for a shorter CPU run (default all 6)")
    ap.add_argument("--faults", default="all")
    ap.add_argument("--methods", default="none,restart,verify")
    ap.add_argument("--extra-faults", action="store_true", help="add the R-distractor fault")
    ap.add_argument("--workers", type=int, default=None, help="parallel episodes (default: 1 for the API, 4 on GPU, 1 on CPU)")
    ap.add_argument("--ctx", type=int, default=8192, help="Ollama context length")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per LLM call before retrying")
    ap.add_argument("--out-root", default="runs", help="e.g. /content/drive/MyDrive/291A-runs on Colab")
    ap.add_argument("--force", action="store_true", help="continue even if preflight/smoke fail")
    a = ap.parse_args()

    if a.install:
        install_ollama()
        return
    cfg = PRESETS.get(a.model, {"ollama": a.model, "no_think": "qwen3:" in a.model
                                and "instruct" not in a.model and "2507" not in a.model})
    a.backend = a.backend or "ollama"
    if a.backend == "api":
        cfg = {"api": a.model, "no_think": False}
        if not (os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")) or \
                not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
            sys.exit("--backend api needs OPENAI_BASE_URL and OPENAI_API_KEY in the environment")
    model = {"ollama": cfg.get("ollama"), "api": cfg.get("api")}[a.backend]
    if not model:
        sys.exit(f"preset {a.model} has no {a.backend} model; pick another --backend or --model")
    gpu = has_gpu()
    if a.workers is None:
        a.workers = 1 if a.backend == "api" else (4 if gpu else 1)  # API: parallel episodes trigger rate limits
    a.out_root = os.path.abspath(a.out_root)
    run_dir = os.path.join(a.out_root, f"phase1-{slug(a.model if a.model in PRESETS else model)}")
    steps = ["preflight", "smoke", "full", "report"] if a.steps == "all" else a.steps.split(",")
    print(f"model={model} backend={a.backend} gpu={gpu} workers={a.workers} steps={steps}\nout={run_dir}\n")

    if a.backend == "ollama":
        start_ollama(a.workers, a.ctx)
        pull(model)
    env = child_env(a, model, cfg)
    t0 = time.time()
    for s in steps:
        print(f"\n========== {s} ==========", flush=True)
        if s == "preflight":
            step_preflight(a, env)
        elif s == "smoke":
            step_smoke(a, env, run_dir)
        elif s == "full":
            step_full(a, env, run_dir)
        elif s == "report":
            step_report(a, env, run_dir)
        else:
            sys.exit(f"unknown step {s}")
    print(f"\ndone in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
