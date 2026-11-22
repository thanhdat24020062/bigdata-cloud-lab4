# Lab 4 — Decision Support & Data-Driven Action (Self-Simulation Guide)
**Five-Student Group Lab · Full Local Self-Simulation · Continues Lab 3**

Build a transparent, leakage-free anomaly decision rule for 20 temperature sensors (`S01–S20` across `zone-1..zone-4`), enforce independent approval before issuing a sandbox `INSPECT_SENSOR` ticket, survive stream replay and an injected acknowledgement failure without duplicate tickets, and verify an append-only SHA-256 audit log against an external anchor. No real hardware devices, emails, or external webhooks are used.

> Read `Lab4_Decision_Support_Data_Driven_Action.docx` for full formulas, background theory, role assignments, and the **Master KPI Scorecard**.

## 1. Five Assessed Functions
- Complete all **4 functions** in `templates/decision_impl.py` before initializing your sandbox: `fit_baseline`, `score_event`, `evaluate`, and `make_action_key`.
- Complete the **5th function** `verify_audit` in `templates/audit_impl.py` for Task T5.
- Run the 8 supplied toy unit tests:
  ```bash
  python -m unittest discover -s tests -v
  ```

## 2. Tasks T1 & T2 — Historical Baseline & Validation Calibration
From the `Lab4-Student-Starter` root directory:
```bash
python tools/run_lab.py preflight --trusted-manifest manifests/inputs_convenience.json
python tools/run_lab.py baseline  --trusted-manifest manifests/inputs_convenience.json
python tools/run_lab.py calibrate --trusted-manifest manifests/inputs_convenience.json --team team-01
```

## 3. Initialize Local Sandbox & Launch Group Action Server (T3–T5)
Once `calibrate` finishes and `templates/decision_impl.py` is finalized and frozen:
```bash
# Generate work/lock_receipt.json, sandbox config, and 4 role tokens in work/sandbox/tokens/
python tools/init_sandbox.py

# Open a dedicated terminal window and keep the Action Server running across T3-T5:
python src/action_server.py --config work/sandbox/service_config.json --state work/sandbox/databases --port 8765
```
*(If you later need to fix a bug in `decision_impl.py`, re-run `calibrate` and then `python tools/init_sandbox.py --reset`).*

## 4. Task T3 — Three Spark Streaming Controls (`first`, `restart`, `replay`)
Load `producer.token` into `LAB4_PRODUCER_TOKEN`:
- Linux / macOS / Git Bash: `export LAB4_PRODUCER_TOKEN=$(cat work/sandbox/tokens/producer.token)`
- Windows PowerShell: `$env:LAB4_PRODUCER_TOKEN = (Get-Content work/sandbox/tokens/producer.token).Trim()`

```bash
python tools/stream_decisions.py --mode spark --trusted-manifest manifests/inputs_convenience.json --checkpoint work/checkpoint-1 --label first
python tools/stream_decisions.py --mode spark --trusted-manifest manifests/inputs_convenience.json --checkpoint work/checkpoint-1 --label restart
python tools/stream_decisions.py --mode spark --trusted-manifest manifests/inputs_convenience.json --checkpoint work/checkpoint-2 --label replay
```
Do not delete `work/checkpoint-1` before `restart`. Required outcomes: `first (new=600, replay=0)`, `restart (new=0, replay=0)`, and `replay (new=0, replay=600)` — preserving exactly 600 unique ledger decisions.

## 5. Task T4 — Governance Probes, Independent Review & HTTP 503 Fault Recovery
1. Load each role holder's token into `LAB4_TOKEN` and run all 6 negative probes (`G01–G06`):
   ```bash
   # G01 (any token - tool sends an invalid credential -> HTTP 401):
   python tools/probe_governance.py bad-token       --out work/G01.json
   # G02 (member E using analyst.token -> HTTP 403):
   python tools/probe_governance.py viewer-review   --out work/G02.json
   # G03 & G04 (member A using producer.token -> HTTP 403 & 409):
   python tools/probe_governance.py self-review     --out work/G03.json
   python tools/probe_governance.py changed-payload --out work/G04.json
   # G05 & G06 (member D using reviewer.token -> HTTP 400 & 409):
   python tools/probe_governance.py short-reason    --out work/G05.json
   python tools/probe_governance.py nonalert-review --out work/G06.json
   ```
2. Before approving any alert, load `operator.token` and record `G07` (`delivered: 0`):
   ```bash
   python tools/action_client.py dispatch --out work/before_approval.json
   ```
3. Load `reviewer.token` (member D), snapshot the queue, and review **5 distinct `PENDING` `ALERT` decisions** (`3 --approve yes` and `2 --approve no` with specific reasons `>= 8` characters, saved to `work/review_01.json .. review_05.json` for `G08`):
   ```bash
   python tools/action_client.py snapshot --out work/pre_review.json
   python tools/action_client.py review --decision-id <SELECTED_ID> --approve yes \
     --reason "Source and score checked; sandbox inspection warranted" --out work/review_01.json
   ```
4. Load `operator.token` into `LAB4_TOKEN` and run the fault & idempotent recovery sequence (`G09–G11`):
   ```bash
   python tools/action_client.py pause    --out work/paused.json
   python tools/action_client.py dispatch --out work/blocked.json
   python tools/action_client.py resume   --out work/resumed.json
   python tools/action_client.py dispatch --fail-after-effect --out work/failure.json
   python tools/action_client.py dispatch --out work/recovered.json
   python tools/action_client.py dispatch --out work/repeated.json
   ```
   After recovery (`recovered.json`), the receiver holds **exactly 3 distinct tickets** (`receiver_deduplicated=1`), not 4.

## 6. Task T5 — Hash-Chain Audit, Dashboard & Clean Portal Packaging
Keep `operator.token` in `LAB4_TOKEN`:
```bash
python tools/action_client.py snapshot --out work/final_snapshot.json
python tools/action_client.py anchor   --out work/ta_anchor.json

python tools/audit_challenge.py --snapshot work/final_snapshot.json --anchor work/ta_anchor.json
python tools/build_dashboard.py --snapshot work/final_snapshot.json --anchor work/ta_anchor.json
```
Complete `templates/report.md` (max 4 pages), `templates/contributions.csv`, and `templates/governance_tests.csv` (`G01–G12`), then run the automated pre-submission validator and packager (which automatically excludes `work/sandbox/` tokens/DBs and `work/checkpoint-*`):
```bash
python tools/pack_submission.py --team team-01
```
