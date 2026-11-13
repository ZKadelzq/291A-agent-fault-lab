#!/usr/bin/env bash
set -euo pipefail

# Mounted from the Job's ConfigMap so it is available before the repository clone.
repo=${PHASE2_REPO_DIR:?set PHASE2_REPO_DIR}
slug=${PHASE2_SLUG:?set PHASE2_SLUG}
resume=${PHASE2_RESUME:-0}
patch=${PHASE2_PATCH_PATH:-/config/phase2.patch}
stamp=${PHASE2_CHECKOUT_STAMP:-/work/source_checkout-$slug.txt}
remote=${PHASE2_REPO_URL:-https://github.com/ZKadelzq/291A-agent-fault-lab.git}
branch=${PHASE2_BRANCH:-phase_2}

[[ "$resume" == 0 || "$resume" == 1 ]] || { echo 'PHASE2_RESUME must be 0 or 1' >&2; exit 1; }
[[ -f "$patch" ]] || { echo "missing source patch: $patch" >&2; exit 1; }
patch_hash=$(sha256sum "$patch" | cut -d' ' -f1)

checkout_identity() {
  cd "$repo"
  [[ -z $(git ls-files --others --exclude-standard) ]] || {
    echo 'checkout has unexpected untracked files; refusing to resume' >&2; exit 1;
  }
  base_commit=$(git rev-parse HEAD)
  diff_hash=$(git diff --binary HEAD | sha256sum | cut -d' ' -f1)
}

write_stamp() {
  mkdir -p "$(dirname "$stamp")"
  printf '%s\n%s\n%s\n' "$base_commit" "$patch_hash" "$diff_hash" > "$stamp.tmp"
  mv "$stamp.tmp" "$stamp"
}

if [[ -e "$repo" ]]; then
  [[ -d "$repo/.git" ]] || { echo "path exists but is not a Git checkout: $repo" >&2; exit 1; }
  [[ "$resume" == 1 ]] || { echo 'checkout already exists; set PHASE2_RESUME=1 to continue' >&2; exit 1; }
  checkout_identity
  if [[ -f "$stamp" ]]; then
    [[ $(wc -l < "$stamp") -eq 3 ]] || {
      echo 'checkout source stamp is incomplete' >&2; exit 1;
    }
    { IFS= read -r saved_commit; IFS= read -r saved_patch; IFS= read -r saved_diff; } < "$stamp"
    [[ "$saved_commit" == "$base_commit" &&
       "$saved_patch" == "$patch_hash" && "$saved_diff" == "$diff_hash" ]] || {
      echo 'checkout or patch changed since the interrupted run; refusing to mix results' >&2; exit 1;
    }
  else
    # Adopt a checkout made by the older Job only if its entire tracked diff
    # exactly matches the supplied patch. An empty patch requires a clean tree.
    [[ "$diff_hash" == "$patch_hash" ]] || {
      echo 'existing checkout has no stamp and does not match the supplied patch' >&2; exit 1;
    }
    write_stamp
  fi
  echo "resuming checkout $base_commit (patch $patch_hash)"
else
  [[ ! -e "$stamp" ]] || { echo 'checkout missing but its source stamp remains; refusing to mix results' >&2; exit 1; }
  git clone --branch "$branch" --single-branch "$remote" "$repo"
  cd "$repo"
  if [[ -s "$patch" ]]; then
    git apply --index --check "$patch"
    git apply --index "$patch"
  fi
  checkout_identity
  write_stamp
  echo "prepared checkout $base_commit (patch $patch_hash)"
fi
