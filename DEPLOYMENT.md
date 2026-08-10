# SentinelIQ deployment guide

SentinelIQ ships as a local portfolio-demo stack with one public web origin, a private FastAPI service,
a private mock ERP, and a one-shot state initializer. Docker Compose is the recommended path; the native
Python and Vite workflow remains supported for development.

## Start the Compose stack

From the repository root:

```bash
docker compose up --build
```

The first run can take several minutes because the initializer generates 60 days of deterministic
telemetry, trains and verifies the scikit-learn artifacts, backfills the ledger, and seeds ERP history.
Compose starts ERP only after initialization succeeds, API only after ERP is healthy, and Nginx only
after the API's core dependencies are ready.

Use these endpoints:

| Endpoint | Purpose |
|---|---|
| `http://127.0.0.1:8080` | React application |
| `http://127.0.0.1:8080/api/docs` | FastAPI OpenAPI documentation |
| `http://127.0.0.1:8080/api/health` | Dependency health report |
| `http://127.0.0.1:8080/healthz` | Nginx health check |

Only Nginx is bound to the host. FastAPI port 8000 and ERP port 8001 are exposed only inside the
Compose network. The default host binding is loopback; set `SENTINEL_BIND_HOST=0.0.0.0` only when the
machine's firewall and network exposure are understood.

## Configuration and Gemini

Copy `.env.example` to `.env` when configuration needs to differ from the local defaults. Docker
Compose reads this file at startup, but `.dockerignore` excludes it from both image build contexts.

| Setting | Default | Purpose |
|---|---|---|
| `SENTINEL_BIND_HOST` | `127.0.0.1` | Host interface used by the web port mapping |
| `SENTINEL_HTTP_PORT` | `8080` | Host port for the complete application |
| `GEMINI_BASE_URL` | Google Gemini Developer API | Native Gemini REST endpoint |
| `GEMINI_API_KEY` | empty | Optional free Google AI Studio key; empty enables deterministic offline mode |
| `ERP_API_KEY` | local demo value | Shared API-to-ERP bearer credential |
| `JWT_SECRET` | local demo value | Session-cookie signing key |

Create a free key in [Google AI Studio](https://aistudio.google.com/app/apikey), place it only in
`GEMINI_API_KEY` inside `.env`, and recreate the API container with `docker compose up -d`. The key is
passed only to the API at runtime; it is not passed to the web build, ERP, initializer, or a Docker build
argument. Leave it empty for fully deterministic offline narration.

The direct `httpx` client calls Gemini's native `generateContent` endpoint. Numerical predictions,
risk fusion, citations, guardrails, authorization, and ERP writes never depend on Gemini. TLS
verification remains enabled by default. If an approved private CA is required, configure the host or
image trust store rather than disabling verification.

The checked-in demo credential defaults are suitable only for the loopback portfolio stack. Set random
`JWT_SECRET` and `ERP_API_KEY` values before any non-loopback exposure.

## Persistent state and initialization

The named volume `sentineliq_state` is mounted at `/state`. It contains:

```text
/state/data/       telemetry, plant database, retrieval index, and LLM cache
/state/models/     verified model artifacts and manifest
/state/erp/        mock ERP database
/state/logs/       structured agent trace log
/state/backups/    operator-created backups
```

Initialization seeds an empty volume in a private staging directory. It validates the signal contract,
artifact hashes, database schema, demo users, ERP history, retrieval index, and demo-integrity tests
before committing a versioned marker. Later starts validate the committed state and do not retrain.
Unmarked, partial, or incompatible state is never overwritten automatically.

`docker compose down` removes containers and preserves the volume. The following command is an explicit,
destructive reset and permanently removes operational history before the next launch reseeds it:

```bash
docker compose down -v
```

## Logs, backup, migration, and recovery

Inspect service logs with:

```bash
docker compose logs -f init erp api web
docker compose ps
```

Create a protected-table backup inside the persistent volume:

```bash
docker compose exec api python scripts/backup.py --output-dir /state/backups
```

If startup reports an outdated plant schema, keep the volume, build the current image, run the explicit
migration with normal services stopped, then adopt the fully validated layout:

```bash
docker compose down
docker compose build api
docker compose run --rm --no-deps api python -m scripts.migrate \
  --database-url sqlite:////state/data/plant.db \
  --backup-dir /state/backups \
  --backfill-ledger
docker compose run --rm --no-deps --user 0:0 init \
  python -m scripts.container_init --adopt-existing
docker compose up -d
```

Migration creates and verifies a backup before changing the schema. Adoption never reseeds: it validates
the database, artifacts, ERP history, telemetry, and retrieval index before writing the state marker.
If validation fails, restore the named backup or inspect the volume; do not use `down -v` when audit or
approval history must be retained.

## Native development

Native execution retains the historical repository-relative layout and requires Python 3.12 plus
Node.js 20:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python scripts/seed.py
python scripts/run_all.py
```

Start the UI separately with `cd ui && npm ci && npm run dev -- --host 127.0.0.1`. The UI uses `/api`
in both modes; Vite removes that prefix and forwards requests to `127.0.0.1:8000`. The native API docs
remain at `http://127.0.0.1:8000/docs`.

Runtime paths default to `data/`, `models/`, `erp/`, and `logs/`. `SENTINEL_STATE_ROOT` moves all mutable
state below one directory; `SENTINEL_DATA_ROOT`, `SENTINEL_MODEL_ROOT`, `ERP_DB_PATH`,
`SENTINEL_INDEX_PATH`, `SENTINEL_LLM_CACHE_PATH`, and `SENTINEL_LOG_PATH` provide narrower overrides.

## Capacity and production boundary

The supported demo envelope is 15 machines, 60 days of telemetry, 60 planted fault events, and 480 ERP
work orders. SQLite uses WAL mode and a single-writer pattern. The Compose stack intentionally has one
API replica and one ERP replica and does not provide TLS termination, a production secret manager,
horizontal scaling, or managed database failover.

For production, place the application behind an authenticated TLS reverse proxy, move mutable state to
managed data services, rotate secrets, add backups and monitoring, and retain the existing ERP client,
`SensorSource`, predictor, TSDB, and vector-store seams.
