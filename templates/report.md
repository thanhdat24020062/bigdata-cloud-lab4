# Lab 4 group report — maximum four pages excluding machine evidence
## 1. Identity and mode
Team, members, approved Lab 3 release or declared fallback; hashes, table/version
binding and history cutoff; Spark or explicit functional fallback.
## 2. Policy selection
*Owner: Member C (`score_event`, `evaluate`, `calibrate`). Evidence: `work/validation_report.json`,
`work/candidate_policy.json`, `work/validation_predictions.json`.*

**Scoring rule.** `score = |temperature_c - median_c| / scale_c`, computed with `Decimal` and rounded
ROUND_HALF_UP to 6 dp; the comparison `score >= threshold` uses the rounded string. Priority order:
(1) sensor missing or `n < 30` -> HOLD/INSUFFICIENT_BASELINE; (2) unparseable, NaN/Inf or outside
[-30, 60] C -> HOLD/INVALID_VALUE; (3) arrival lag `ingest - event > 900 s` -> HOLD/LATE_FOR_ACTION
with the score retained; (4) ALERT/THRESHOLD; (5) NORMAL/BELOW_THRESHOLD. Unparseable timestamps are
routed to LATE_FOR_ACTION because timeliness cannot be proven. All constants come from
`schemas/decision_contract.json`.

**Validation table** (2026-02-10, n = 600, positive_total = 90, HOLD = 20 of which 4 positive):

| Policy | Alerts | TP | FP | TN | FN | Precision | Recall (eligible) | E2E recall | F1 | cost_units | cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **t = 1.50 (selected)** | 117 | 85 | 32 | 462 | 1 | 0.726 | 0.988 | 0.944 | 0.837 | **804** | 80.4 |
| t = 2.00 | 105 | 82 | 23 | 471 | 4 | 0.781 | 0.953 | 0.911 | 0.859 | 840 | 84.0 |
| t = 2.50 | 77 | 61 | 16 | 478 | 25 | 0.792 | 0.709 | 0.678 | 0.748 | 1764 | 176.4 |
| t = 3.00 | 57 | 47 | 10 | 484 | 39 | 0.825 | 0.547 | 0.522 | 0.657 | 2364 | 236.4 |
| t = 3.50 | 31 | 27 | 4 | 490 | 59 | 0.871 | 0.314 | 0.300 | 0.462 | 3252 | 325.2 |
| never_alert | 0 | 0 | 0 | 494 | 86 | None | 0.000 | 0.000 | 0.000 | 4500 | 450.0 |
| temperature_ge_30 | 50 | 38 | 12 | 482 | 48 | 0.760 | 0.442 | 0.422 | 0.559 | 2820 | 282.0 |

Coverage = 580/600 = 0.967 for every row (the same 20 rows are held under every policy).

**Denominators.** TP/FP/TN/FN are counted on eligible rows only (ALERT + NORMAL = 580).
Precision = TP/(TP+FP); eligible recall = TP/(TP+FN) = 85/86; F1 = 2TP/(2TP+FP+FN);
coverage = eligible/n; end-to-end recall = TP/positive_total = 85/90, where positive_total also
includes the 4 `held_positive` anomalies. A zero denominator returns `None` (never_alert precision).

**HOLD burden.** All 20 held rows are LATE_FOR_ACTION (lag > 900 s); no sensor lacks baseline and no
value is invalid. HOLD costs 10 x 20 = 200 units under every candidate, so it does not change the
ranking, but it hides 4 true anomalies from eligible recall. This is why eligible recall (0.988)
overstates end-to-end recall (0.944): routing positives to HOLD removes them from the eligible
denominator while they still count in positive_total.

**False-negative penalty and choice.** `cost_units = 50 FN + 10 FP + 2 alerts + 10 HOLD`. A missed
anomaly costs five false inspections, so the proxy favours sensitivity. Moving from 2.00 to 1.50
saves 3 FN (-150) at the price of 9 FP (+90) and 12 extra reviews (+24): net -36 units, so 1.50 is
the minimum-cost candidate (tie-break FN -> FP -> higher threshold was not needed). Both comparators
are much worse: never_alert pays 50 x 86 for misses, and temperature_ge_30 cannot detect cold drops
or sensor-relative spikes (FN = 48), whereas the per-sensor robust score is two-sided.

