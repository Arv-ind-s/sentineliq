# SentinelPdM deployment guide

SentinelPdM is designed for a local, Docker-free demonstration on a developer laptop. It uses a Python virtual environment, SQLite, local Parquet/DuckDB data, two loopback Uvicorn processes, and an optional HTTPS call to the Gemini Developer API. No administrator privileges, system service, container runtime, or database server is required.

## Install and start

From the repository root:

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/seed.py
python scripts/run_all.py
```

PowerShell users can use `Copy-Item .env.example .env` and `.\.venv\Scripts\Activate.ps1` in their equivalent commands. `scripts/run_all.py` starts the API at `127.0.0.1:8000` and the mock ERP at `127.0.0.1:8001`, and shuts down both children on Ctrl-C. Start the UI separately when a browser demo is required:

```bash
cd ui
npm install
npm run dev -- --host 127.0.0.1
```

The API health endpoint is `GET http://127.0.0.1:8000/health`; OpenAPI is at `/docs`. The UI uses relative API paths (for example `/auth`, `/machines`, and `/stream`) through the Vite proxy during development. Keep the API and UI on loopback for the demo.

## Configuration reference

Secrets belong in `.env` or the process environment. They must not be added to `config.yaml`, committed, or copied into logs or screenshots.

| Setting | Current/default value | Purpose |
|---|---|---|
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` | Gemini Developer API base URL. |
| `GEMINI_API_KEY` | empty by default | Optional Google AI Studio API key; empty means deterministic offline mode. |
| `ERP_BASE_URL` | `http://127.0.0.1:8001` | Mock or compatible ERP endpoint. |
| `ERP_API_KEY` | `dev-erp-key` in the local example | Mock ERP bearer credential; replace for a real integration. |
| `JWT_SECRET` | `change-me-for-local-development` in the example | Signs local session cookies; set a random secret outside a throwaway demo. |
| `DB_URL` | `sqlite:///data/plant.db` | Application database. A Postgres adapter can be introduced behind the existing database seam. |
| `SSL_CERT_FILE` | unset | Optional approved private CA bundle used by the HTTPS client. |
| `llm.provider` | `gemini` | Uses the native Gemini `generateContent` REST API through `httpx`. |
| `llm.tls_verify` | `true` | Controls TLS verification for Gemini API calls only. |
| `llm.models.coordinator.name` | `gemini-2.5-flash-lite` | Stable Gemini model with free-tier input and output. |
| `llm.models.log_summary.name` | `gemini-2.5-flash-lite` | Gemini model used for optional maintenance-log summaries. |
| `llm.models.embeddings.enabled` | `false` | Retrieval uses the local TF-IDF `VectorStore`; no remote embedding model is required. |
| `llm.timeout_s` / `max_retries` | `20` / `1` | Gemini request budget and retry count. |
| `llm.circuit_breaker.failure_threshold` / `cooldown_s` | `4` / `60` | Opens the breaker after repeated failures and permits a later probe. |
| `llm.cache.ttl_s` | `900` | Diagnosis/narrative cache lifetime. |
| `stream.speed` / `replay_day` | `600` / `56` | Deterministic replay speed and seeded day. |

The complete validated schema and thresholds are in `config.yaml`. The tuned prediction threshold is an artifact value in `models/MANIFEST.json`, not a configuration magic number.

## Gemini Developer API

