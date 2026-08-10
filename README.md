# SentinelIQ

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

SentinelIQ is an offline-first predictive-maintenance and fault-diagnosis platform for a simulated
15-machine plant. It replays deterministic telemetry, cleans and featurises sensor streams, serves
scikit-learn predictions, retrieves maintenance evidence, and presents grounded diagnoses with citations.
An optional Gemini model can improve the narrative, while every numerical prediction,
guardrail, approval decision, and ERP authorization remains deterministic and auditable.

SentinelIQ is a decision-support demonstration, not a safety controller. Maintenance actions require the
documented human approval or acknowledgement boundary before the system can write to ERP.

## Highlights

- Deterministic failure probability, fault-mode, remaining-useful-life, and fused-risk predictions.
- Grounded diagnoses with evidence citations, facts, assumptions, limits, confidence, and agent traces.
- Strict A0 planner approval for immutable ERP work-order payloads and acknowledgement-only A1 routing.
- Offline operation with no API key or external model, plus both containerized and native workflows.
- Replayable telemetry, downtime ledger, evaluation gates, audit hash chain, and mock ERP integration.
- FastAPI and SQLite backend with a React, TypeScript, Vite, and Three.js interface.

## Docker quickstart

The recommended demo path requires Git, Docker Engine or Docker Desktop, and Docker Compose v2:

```bash
git clone https://github.com/Arv-ind-s/sentineliq.git
cd sentineliq
docker compose up --build
```

The first launch generates the deterministic telemetry, trains and verifies the models, seeds both
SQLite databases, and builds the retrieval index in a persistent Docker volume. When the services are
healthy, open [`http://127.0.0.1:8080`](http://127.0.0.1:8080) and sign in as `planner` with password
`fixture`. OpenAPI is available at
[`http://127.0.0.1:8080/api/docs`](http://127.0.0.1:8080/api/docs).

The application works without an API key. To enable the free Gemini Developer API path, copy the
example environment file and add a key created in
[Google AI Studio](https://aistudio.google.com/app/apikey):

```bash
cp .env.example .env
# Edit GEMINI_API_KEY in .env, then start or recreate the stack.
docker compose up --build
```

Never commit `.env` or put the key in `config.yaml`, an image, or a Compose build argument. Stop the
stack with `docker compose down`; the `sentineliq_state` volume is preserved. The explicit
`docker compose down -v` reset deletes all generated data and operational history.

## Native development

The original Python and Vite workflow remains supported and uses the repository-relative state layout:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python scripts/seed.py
python scripts/run_all.py
```

In a second terminal:

```bash
cd ui
npm ci
npm run dev -- --host 127.0.0.1
```

The native UI is at `http://127.0.0.1:5173`; Vite forwards `/api` to the API at
`http://127.0.0.1:8000`. Existing databases must be upgraded with
`python -m scripts.migrate --backfill-ledger` rather than reseeded.

## Core capabilities

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

The Nginx web service is the single browser-facing origin and forwards `/api` to FastAPI; API and mock
ERP ports remain private to the Compose network. FastAPI owns authentication, orchestration,
persistence, replay, audit, and health reporting. SQLite stores application state, DuckDB reads the
local Parquet time series, and the mock ERP remains an independent service. The agent workflow is
explicit rather than framework-dependent: sensor and maintenance evidence are collected,
deterministic fusion is authoritative, the coordinator narrates only supplied evidence, guardrails
validate the response, and only the ERP action agent can write a work order after approval.

Numerical predictions never depend on the LLM. Narration uses a direct `httpx` call to the native
Gemini Developer API with the free-tier `gemini-2.5-flash-lite` model; no LiteLLM, OpenAI, or LangChain
adapter is involved. When the key is absent, the network is unavailable, or the circuit breaker is
open, the deterministic template is used.

## Five-minute demo

1. Log in as `planner` with the local fixture password `fixture`. The other seeded users are `operator`, `technician`, and `admin`; each has the role matching its username.
2. Open Live Floor and let replay run. The seeded `PUMP-07` bearing-wear scenario crosses the tuned threshold at 52.3 seconds of the 600× replay; the live alert appears at about 56.1 seconds. Open the alert and choose Diagnosis.
3. In Diagnosis, show the agent trace, the deterministic risk/RUL numbers, the Facts / Assumptions / Limits sections, and the `[S#]` / `[M#]` citations. Select a citation to open the corresponding sensor window or maintenance record.
4. For an A0 recommendation, open Schedule & Approvals as `planner`, compare the immutable server payload with the evidence, approve once, and verify the work order in the mock ERP. A1 items are acknowledged by a technician and never write to ERP. Every decision is audited and each A0 payload is idempotent.
5. Open Trust & Evals to show the current generated evaluation report and health dependencies. Open Audit Ledger to verify the hash chain. For the resilience moment, use the Trust screen's offline toggle or remove the network: the amber Offline diagnosis mode and `deterministic-template` stamp are expected.

The planted scenarios and expected outputs are recorded in [`data/DEMO_NOTES.md`](data/DEMO_NOTES.md). The measured evaluation is in [`eval/REPORT.md`](eval/REPORT.md).

For a repeatable rehearsal, run `python scripts/demo_rehearsal.py --interactive` from an authenticated
admin session. It fetches measured diagnosis and ledger values, resets replay to t0, waits for the
PUMP-07 cue, and records the presenter-controlled timings.

## Repository layout

```text
agents/     Agent roles, prompts, orchestration, guardrails, and ERP action boundary
api/        FastAPI routes, authentication, schemas, and HTTP security
core/       Persistence, prediction, preprocessing, retrieval, ledger, clocks, and audit
data/       Deterministic generator, scenario metadata, and checked-in demo notes
erp/        Independent mock ERP service and client-facing history helpers
eval/       Reproducible evaluation harness, scenarios, and generated reports
llm/        Provider-neutral LLM clients, TLS policy, embeddings, and response cache
scripts/    Seed, migration, replay, backup, evaluation, and startup commands
tests/      Backend, workflow, migration, security, persistence, and KPI tests
ui/         React/TypeScript dashboard and Playwright tests
compose.yaml / Dockerfiles  One-command web, API, initializer, and mock ERP stack
```

## Validation commands

```bash
python -m pytest -q
python scripts/evaluate.py
cd ui
npm run lint
npm run build
cd ..
docker compose config
docker compose build
```

The test suite includes the balanced-accuracy and downtime KPI gates, demo-integrity checks, security checks, and agent/API tests. The evaluation script regenerates `eval/REPORT.md` and `eval/report.json` from the current artifacts. The current run reports token usage; free-tier limits and availability are controlled by Google.

## Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md): installation, Gemini configuration/TLS posture, offline operation, backup, retention, and capacity.
- [`USER_GUIDE.md`](USER_GUIDE.md): operator, technician, and planner workflows, diagnosis interpretation, confidence, escalation, and limitations.
- [`docs/AGENTS.md`](docs/AGENTS.md): agent roles, handoffs, prompts, tools, and escalation boundaries.
- [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md): data, model metrics, difficulty controls, known failures, fairness notes, and intended use.
- [`eval/REPORT.md`](eval/REPORT.md): generated evaluation report.
- [`data/DEMO_NOTES.md`](data/DEMO_NOTES.md): generated planted-scenario notes.
- `/api/docs` in Docker, or `/docs` in native development: generated OpenAPI documentation.

## License

SentinelIQ is available under the [MIT License](LICENSE).
