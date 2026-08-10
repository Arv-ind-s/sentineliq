# SentinelPdM model card

## Summary

SentinelPdM uses versioned scikit-learn artifacts to estimate machine failure risk, classify a fault mode, and estimate a remaining-useful-life (RUL) interval from a fixed 41-feature contract (`fv1`). The model is a decision-support component for the seeded predictive-maintenance demo; it is not a safety controller or an autonomous maintenance authority.

The sklearn models produce every numerical prediction and KPI; `gemini-2.5-flash-lite` only narrates the already-computed evidence. Gemini is not part of the >=85% claim. If the Gemini API is unavailable or no key is configured, the numerical path is unchanged and the diagnosis uses the deterministic template.

## Training and evaluation data

The current deterministic dataset is generated with seed 42 and contains 15 machines across three lines, 60 days of eight-channel telemetry, approximately 2.59 million readings, 60 labelled fault events, 12 `sensor_drift` intervals, and 480 maintenance work orders. The generator includes deliberate skew, noise spikes, dropouts, duplicates, Fahrenheit values, and frozen-sensor conditions. The data is synthetic/demo data and must not be treated as a representative sample of a production plant.

The model artifacts record the train/validation/test split and held-out machine controls. The three named held-out machines are:

- `PUMP-07` — planted bearing-wear scenario used in the live alert moment.
- `CNC-11` — seal-leak scenario with intentionally conflicting maintenance history.
- `CONV-14` — connectivity/data-integrity scenario where prediction is withheld when the signal cannot be trusted.

The `sensor_drift` trap is deliberately included as a non-mechanical-failure condition. `PRESS-03` has sensor drift without a true failure; a good system should resist turning that trap into a mechanical diagnosis.

Training data and model artifacts are generated locally by `scripts/seed.py` and `scripts/train.py`. The serving loader verifies artifact hashes and the feature-version contract before using them.

## Model and feature contract

- Failure risk: HistGradientBoosting classifier with calibrated probabilities and a tuned threshold stored in `models/MANIFEST.json`.
- Fault mode: RandomForest classifier over the configured fault classes.
- RUL: three quantile GradientBoosting regressors used to form a calibrated interval.
- Inputs: 41 physical, windowed features from the versioned `fv1` contract after the deterministic preprocessing pipeline.
- Excluded identity attributes: crew, shift, and operator are not features.
- Numerical source of truth: `core/predict.py` and the verified model artifacts. Coordinator prose cannot override these outputs.

## Current measured results

The generated `eval/REPORT.md` is the authoritative report for the current artifact set. Its current run reports balanced accuracy `0.987`, precision `0.990`, recall `0.979`, per-event detection `0.917` (22/24), downtime-weighted capture `46.72%`, fault-mode top-1 accuracy `0.985`, and calibrated RUL interval coverage `0.928`. These values are evaluation results for this generated split, not a promise of production performance.

The following family table is computed from the current test windows. Event counts are small, so the rows should be read as diagnostic slices rather than independent generalisation studies.

| Machine family | Test windows | Test events | Balanced accuracy | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| CNC | 2,010 | 6 | 0.987 | 0.989 | 0.979 |
| CONV | 1,722 | 5 | 0.991 | 0.988 | 0.992 |
| PRESS | 864 | 1 | 0.989 | 0.989 | 0.979 |
| PUMP | 3,162 | 12 | 0.983 | 0.992 | 0.972 |

The >=85% benchmark claim applies to the measured sklearn evaluation gates only. It does not include the LLM, retrieval prose, the user interface, or a safety guarantee.

## Difficulty controls and known failure modes

The evaluation deliberately keeps difficulty visible:

- Held-out machines `PUMP-07`, `CNC-11`, and `CONV-14` test behaviour on machines not used as ordinary training examples.
- The `sensor_drift` trap tests whether the system can decline a mechanical-fault explanation when a sensor condition is the better explanation.
- `CNC-11` tests conflict handling between sensor evidence and a dominant but mismatched maintenance history.
- `CONV-14` tests data-integrity refusal during an eight-hour connectivity outage; the system withholds a prediction rather than guessing.
- PUMP-07 tests an alert crossing at the seeded replay time and verifies the tuned threshold against the serving artifact.

Known limitations include synthetic distributions, limited event counts per family, dependence on the 41-feature contract, possible novel operating regimes, sensor calibration changes, unseen fault modes, concept drift, and incomplete maintenance history. A high score on the generated split does not establish performance on a different plant or a safety-critical deployment. RUL is an interval estimate with model and data uncertainty; it is not a maintenance deadline.

## Fairness and data-governance notes

The feature contract intentionally excludes crew, shift, and operator identity. The repository's fairness check scans the model feature surface for those tokens and compares seeded decision rates across `CREW-A`, `CREW-B`, and `CREW-C` with a five-percentage-point tolerance. This is a structural and synthetic-data check, not proof of fairness in a real workforce.

Historical maintenance logs can carry recording and intervention bias: a machine that received more inspections may appear better represented than one that was under-maintained. The system should expose evidence coverage and let a human challenge a diagnosis; it must not present historical frequency as causal proof.

## Intended use

Intended uses are local/offline predictive-maintenance rehearsal, operator situational awareness, technician evidence review, planner scheduling support, and evaluation of grounded agent handoffs. It is suitable for demonstrating deterministic prediction, cited narrative, explicit uncertainty, approval boundaries, and degraded operation.

## Out-of-scope use

Do not use this model card or these artifacts to authorize autonomous repair, bypass safety procedures, control a PLC, issue a lockout/tagout clearance, guarantee uptime, certify a machine, or make employment/crew decisions. Do not transfer the generated metrics to another plant without a new data, calibration, fairness, and safety validation programme.

## Change and monitoring requirements

Every retrain must preserve the feature-version and artifact-hash checks, regenerate the evaluation report, rerun the demo-integrity and fairness tests, review per-family metrics and the difficulty controls, and retain the prior model plus evaluation outputs for rollback. Monitor data-quality flags, calibration, class balance, drift, false positives on `sensor_drift`, withheld predictions, evidence coverage, and human escalation outcomes.
