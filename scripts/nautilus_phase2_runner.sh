#!/usr/bin/env bash
set -euo pipefail

# Run inside the dedicated ecepxie Job. The model server is the Ollama sidecar.
export PHASE2_MODEL="${PHASE2_MODEL:-qwen3:30b-instruct}"
export PHASE2_SLUG="${PHASE2_SLUG:-qwen3-30b-instruct}"
export PHASE2_RESUME="${PHASE2_RESUME:-0}"
cd "${PHASE2_REPO_DIR:-/work/291A-agent-fault-lab}"
source scripts/nautilus_phase2_runlib.sh
rm -f "/work/READY_TO_COPY-$PHASE2_SLUG" "/work/COPIED_TO_LOCAL-$PHASE2_SLUG"
export LLM_BASE_URL=http://127.0.0.1:11434/v1
export LLM_API_KEY=ollama
export LLM_MODEL="$PHASE2_MODEL"
export LLM_TEMPERATURE=0
export LLM_MAX_TOKENS=4096
export LLM_TIMEOUT=900
export LLM_RETRIES=3
export PYTHONUNBUFFERED=1

python - <<'PY'
import json, os, time, urllib.request
model_name = os.environ['PHASE2_MODEL']
slug = os.environ['PHASE2_SLUG']
base = 'http://127.0.0.1:11434'
for attempt in range(120):
    try:
        urllib.request.urlopen(base + '/api/version', timeout=3).close()
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit('Ollama sidecar did not become ready')
req = urllib.request.Request(base + '/api/pull',
    data=json.dumps({'model': model_name, 'stream': False}).encode(),
    headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=7200) as response:
    print('model pull:', json.load(response).get('status'), flush=True)
with urllib.request.urlopen(base + '/api/tags', timeout=30) as response:
    models = json.load(response)['models']
model = next(m for m in models if m['name'] == model_name)
from pathlib import Path
identity = Path(f'/work/model_identity-{slug}.json')
if identity.exists():
    old = json.loads(identity.read_text())
    if old['digest'] != model['digest']:
        raise SystemExit('model digest changed; refusing to mix results')
else:
    identity.write_text(json.dumps(model, indent=2) + '\n')
print('model digest:', model['digest'], flush=True)
PY

python preflight.py

# A clean-only check prevents a full fault matrix from hiding a weak base model.
run_phase2_matrix "/work/preflight-clean-$PHASE2_SLUG" \
  --clean-only --methods none --workers 2
python - <<'PY'
import json, os
from pathlib import Path
rows = [json.loads(x) for x in Path(f"/work/preflight-clean-{os.environ['PHASE2_SLUG']}/results.jsonl").read_text().splitlines()]
good = sum(bool(r['safe_success']) for r in rows)
print(f'clean model gate: {good}/{len(rows)}', flush=True)
if good != 6:
    raise SystemExit('clean model gate failed; full experiment not started')
PY

methods=none,restart,verify,selective,change_aware,reconcile
run_phase2_matrix "/work/phase2-$PHASE2_SLUG-committed" \
  --extra-faults --cash ample --workers 2 --methods "$methods"
run_phase2_matrix "/work/phase2-$PHASE2_SLUG-not-committed" \
  --faults E-timeout --timeout-outcome not_committed --cash ample \
  --workers 2 --methods "$methods"
run_phase2_matrix "/work/phase2-$PHASE2_SLUG-unknown" \
  --faults E-timeout --timeout-outcome unknown --cash ample \
  --workers 2 --methods "$methods"

for folder in "/work/phase2-$PHASE2_SLUG-committed" "/work/phase2-$PHASE2_SLUG-not-committed" "/work/phase2-$PHASE2_SLUG-unknown"; do
    python summarize.py "$folder"
    python figures.py "$folder"
done
python audit_phase2.py \
  --committed "/work/phase2-$PHASE2_SLUG-committed" \
  --not-committed "/work/phase2-$PHASE2_SLUG-not-committed" \
  --unknown "/work/phase2-$PHASE2_SLUG-unknown" \
  --out "/work/PHASE2_AUDIT-$PHASE2_SLUG.md"
git rev-parse HEAD > "/work/source_commit-$PHASE2_SLUG.txt"
sha256sum /config/phase2.patch > "/work/source_patch_sha256-$PHASE2_SLUG.txt"
touch "/work/READY_TO_COPY-$PHASE2_SLUG"
echo 'Phase 2 live matrix finished and audited; waiting for result copy.'
while [[ ! -f "/work/COPIED_TO_LOCAL-$PHASE2_SLUG" ]]; do sleep 15; done