**Fixed grid and independent lock.** Only the five contract thresholds were evaluated; no
interpolation or extra values, which limits overfitting to 600 synthetic rows / 90 positives.
`candidate_policy.json` binds threshold 1.50 to `baseline_sha256`, `contract_sha256` and
`decision_code_sha256`; `init_sandbox.py` copies that hash into `lock_receipt.json` and the code is
frozen. Test labels are never read and Test (2026-02-11) is streamed once with the frozen policy;
no threshold is retuned on Test. Limitation: Validation labels are synthetic injected episodes, so
the cost-optimal threshold reflects that injection design, not real incident rates.
## 3. Replay and review
First/restart/fresh-checkpoint evidence; semantic key fields; five selected alerts;
three independent approvals and two rejections, with record-specific reasons.
## 4. Failure and accountability
Before/after control/outbox/receiver states. Explain the commit/acknowledgement
window, why retry does not add a fourth ticket, and why PAUSE is not undo.
## 5. Dashboard and limitations
`work/dashboard.html` (six sections) is rendered only after `verify_audit` passes against the anchor; it also checks that the Gold context equals the inherited `gold_lab3.json` and that the snapshot `policy_id` equals the locked policy.

- **Gold context:** 20 historical rows as context, not training labels. Weighted site means (sum/count): zone-1 22.3123, zone-2 22.8136, zone-3 23.3132, zone-4 23.3979.
- **Decisions:** 600 = 467 NORMAL + 113 ALERT + 20 HOLD. The 113 ALERTs are 3 DISPATCHED, 2 REJECTED and 108 PENDING; only five were reviewed, pending alerts are not delivered, and the receiver holds 3 distinct tickets.
- **Observability:** 6 callback samples, nearest-rank p95 0.053 s. This is bounded callback time, not event-to-action latency or an SLO, and 6 samples are too few for a stable p95.
- **Test metrics:** no independently supplied `test_metrics_TA.json` was available, so none are reported; Test labels were never read.
- **Anchored audit:** 622 entries, anchor count 622, last_hash `9cb13e5c...`. `audit_challenge`: original True; edited, truncated and rehash-with-old-anchor all False (4/4).

Limitations: (1) The stream adapter collects at most 1,001 rows per batch on the driver (`limit(1001).collect()`, one file per trigger, `local[2]`), so it shows correctness, not cluster-scale throughput. (2) Validation labels are synthetic injected episodes. (3) No HA evidence: one server process, one SQLite file per store, no failover or backup, single-node Spark. (4) The anchor was exported from the same sandbox by the operator role, so it demonstrates the verification mechanism but is not an independent trust anchor; in production it must be kept outside the control database. (5) The audit holds two DENIED entries from E's session beyond the planned probes: G02 and one accidental unauthenticated snapshot call. The server was found stopped and was restarted with the same config and state; startup only runs CREATE IF NOT EXISTS / INSERT OR IGNORE and writes no audit entry.
## 6. Individual contributions and assistance
Link authored code/commands and cross-checks. Record external/AI help and how each
member independently verified the result. Never include credentials.

- **C (scoring, metrics, validation selection):** `score_event`/`evaluate` were drafted with AI assistance (Claude). C verified them independently: 8 public tests (6 C tests pass; 2 key tests depend on D), hand recomputation of cost_units 804 = 50*1 + 10*32 + 2*117 + 10*20, HOLD = 20 equals the rows with lag > 900 s, and extra edge cases (lag 900 vs 901 s, NaN/Inf/empty/60.01, half-up rounding, duplicate ids and non-0/1 labels raise ValueError).

- **E (audit verification, dashboard):** `verify_audit` was drafted with AI assistance (Claude). E verified it independently: 14 own unit tests (tampered entry, truncation, rehash with old anchor, empty log, malformed entries, NaN, bool seq), an `audit_challenge.py` dry run on a synthetic chain, then the real snapshot/anchor (4/4). E also cross-checked the team's code lock: `templates/decision_impl.py` on main (c8d8cfdb...) differed from the hash in `lock_receipt.json` (6313979b...) by comments only, and the frozen file was restored via PR so the hashes match. Read-only database check before G02: 600 decisions, 3 tickets, 2 REJECTED, 620 audit entries.
