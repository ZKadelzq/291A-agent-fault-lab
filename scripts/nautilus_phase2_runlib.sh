#!/usr/bin/env bash

# An interrupted matrix has its own manifest and selection. New matrices do not
# yet have these files and must start without --resume.
run_phase2_matrix() {
  local out=$1
  shift
  local -a resume_args=()
  if [[ ${PHASE2_RESUME:-0} == 1 ]]; then
    if [[ -f "$out/manifest.json" || -f "$out/selection.json" ]]; then
      [[ -f "$out/manifest.json" && -f "$out/selection.json" ]] || {
        echo "incomplete resume metadata in $out" >&2; return 1;
      }
      resume_args=(--resume)
    fi
  fi
  python run.py "$@" --out "$out" "${resume_args[@]}"
}
