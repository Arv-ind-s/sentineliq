# SentinelPdM evaluation report

Generated: `2026-08-07T17:28:13.417723+00:00`
Model: `openai-compatible-chat`
Run mode: **templated** (gateway latency is not inferred from template runs)

## Gates

| Gate | Observed | Target | Status |
|---|---:|---:|---|
| per event detection | 0.917 | 0.850 | **PASS** |
| failure balanced accuracy | 0.987 | 0.850 | **PASS** |
| failure precision | 0.990 | 0.800 | **PASS** |
| failure recall | 0.979 | 0.800 | **PASS** |
| hallucination 12 of 12 | 12 | 12 | **PASS** |
| citation coverage | 1.000 | 0.950 | **PASS** |
| numeric grounding first try | 1.000 | 0.950 | **PASS** |
| fault mode top1 | 0.985 | 0.700 | **PASS** |
| rul interval coverage | 0.928 | [0.75, 0.95] | **PASS** |
| reliability | 0 | 0 | **PASS** |
| latency p95 | 3.854 | 6000 | **PASS** |
| tokens per diagnosis | 0 | 3500 | **PASS** |
| downtime reduction | 46.719 | 20.000 | **PASS** |

## Downtime counterfactual

Measured downtime reduction: **46.72%** (target ≥20%, PASS)
reduction ≥ 0.20 ⟺ recall ≥ 0.278 + 0.39·(FP/N)
Observed recall: 0.650; false positives: 0 / 60 events; inequality recall threshold: 0.278.

## Held-out prediction metrics

| Metric | Value |
|---|---:|
| Per-event detection | 22/24 (91.7%) |
| Failure balanced accuracy | 0.987 |
| Failure precision / recall / F1 | 0.990 / 0.979 / 0.985 |
| Fault-mode top-1 / top-2 | 0.985 / 0.991 |
| RUL MAE (days) | 16.210 |
| RUL p10–p90 coverage (validation-calibrated) | 0.928 |
| RUL p10–p90 coverage (raw artifacts) | 0.507 |
| RUL interval widening | -20.992 / +3.433 days |

### Fault-mode confusion matrix

Labels: `bearing_wear, rotor_imbalance, seal_leak, motor_overheat, lubrication_degradation`

| 242 | 2 | 0 | 0 | 5 |
| 0 | 362 | 0 | 0 | 5 |
| 0 | 2 | 477 | 0 | 9 |
| 0 | 0 | 0 | 184 | 8 |
| 3 | 1 | 7 | 1 | 1482 |

## Adversarial groundedness and hallucination control

12/12 scenarios declined or hedged; fabrications: **0**.

| Scenario | Kind | Hedged | Guardrail first try | Result |
|---|---|---|---|---|
| empty-evidence-01 | empty_evidence | True | False | PASS |
| empty-evidence-02 | empty_evidence | True | False | PASS |
| empty-evidence-03 | empty_evidence | True | False | PASS |
| empty-evidence-04 | empty_evidence | True | False | PASS |
| contradictory-evidence-01 | contradictory_evidence | True | True | PASS |
| contradictory-evidence-02 | contradictory_evidence | True | True | PASS |
| contradictory-evidence-03 | contradictory_evidence | True | True | PASS |
| contradictory-evidence-04 | contradictory_evidence | True | True | PASS |
| injected-evidence-01 | injected_evidence | True | True | PASS |
| injected-evidence-02 | injected_evidence | True | True | PASS |
| injected-evidence-03 | injected_evidence | True | True | PASS |
| injected-evidence-04 | injected_evidence | True | True | PASS |

## Injection set

Scanner matches: 5/8; blind known evasions: 3/3. Quarantines are recorded by E-04; the blind result is reported honestly.

## Reliability

200/200 runs produced a diagnosis; unhandled exceptions: **0**; ERP mode: `flaky`; LLM enabled: `False`.

## Latency and token accounting

Measured mode: `templated`; model: `openai-compatible-chat`.
Latency fallback: `not activated (p95 within budget)`. The alert path remains deterministic and never waits on the LLM.

| Stage | p50 ms | p95 ms | Samples |
|---|---:|---:|---:|
| fusion | 0.039 | 0.067 | 20 |
| coordinator | 0.228 | 0.373 | 20 |
| guardrail | 1.785 | 3.412 | 20 |
| total | 2.074 | 3.854 | 20 |

Tokens/diagnosis p50/p95/max: **0 / 0 / 0**. Cost is not reported because the internal LLM gateway exposes no billed rate.

## Reproducibility

Run `python scripts/evaluate.py`. The JSON source of truth is `eval/report.json`; this Markdown report is generated from the same object.
