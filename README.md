# SentinelPdM

SentinelPdM is an offline-first predictive-maintenance demonstration for a 15-machine plant. It replays deterministic sensor data, cleans and featurises the stream, serves scikit-learn predictions, retrieves maintenance evidence from a local index, and presents grounded diagnoses with citations. The system can use the OpenAI-compatible LLM gateway for narrative prose, but it remains usable with no API key and no network connection.

## Quickstart

The canonical four-stage rehearsal path is the following. The first line creates and activates a user-space virtual environment; the final line starts the two backend processes.

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/seed.py
python scripts/run_all.py
```

On Windows PowerShell, the equivalent activation/copy commands are:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python scripts/seed.py
python scripts/run_all.py
```

`scripts/seed.py` generates the deterministic plant data, trains and verifies the model artifacts, backfills label-free time-indexed predictions, builds the local retrieval index, and seeds the users plus a real PUMP-07 approval. It is idempotent. Existing databases must first be upgraded with `python -m scripts.migrate --backfill-ledger`; the command creates and verifies a backup before changing the database. `scripts/run_all.py` starts the backend at `http://127.0.0.1:8000` and the mock ERP at `http://127.0.0.1:8001`; stop it with Ctrl-C to terminate both child processes. To run the React UI, open a second terminal after installing Node dependencies:

```bash
cd ui
npm install
npm run dev -- --host 127.0.0.1
```

The API's generated OpenAPI documentation is at [`http://127.0.0.1:8000/docs`](http://127.0.0.1:8000/docs). The UI is normally at `http://127.0.0.1:5173`.

The `LLM_API_KEY` in `.env` is optional. Leave it empty for the deterministic offline-template path. Never commit `.env` or place a gateway key in `config.yaml`.

## What is shipped

- Live Floor and Machine Detail views for all 15 machines, with replayed telemetry and health state.
- Deterministic failure, fault-mode, RUL, and fused-risk predictions from versioned model artifacts.
- One-click Diagnosis with an agent trace, facts, assumptions, limits, evidence citations, confidence, and escalation routing.
- Role-based local authentication for `operator`, `technician`, `planner`, and `admin`.
- Schedule and decision workflow with A0 planner approval before an immutable ERP write and acknowledgement-only A1 technician routing, plus an ERP workspace for live/cached history, inventory, sync state, and idempotent retries by approval ID.
- Downtime Ledger, Trust & Evals, and Audit Ledger screens, plus the digital-twin view used by the current demo build.
- A mock ERP service with read/write scopes, fault injection, retries, circuit breaking, pending-sync handling, and idempotent work-order creation.
- Audit-chain verification, quarantine/release controls, dead-letter handling, and deterministic offline fallback.

## Architecture summary

```text
Parquet replay / SensorSource
          |
preprocess -> 41 versioned features -> sklearn Predictor -> deterministic fusion
          |                                      |
      DuckDB/TSDB                         alert + RUL + fault mode
          |                                      |
maintenance ERP -> redaction -> TF-IDF retrieval -> evidence bundle
                                                     |
             SensorAgent + MaintenanceLogAgent -> CoordinatorAgent
                                                     |
                           GuardrailAgent -> diagnosis -> approval -> ERPActionAgent
```

The Python API is the single browser-facing origin and owns authentication, orchestration, persistence, replay, audit, and health reporting. SQLite stores application state, DuckDB reads the local Parquet time series, and the mock ERP is an independent loopback FastAPI process. The agent workflow is explicit rather than framework-dependent: sensor and maintenance evidence are collected, deterministic fusion is authoritative, the coordinator narrates only supplied evidence, guardrails validate the response, and only the ERP action agent can write a work order after approval.

Numerical predictions never depend on the LLM. The default provider is the configured `langchain` adapter for the LLM gateway, with `httpx` retained as a selectable adapter; when the key is absent, the network is unavailable, or the circuit breaker is open, the deterministic template is used.

## Five-minute demo script

1. Log in as `planner` with the local fixture password `fixture`. The other seeded users are `operator`, `technician`, and `admin`; each has the role matching its username.
2. Open Live Floor and let replay run. The seeded `PUMP-07` bearing-wear scenario crosses the tuned threshold at 52.3 seconds of the 600× replay; the live alert appears at about 56.1 seconds. Open the alert and choose Diagnosis.
3. In Diagnosis, show the agent trace, the deterministic risk/RUL numbers, the Facts / Assumptions / Limits sections, and the `[S#]` / `[M#]` citations. Select a citation to open the corresponding sensor window or maintenance record.
4. For an A0 recommendation, open Schedule & Approvals as `planner`, compare the immutable server payload with the evidence, approve once, and verify the work order in the mock ERP. A1 items are acknowledged by a technician and never write to ERP. Every decision is audited and each A0 payload is idempotent.
5. Open Trust & Evals to show the current generated evaluation report and health dependencies. Open Audit Ledger to verify the hash chain. For the resilience moment, use the Trust screen's offline toggle or remove the network: the amber Offline diagnosis mode and `deterministic-template` stamp are expected.

The planted scenarios and expected outputs are recorded in [`data/DEMO_NOTES.md`](data/DEMO_NOTES.md). The measured evaluation is in [`eval/REPORT.md`](eval/REPORT.md).

For the repeatable judge-facing rehearsal, run `python scripts/demo_rehearsal.py --interactive` from
an authenticated admin session. It fetches measured diagnosis/ledger values, resets replay to t0 before
each take, waits for the PUMP-07 cue, and records the presenter-controlled ≤30s / ≤20s timings.

## Validation commands

```bash
python -m pytest -q
python scripts/evaluate.py
```

The test suite includes the balanced-accuracy and downtime KPI gates, demo-integrity checks, security checks, and agent/API tests. The evaluation script regenerates `eval/REPORT.md` and `eval/report.json` from the current artifacts. The current run is intentionally reported in tokens rather than a fabricated currency cost because the internal gateway does not publish a rate.

## Documentation map

The documentation deliverable set is 7/7: these five authored documents, the generated evaluation report, the generated demo notes, and the generated OpenAPI surface at `/docs`.

- [`DEPLOYMENT.md`](DEPLOYMENT.md): installation, configuration, LLM gateway/TLS posture, offline operation, backup, retention, and capacity.
- [`USER_GUIDE.md`](USER_GUIDE.md): operator, technician, and planner workflows, diagnosis interpretation, confidence, escalation, and limitations.
- [`docs/AGENTS.md`](docs/AGENTS.md): agent roles, handoffs, prompts, tools, and escalation boundaries.
- [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md): data, model metrics, difficulty controls, known failures, fairness notes, and intended use.
- [`eval/REPORT.md`](eval/REPORT.md): generated evaluation report.
- [`data/DEMO_NOTES.md`](data/DEMO_NOTES.md): generated planted-scenario notes.
- `/docs`: generated OpenAPI documentation while the API is running.
