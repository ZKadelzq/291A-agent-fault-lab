#!/usr/bin/env bash
# Execute on the authorized remote runner. Never silently replace a completed run.
set -euo pipefail
cd "$(dirname "$0")/.."
root=${1:?output root required}
export PYTHONUNBUFFERED=1
export TASK_CASH=ample
mkdir -p "$root"
python -m pip freeze > "$root/dependencies.txt"
python -m pytest -q tests > "$root/tests.log"
python preflight.py > "$root/preflight.log"
methods=none,restart,verify,selective,change_aware,reconcile
source scripts/nautilus_phase2_runlib.sh
export PHASE2_RESUME=1
run_phase2_matrix "$root/committed" --extra-faults --cash ample --workers 2 --methods "$methods"
run_phase2_matrix "$root/not-committed" --faults E-timeout --timeout-outcome not_committed --cash ample --workers 2 --methods "$methods"
run_phase2_matrix "$root/unknown" --faults E-timeout --timeout-outcome unknown --cash ample --workers 2 --methods "$methods"
python audit_phase2.py --committed "$root/committed" --not-committed "$root/not-committed" --unknown "$root/unknown" --out "$root/MATRIX_AUDIT.md"
for scenario in committed not-committed unknown; do
  python summarize.py "$root/$scenario"
  python figures.py "$root/$scenario"
done
# Two additional independent repeats of three predeclared mechanisms. Together
# with the full grid, each selected cell has three repetitions. No seed guarantee.
for repeat in 2 3; do
  run_phase2_matrix "$root/repeat-$repeat" --tasks T1,T6 --faults R-incorrect,E-timeout,P-malicious --cash ample --workers 2 --methods "$methods"
  python summarize.py "$root/repeat-$repeat"
done
# All six tasks: changed decision input. Plus price and irrelevant-news controls.
for spec in 'T1 AAPL hold' 'T2 CVX buy' 'T3 GS buy' 'T4 JNJ buy' 'T5 MSFT buy' 'T6 CVX buy'; do
  read -r task ticker rating <<< "$spec"
  folder="$root/drift-rating-$task"
  if [[ ! -f "$folder/manifest.json" ]]; then
    python scripts/run_market_drift.py --task "$task" --fault E-timeout --ticker "$ticker" --rating "$rating" --out "$folder"
  fi
done
if [[ ! -f "$root/drift-price-T1/manifest.json" ]]; then
  python scripts/run_market_drift.py --task T1 --fault E-timeout --ticker AAPL --price 200 --out "$root/drift-price-T1"
fi
if [[ ! -f "$root/drift-news-T1/manifest.json" ]]; then
  python scripts/run_market_drift.py --task T1 --fault R-distractor --ticker INTC --news 'Updated company headline; unchanged rating and price.' --out "$root/drift-news-T1"
fi
printf '%s\n' 'All predeclared live runs completed.' > "$root/LIVE_RUNS_COMPLETE"
