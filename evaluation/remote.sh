#!/usr/bin/env bash
# Run inside the dedicated Nautilus runner after unpacking the evaluation source.
set -euo pipefail
cd "$(dirname "$0")/.."
root=${1:?supply a new or matching remote results directory}
mkdir -p "$root"
python -m pip install --disable-pip-version-check -r requirements-final.lock > "$root/dependency-install.log" 2>&1
python -m pip freeze > "$root/dependencies.txt"
sha256sum evaluation/holdout.{json,py} evaluation/tracking.py > "$root/protocol.sha256"
matrix=${TRACKING_INPUT:-/work/final-1117/release/committed}
if [[ ! -f "$root/tracking.json" ]]; then
  python -m evaluation.tracking "$matrix" "$root/tracking.json"
fi
for rep in 1 2 3; do
  opts=()
  if [[ -f "$root/repeat-$rep/manifest.json" ]]; then opts+=(--resume); fi
  python -m evaluation.holdout "$root/repeat-$rep" "${opts[@]}"
done
date -u > "$root/RUNNER_FINISHED"
python -m evaluation.audit "$root" --tracking-matrix "$matrix"
