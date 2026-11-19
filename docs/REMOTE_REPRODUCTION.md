# Remote reproduction

Run orchestration, deterministic tests and model experiments on Nautilus, namespace
`ecepxie`, using a dedicated project pod/PVC. Do not run the workflows on a laptop.
The November 17 run used `python:3.11-bookworm`, 2 CPU cores / 4 GiB RAM, the existing
`cse291-phase2-qwen30b` PVC and the school Muse API (no GPU allocation needed).

1. Check out the final branch and install `pip install -r requirements-final.lock`.
2. Supply the school's existing API credentials through a Kubernetes Secret or the
   remote process environment, never a committed `.env` file. Set `LLM_MODEL=api-muse-glimmer-30b`,
   `LLM_TEMPERATURE=0`. API inference runs on the school service.
3. If the endpoint still omits its intermediate TLS certificate, use
   `export SSL_CERT_FILE=$(bash scripts/prepare_api_ca.sh /work/project-certs)`.
   This verifies the intermediate signature against certifi's root store; it does
   not disable hostname or certificate verification. A working complete server
   certificate chain needs no workaround.
4. Run `bash scripts/run_final_experiments.sh /work/phase2-final`.
   Standard matrices support matching `--resume`; completed task/fault groups
   must have matching source IDs. An interrupted drift run has no final manifest:
   preserve that partial directory separately before repeating the whole case.
5. Run `python analyze_phase2_provenance.py --run /work/phase2-final/committed --out /work/phase2-final/PROVENANCE.md`.
6. Run `python reporting/final_report.py /work/phase2-final`.
7. Copy the result directory back, verify `SHA256SUMS.json`, and retain the code
   commit, dependency lock, test/preflight logs and execution source identity.

`LIVE_RUNS_COMPLETE` means the runner reached its last experiment. Only a passing
`RELEASE_AUDIT.json` means the complete artifacts passed the independent release
checks. Neither marker implies all recovery methods succeeded.

Temporary runner pods and the run-specific API Secret can be removed after the
result copy is verified. Keep the shared project PVC and all historical results.

## Independent evaluation and tracking replay

Use the frozen H5–H8 task manifest and model adapter from commit `2b6aed7`.
The `evaluation/` directory contains the protocol, runner and audit. The simulator
only recognizes registered clients. A preflight validates the workload before any
model calls. The six saved observations from an earlier invalid-client pilot are
archived separately with their original manifest.

On Nautilus, provide the same locked dependencies and school endpoint. Kubernetes
passes env-file values literally, so provide environment values without wrapping
quotes. Use a verified issuer chain if required. The tracking input is the original
`committed/` matrix containing `T1` through `T6` clean source traces. Set
`TRACKING_INPUT` to that folder. Set `LLM_MODEL=api-muse-glimmer-30b`,
`LLM_TEMPERATURE=0`, and leave the model output cap at its server default.

```bash
bash evaluation/remote.sh /work/independent-results
```

The four task workers share one endpoint. Repeats run sequentially. Every repeat
saves 68 traces and matching row data. An interrupted group can be resumed using
its saved selection and code signatures. The CPU replay operates on already
decoded events using the standard Trace and World snapshot methods. It makes no
model calls. The final audit rescans the saved account and historical records,
checks exact source pairing and recorded use on each attempt, and recomputes CPU
summaries and omitted edges from 720 observations. Copy the release back and
verify the relative paths in its SHA-256 manifest. Regenerate the course PDF
with `python reporting/course_pdf.py` using the audited artifacts and ReportLab.