Create an API key in [Google AI Studio](https://aistudio.google.com/app/apikey). Do not paste the key
into an issue, source file, terminal transcript, or documentation. Store it only as `GEMINI_API_KEY`
in `.env` or an equivalent secret store.

Google's [Gemini API pricing page](https://ai.google.dev/gemini-api/docs/pricing) states that free-tier
request content may be used to improve its products. Keep this setting
on synthetic/demo data unless your organization's data policy and Google terms allow the submitted
content; redaction is a defense in depth, not permission to send sensitive plant records.

Set the key as follows; the checked-in base URL already points directly to Google's API:

```dotenv
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
GEMINI_API_KEY=<your-google-ai-studio-key>
```

The default model is:

```text
gemini-2.5-flash-lite
```

The client posts directly to `models/{model}:generateContent` with the `x-goog-api-key` header. It
does not require LiteLLM, an OpenAI-compatible proxy, LangChain, or an OpenAI SDK. Test the configured
key without starting the application:

```bash
python scripts/smoke_llm.py
```

To use another Gemini text model, change the relevant model entry in `config.yaml`:

```yaml
name: <gemini-model-id>
```

Retrieval is intentionally local TF-IDF and works without a Gemini embedding model.

### TLS escalation ladder

The demo configuration uses the first rung: `config.yaml` has `llm.tls_verify: true`. An offline/template-backed evaluation confirms the configured TLS posture but does not claim that a live Gemini call was exercised.

Use this escalation ladder when a managed HTTPS proxy is present:

1. Keep `llm.tls_verify: true`. The Gemini client uses the operating system trust store, including approved private roots installed for the current user or machine.
2. If a required private root is not in the operating system trust store, set `SSL_CERT_FILE` to the approved CA bundle and keep verification enabled.
3. Only for a controlled local demonstration behind an explicitly approved TLS-inspection proxy, set `llm.tls_verify: false`. This setting is scoped to the Gemini client only, is WARN-logged at startup, and is displayed in health/Trust diagnostics. ERP and other clients retain hard-coded certificate verification.

The final rung creates residual man-in-the-middle risk. It is bounded for this demo because Gemini receives redacted, evidence-only prompts, has no ERP write tool, and the Guardrail Agent rejects unsupported or unsafe output. It is not an acceptable default for a general production deployment; prefer the approved CA bundle.

## No-key and offline operation

For an air-gapped rehearsal, leave `GEMINI_API_KEY` empty or use the Trust screen's offline toggle. Sensor processing, feature extraction, model predictions, risk fusion, retrieval, ledger values, citations, audit, approvals, and ERP integration do not require Gemini. Narrative calls fail closed to the deterministic template, which is stamped `deterministic-template` and shown with the amber Offline diagnosis mode banner. Gemini is not used to invent a number or authorize a work order.

## Backup, reseed, and rollback

Before reseeding a shared demo or changing artifacts, back up the application database, ERP database, model manifest, evaluation outputs, and audit/approval records. The repository includes:

```bash
python scripts/backup.py
python scripts/seed.py --if-stale
```

`seed.py` regenerates data/model/index state and is idempotent. Approvals and the audit ledger are operational history and are not regenerated safely from a seed; preserve the backup before reseeding. A rollback is a version-controlled checkout plus restoration of the backup, followed by the matching `pip install -r requirements.txt`. Do not use a destructive reset on a database containing evidence needed for an audit.

For an existing Sentinel database, stop API and replay writers and run the versioned upgrade instead of reseeding:

```bash
python -m scripts.migrate --backfill-ledger
```

The command writes a timestamped pre-migration copy under `data/backups`, upgrades the schema in one transaction, rekeys evidence without dropping rows, supersedes unsafe legacy pending approvals, verifies foreign keys and the audit chain, and then scores historical telemetry without fault labels for the downtime ledger. Application startup rejects an unversioned or outdated schema. Keep the generated migration report with the deployment record; rollback means stopping writers and restoring its named backup.

For controlled replay and retention maintenance:

```bash
python scripts/replay.py
python scripts/prune.py --retention-days 30
```

Retention pruning applies to telemetry/operational data according to the script's policy. The audit ledger is not pruned by the normal retention operation. Confirm local policy and export requirements before applying a shorter retention period.

## Capacity and operational limits

The demo dataset contains 15 machines across three lines, 60 days of eight-channel telemetry (about 2.59 million readings), 60 planted fault events, 12 `sensor_drift` intervals, and 480 work orders. SQLite runs in WAL mode with a short busy timeout and a single-writer application pattern; this is appropriate for the local demo and read-heavy inspection, not for many concurrent production writers.

On a normal developer laptop, the seeded dataset and replay are the supported capacity envelope. Larger plants should move telemetry to a managed time-series/columnar store, move application state behind the `DB_URL` seam to Postgres, and place the API behind an authenticated reverse proxy. Keep the ERP client, `SensorSource`, `Predictor`, `TSDBClient`, and `VectorStore` protocol seams when scaling; do not add a second browser-facing origin without revisiting cookie and CORS policy.

Monitor `/health`, `/metrics`, the audit verification endpoint, circuit-breaker state, pending ERP sync rows, and the dead-letter queue. A degraded LLM should be visible and should not be treated as a failed numerical prediction service.
