# tasks.md — **SentinelPdM** Executable Task List

> Decomposition of [spec.md](spec.md) + [plan.md](plan.md). **No re-planning, no re-architecture.**
> Task IDs are inherited verbatim from plan.md §5 (`A-01…A-12`, `B-01…B-14`, `C-01…C-09`, `D-01…D-06`,
> `E-01…E-06`, `F-01…F-03`) so every task traces back to a spec.md §11 line item.
>
> **Owners:** R1 backend/ML · R2 agents/LLM/integration · R3 frontend. Hours are elapsed wall-clock.
> **Every task below is Docker-free, container-free, admin-free:** PyPI wheels into a user-space venv
> (`python -m venv .venv`), npm packages into `ui/node_modules`, SQLite (ships in CPython) + Parquet +
> DuckDB in-process, two loopback uvicorn processes on unprivileged ports, one API-key HTTPS call.

---

## 0. Coverage Deltas (read before starting — 4 task gaps, 5 adversarial-review deltas in §0.2, and the LLM provider change in §0.1; all flagged, none silently absorbed)

plan.md's own six deltas (D1–D6) are already resolved inside its timeline and are carried through
below as owner reassignments. Converting plan.md → tasks surfaced **four additional gaps** where a
mandated artifact has no task. Per the brief's "flag, don't silently change" rule:

| # | Gap | Evidence it is mandatory | Resolution (new task, flagged) |
|---|---|---|---|
| **T1** | `scripts/seed.py` + `scripts/run_all.py` have **no MVP task**. plan.md §5 buries `run_all.py` inside stretch task A-12 and never names `seed.py`. | plan.md §7 DoD-A: *"`python scripts/seed.py` runs clean on a fresh clone in ≤90s"*; §8.1 demo checklist is *4 commands* ending in `python scripts/run_all.py`; spec §10.4. **Without this the demo cannot start.** | **A-13** added, MVP, 0h 20m, R1, placed in R1's H9.0–10.0 float. A-12 stays stretch for `replay/prune/backup`. |
| **T2** | **No login screen task.** C-01…C-09 never build auth UI. | §8.1: *"Login as `planner`"*; spec §7.1 (httpOnly cookie); E-02 builds the backend half only. | **C-10** added, MVP, 0h 15m, R3, placed in R3's H7.0–8.0 polish block. |
| **T3** | **No rollback task** if a stretch feature breaks the core at integration. | plan.md §3.1 cut line + §8.2 *"a cut feature is not mentioned, not visible in the nav"* describe the outcome but assign no owner/time. | **F-04** added, MVP-protective, 0h 10m, contingency-only, drawn from H10.0–11.0 float. |
| **T4** | **No task for the demo-script dry run**, though plan.md §3's H11.0–12.0 R3 row names it and §13.7 sets ≤30s / ≤20s budgets for it. *(This row was itself missing from an earlier version of this table, which claimed "three gaps" while listing four tasks — corrected here.)* | plan.md §3 H11.0–12.0 row; spec §13.7 demo-moment runtime targets. | **F-05** added, MVP, 0h 15m, R3, concurrent with F-02 docs. |

### 0.2 Adversarial-review deltas (D7–D11) — **schedule-changing, not reassignments**

An adversarial pass over spec.md + plan.md + this file found work that existed in no document and
estimates that do not survive contact. Unlike D1–D6, these are **not** resolved by reshuffling owners.
Full statements in plan.md §0.1; the task-level consequences are:

| # | Finding | Task consequence |
|---|---|---|
| **D7** | **`core/predict.py` and `core/tsdb.py` had no task in any document.** `predict.py` is the serving path the ≥85% KPI is measured *through*; `tsdb.py` is the DuckDB seam under every windowed read. Both were assumed to fall out of `train.py` or the Sensor Agent. | **B-15** added, MVP, 0h 30m, R2, H2.6→H3.1, **critical path** — it sits between B-05 and A-04. |
| **D8** | **A-03's demo verification was unsatisfiable in build order** — it asserted PUMP-07 crosses τ, but τ doesn't exist until A-04 and `core/` doesn't exist at H2.5. The Risk-1 horizon fallback re-tunes τ and **moves the moment PUMP-07 fires**, with no owner for re-verification. | A-03 keeps model-free `verify_signal()`. **A-15** added, MVP, 0h 15m, R1, H3.4→H3.65, joins the ⛳H3.5 gate, **re-run after every retrain**. |
| **D9** | **A-07 (0:50) and C-08 (1:00) were the two worst estimates in the plan.** | **A-07 split** into A-07a (0:50, gate-blocking) + **A-07b** (0:40, not gate-blocking). **C-08 re-estimated to 1:30.** |
| **D10** | **The session cookie could never reach the API in a browser** — `SameSite=Strict` across `localhost:5173` → `127.0.0.1:8000` is cross-*site*. Login would 401 silently and SSE would fail the same way, with 0:15 budgeted at H7.0 to discover it. | Design fix, **+0:00**: the Vite dev proxy moves into **C-01**, and the origin rule is **frozen in A-02**. |
| **D11** | **KPI gate #2 had no contingency at all**, and is **algebraically entailed** by KPI #1 (spec §3.12). | **+0:00**: plan.md Risk **P3** adds a 20-minute lever ladder; **A-09** discloses the entailment in `eval/REPORT.md`. |

**Net +1:55 of MVP work.** The honest consequence, recorded in plan.md §3.2: the buffer is **0:40, not
2:30**, and the stretch cut moves from H10.0 to **⛳H8.5** with only the top three stretch items
attempted by default. Nothing was quietly absorbed to protect a schedule number.

### 0.1 LLM provider change — OpenAI-compatible LLM gateway (applied to spec.md, plan.md and this file)

The LLM layer uses a configurable OpenAI-compatible gateway. **No task was added, removed, re-owned or re-timed** —
this is a substitution inside `B-03` plus consequential wording. What changed and why:

| # | Change | Affects |
|---|---|---|
| **L1** | **Gateway** is `https://llm-gateway.example.com`; **model** is `openai-compatible-chat` for both the coordinator and log-summary roles. Both are config values; zero literals in agent code. | A-01, B-03, B-09, B-10 |
| **L2** | **Client library**: `langchain_openai.ChatOpenAI` is the default adapter for the standard OpenAI-compatible client contract. It sits behind an `LLMClient` protocol with the original plain-`httpx` adapter retained and selectable via `llm.provider`, so **no agent is coupled to an SDK** and spec §10.3's anti-lock-in claim still holds. | A-01 (2 new wheels), B-03 |
| **L3** | **TLS**: `verify` is controlled by `llm.tls_verify` (default `true`) with a fixed escalation ladder — `verify=true` → `SSL_CERT_FILE` → `tls_verify: false` **for the gateway host only during a controlled local demonstration**, WARN-logged every startup and shown on `/health` and the Trust screen. The ERP and all other clients keep `verify=True` **hard-coded**. | B-03, A-10, D-02, F-02 |
| **L4** | **Embeddings are out of MVP scope.** The gateway is confirmed for chat only; no embeddings model name is known for `llm-gateway.example.com`. TF-IDF is promoted from "offline fallback" to **the shipped retrieval path** (`llm.models.embeddings.enabled: false`). At 480 work orders this costs little ranking quality and guarantees offline operation. **Do not spend build time hunting for an embeddings model.** | B-07, B-03 |
| **L5** | **JSON output is not assumed well-formed.** A configured provider may not support OpenAI `json_schema` strict mode or pass through `response_format`. `json_mode: "auto"` tries it once and drops it on a 4xx; `parse_json()` tolerantly strips code fences and prose. The Guardrail Agent's strict Pydantic validation remains the real contract. | B-03, B-09, B-10 |
| **L6** | **Cost is reported in tokens, not £.** No billed rate is configured, so the old `≤£0.004/diagnosis` target became `≤3,500 tokens/diagnosis`. `price_per_1k` defaults to `0.0` and any £ shown is labelled *indicative*. | B-14, D-02, A-10, §6 metrics |
| **L7** | **New pre-decided risks** in plan.md §6: **3b** model name unroutable (10-min box: swap to another model the gateway lists, else template path) and **3c** prose-wrapped JSON (already covered by L5 — no new work). | plan.md §6 |

**Unchanged by all of the above:** every task ID, owner, duration and dependency; the H1.4 gate; the
deterministic-template path; both KPI gates. **The ≥85% accuracy KPI does not involve the LLM at all** —
sklearn produces every number, the configured model only narrates it — so the provider change cannot move it.

**One honest schedule warning:** R1's H9.0–10.0 hour now holds A-10 (0:20) + A-11 (0:20) + A-13 (0:20)
= **1:00 with zero float**, but plan.md §3 designates that hour as "floating support: fix whatever
H6.5–H8.5 surfaced". **Pre-decided:** if bug triage is needed, drop **A-10 `/metrics`** first — plan.md
§7 DoD-A explicitly lists `/metrics` dashboards as *"Not required"*. Do not drop A-13 (demo won't start)
or A-11 (resilience story).

**And a second, larger one, now that D7–D9 are counted:** the plan's "2:30 of buffer" never existed
alongside 3:40 of scheduled stretch. The real figure is **0:40**. Every task below still fits its
plan.md hour allocation, but only at zero slippage across 47 tasks — so the stretch queue in §4 is
**opt-in from ⛳H8.5**, not opt-out at H10.0. Ranks 4–8 are cut by default.

---

## 1. Build Order

Legend: 🔒 critical path · ⛳ checkpoint · ⭐ standout/stretch.

---

### Hour 0.0–1.0 — Foundation 🔒
*Gate: contract frozen; `GET /machines` returns fixture JSON; `ui` shell renders against it. R3 unblocked for 11 hours.*

---

### [A-01] Repo scaffold, venv, config system
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** None
- **Estimated time:** 0h 25m
- **Description:** Create the repository skeleton from spec §2.3, a user-space Python 3.12 venv, `requirements.txt`, the committed `config.yaml` (spec Appendix A verbatim), `.env.example`, and `core/config.py` as the single source of truth for every threshold and weight.
- **Implementation notes:** `python -m venv .venv` (no admin). Pin wheels: FastAPI 0.115, uvicorn 0.32, SQLAlchemy 2.0.36, Pydantic 2.9, pydantic-settings, DuckDB 1.1.3, PyArrow 17, NumPy 2.1, pandas 2.2.3, scikit-learn 1.5.2, httpx 0.27, **langchain-openai 0.2 + openai 1.x (the OpenAI-compatible gateway path — pure-Python wheels, no compiler)**, tenacity 9.0, argon2-cffi 23.1, PyJWT 2.9, python-dotenv 1.0.1, structlog 24.4, pytest 8.3. **Resolve `langchain-openai` against Pydantic 2.9 during this task, not at H5** — if it forces a Pydantic bump that breaks FastAPI, drop to `llm.provider: httpx` immediately and note it; do not spend the hour. `.env.example` ships `LLM_BASE_URL=https://llm-gateway.example.com` pre-filled with `LLM_API_KEY=` deliberately blank. `core/config.py` = Pydantic Settings: loads `config.yaml`, overlays `.env`, **fails fast** with a named message on a missing/malformed value, and **raises on startup if any secret-shaped key appears in `config.yaml`** (spec §7.3). Directories per spec §2.3: `core/ agents/ llm/ erp/ api/ data/ eval/ tests/ scripts/ ui/ docs/ models/ logs/`. `.gitignore` covers `.env`, `data/*.parquet`, `*.db`, `logs/`, `models/`, `node_modules/`.
- **Acceptance criteria:**
  - `pip install -r requirements.txt` completes in a fresh venv with **no compiler and no admin prompt**.
  - `python -c "from core.config import settings; print(settings.predict.threshold_tau_default)"` prints `0.42` — and **`threshold_tau` itself is absent from `config.yaml`**, because the tuned value is a model artifact written to `models/MANIFEST.json` (spec §3.5). Training must never mutate a committed file.
  - `python -c "from core.config import settings; print(settings.llm.base_url, settings.llm.models.coordinator.name)"` prints `https://llm-gateway.example.com openai-compatible-chat`.
  - `import langchain_openai` succeeds in the fresh venv **and** `import fastapi, pydantic` still work (no resolver conflict).
  - Deleting a required key from `config.yaml` produces a one-line named error, not a traceback.
  - Adding `jwt_secret: abc` to `config.yaml` makes startup fail with "secret in config.yaml".
  - `config.yaml` contains every constant from spec Appendix A; **zero magic numbers** land in later modules.
- **Demo relevance:** No (enables everything).

### [A-02] 🔒 Freeze the API contract
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-01
- **Estimated time:** 0h 25m
- **Description:** Write `api/schemas.py` (Pydantic v2, `extra="forbid"`) covering every response body in spec Appendix B, and stub **all** Appendix-B routes returning realistic fixture JSON. Do this **with R3 in the room** — this contract is frozen for the remaining 11 hours.
- **Implementation notes:** Routes: `/auth/login|logout`, `/health`, `/metrics`, `/machines`, `/machines/{id}`, `/machines/{id}/telemetry`, `/machines/{id}/prediction`, `POST /machines/{id}/diagnose`, `/diagnoses/{id}`, `/runs/{trace_id}`, `/alerts`, `PATCH /alerts/{id}`, `/approvals`, `/approvals/{id}/approve|reject`, `/schedule`, `/ingest/batch`, `/ingest/dlq`, `/ledger`, `/eval/report`, `/audit`, `/audit/verify`, `/rehearsal/*`, `/stream/events`, `/meta`. Error envelope `{"error":{"code","message","trace_id"}}` defined here. Export TS types to `ui/src/types/api.ts` (hand-written mirror is fine — spec §4.8: types mirror the backend exactly).
- **Also frozen here — the browser origin rule *(D10)*:** the UI **never** uses an absolute API URL. All fetches and the `EventSource` use **relative paths**, and `vite.config.ts` proxies the Appendix-B prefixes to `http://127.0.0.1:8000` (C-01). This is not style: `SameSite=Strict` treats `localhost:5173` → `127.0.0.1:8000` as **cross-site**, so an absolute URL means the session cookie is never sent, every authenticated call 401s, and SSE fails identically — at H7.0, with 15 minutes budgeted to find it. Freezing it here costs nothing; discovering it later costs the demo's opening.
- **Acceptance criteria:**
  - `uvicorn api.main:app` serves `/docs` listing **all 25+ Appendix-B routes**.
  - `GET /machines` returns 200 with 15 fixture machines validating against `MachineSummary[]`.
  - Every stub response validates against its Pydantic model (assert in `tests/test_contract.py`).
  - `ui/src/types/api.ts` compiles and matches field-for-field; R3 signs off verbally.
  - Contract is **frozen** — after this task, changes require both R1 and R3 to agree.
  - **`grep -rn "127\.0\.0\.1\|localhost:8000" ui/src` returns nothing**, now and at every later checkpoint.
- **Demo relevance:** No (but Risk R4's single highest-leverage 25 minutes).

### [B-01] Mock ERP service skeleton on :8001
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-01
- **Estimated time:** 0h 30m
- **Description:** Stand up the mock ERP as a **separate FastAPI app, separate process, separate API key, separate SQLite file** (spec §3.10) with all 6 endpoints on fixture data, plus the `ERP_FAULT_MODE` chaos switch.
- **Implementation notes:** `erp/app.py`, own `erp/erp.db`. Endpoints: `GET /erp/v1/assets/{asset_id}`, `GET /erp/v1/workorders?asset_id&since&limit`, `POST /erp/v1/workorders` (**requires `Idempotency-Key` header**), `PATCH /erp/v1/workorders/{id}`, `GET /erp/v1/parts?fault_code`, `GET /erp/v1/health`. Bearer auth against `ERP_API_KEY` with scopes `workorder:read|write`. `ERP_FAULT_MODE=none|slow|flaky|down` → 3s sleep / 30% 503 / hard connection refusal. Bind `127.0.0.1:8001`.
- **Acceptance criteria:**
  - `python scripts/run_erp.py` serves `GET /erp/v1/health` → 200 on `127.0.0.1:8001`.
  - Request without the bearer token → 401; wrong scope → 403.
  - `POST /erp/v1/workorders` twice with the same `Idempotency-Key` creates **one** row and returns the same `workorder_id`.
  - `ERP_FAULT_MODE=flaky` produces 503s on ~30% of requests over 50 calls.
  - `ERP_FAULT_MODE=down` refuses the connection (not a 500).
- **Demo relevance:** Yes — closer #1 (work order lands in ERP) and closer #2 (chaos toggle).

### [B-02] 🔒 SQLite engine + 13-table ORM schema
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-01
- **Estimated time:** 0h 25m
- **Description:** `core/db.py` (SQLite WAL engine, lazy singleton session factory) and `core/models.py` — the **13 tables of spec Appendix C**, SQLAlchemy 2.0 declarative, dialect-neutral types only. *(plan.md delta D6: unbudgeted in spec §11, assigned to R2's free 0:30 in Phase 0.)*
- **Implementation notes:** `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, 32MB page cache — set via `PRAGMA` in a `connect` event listener. Tables: `machines, fault_events, work_orders, features, predictions, evidence, diagnoses, agent_runs, alerts, approvals, audit_ledger, ingest_dlq, users`. No SQLite-specific column types (spec §8: `DB_URL` → Postgres is a one-line env change). `alerts.id` is a **stable content-derived hash**, not autoincrement.
- **Acceptance criteria:**
  - `Base.metadata.create_all()` creates exactly **13 tables**; asserted in `tests/test_schema.py`.
  - `PRAGMA journal_mode` returns `wal`; `PRAGMA busy_timeout` returns `5000`.
  - Two concurrent writers 5s apart do not raise `database is locked` (test with threads).
  - Every column from spec Appendix C's table descriptions exists with a sane type.
  - No `sqlite3`-only types (`JSON` via `sqlalchemy.JSON`, timestamps as int64 epoch-ms).
- **Demo relevance:** No.

### [C-01] 🔒 Vite + React 19 + TS + Tailwind 4 scaffold and shell
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** A-02
- **Estimated time:** 0h 35m
- **Description:** Scaffold `ui/`, define the dark control-room theme tokens, build the layout shell (nav + health strip slot + content outlet), wire the TanStack Query client and react-router routes for all 6 screens, and create skeleton primitives.
- **Implementation notes:** `npm create vite@latest ui -- --template react-ts` (writes only to `ui/`, no admin). Add `@tailwindcss/vite` (Tailwind 4 — no config file), TanStack Query 5, react-router-dom 7, Recharts 2.13, framer-motion 11.11, lucide-react. **`vite.config.ts` `server.proxy` maps every Appendix-B prefix — `/auth /health /metrics /machines /diagnoses /runs /alerts /approvals /schedule /ingest /ledger /eval /audit /rehearsal /stream /meta /docs /openapi.json` — to `http://127.0.0.1:8000`, with `changeOrigin: false` so the cookie stays first-party** *(D10)*. `/stream` additionally needs buffering disabled so SSE is not held by the proxy. The browser therefore only ever talks to `localhost:5173`: the `SameSite=Strict` cookie works, CORS is never exercised, and **dev matches prod**, where FastAPI `StaticFiles` serves the UI same-origin anyway. `ui/src/styles/theme.css` per spec §4.2: surfaces `#0B0F14 / #131A22 / #1C2632 / #25313F`, status ramp `LOW #22C55E · MEDIUM #EAB308 · HIGH #F97316 · CRITICAL #EF4444 · NODATA #64748B`, accent `#38BDF8`, Inter + JetBrains Mono (tabular figures), 8px grid, 4px radius, 44px min touch target. Routes: `/floor /machines/:id /diagnosis/:id /schedule /ledger /trust` (+ `/login` from C-10, `/rehearsal` behind a flag). Feature flags read from `GET /meta`.
- **Acceptance criteria:**
  - `npm run dev` serves `http://localhost:5173` with **zero** install requiring admin rights.
  - **`fetch('/machines')` from the browser reaches the backend through the proxy and `document.cookie` set by `/auth/login` is sent on the next request** — verified in devtools' Network tab, not assumed. This is the D10 check and it happens in hour 0, not hour 7.
  - **`curl -N http://localhost:5173/stream/events` streams** (proxy does not buffer SSE).
  - All 6 routes render a titled shell with nav highlighting; no route 404s.
  - `Skeleton`, `Panel`, `StatusPill`, `MonoNumber` primitives exist and are used by the shell.
  - Every status colour is accompanied by an **icon shape + text label** in `StatusPill` (never colour alone).
  - Layout is correct at **1280×800**; text contrast ≥ 4.5:1 (spot-check 5 samples).
  - TanStack Query devtools show the client mounted; `GET /machines` fixture data renders in a list.
- **Demo relevance:** Yes — every judge-facing screen sits on this.

---

### Hour 1.0–2.0 — Data generation begins, LLM client, Live Floor 🔒
*Gate: **LLM gateway (`https://llm-gateway.example.com`) reachable with `openai-compatible-chat`, or the offline template path confirmed and announced** — decided at H1.4 and not revisited; 1-machine sample Parquet exists by H1.3.*

---

### [A-03] 🔒 Synthetic plant generator with defects injected inline
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** B-02
- **Estimated time:** 1h 30m *(runs H1.0 → H2.5)*
- **Description:** `data/generate.py` — a `SEED=42` deterministic generator producing 15 machines / 3 lines, 7,200 production cycles, 60 days × 8 channels @ 30s (2.59M readings), 6 signatures, **60 labelled fault events + 12 `sensor_drift` intervals**, all **6 deliberate data defects injected inline**, and the 4 planted demo scenarios positioned relative to **`t0` = day 56**, with a **model-free** verifier that raises rather than shipping a broken demo. *(plan.md delta D1: spec tasks 1.1 + 1.2 merged — they are the same file. Deltas D8 + the §3.1 event-count change apply here.)*
- **Two changes from the original scope, both flagged:**
  - **34 → 60 failure events** *(spec §3.1)*. At 34, the held-out split (last 20% of days **and** 3 unseen machines) contains only 7–12 events, so one missed event moves recall ~10 points and the 6-class confusion matrix has empty rows — the ≥85% gate would turn on seed luck. At 60 the held-out set holds ~20 events with ≥3 per mode. The data is synthetic; statistical power is free.
  - **`verify_planted_scenarios()` → `verify_signal()`** *(D8)*. The original verifier asserted PUMP-07 "crosses the alert threshold", which is **impossible to check here**: τ does not exist until A-04 (H3.2) and `core/preprocess|features|predict` do not exist at H2.5. This task keeps only the model-free half; the threshold-crossing assertion moves to **A-15**.
- **Implementation notes:** `numpy.default_rng(42)`. Channels: `vibration_rms_mm_s, vibration_kurtosis, temp_bearing_c, temp_motor_c, pressure_bar, current_a, rpm, ambient_temp_c`. Fault physics in **one dict `FAULT_SIGNATURES`** (spec §3.1 table): `bearing_wear, rotor_imbalance, seal_leak, motor_overheat, lubrication_degradation, sensor_drift`. Defects: ±90s per-source clock skew · Gaussian noise + **0.4% impulse spikes (10–40σ)** · **1.1% dropouts in bursts of 1–20** · 0.6% near-duplicate ERP rows · **2 machines report bearing temp in °F** · one machine's pressure channel frozen for 6h. **Replay origin:** `stream.replay_start_day = 56.0` is `t0`. Everything before it is history (training, 6h feature windows, 90/180d ERP lookbacks, startup cache warm); the demo runs forward from it at 600×, so 10 minutes of demo ≈ 4.2 days of plant time. Planted scenarios are positioned **relative to `t0`**: **PUMP-07** bearing_wear onset at `t0 + ~7.5h` so the alert fires **45–60s into the demo**; **PRESS-03** sensor_drift false-positive trap; **CNC-11** seal_leak with conflicting history; **CONV-14** 8h gateway outage. Write `data/sensors/machine_id=*/date=*.parquet` (PyArrow), machines + fault_events into `data/plant.db`. **Emit a 1-machine sample partition by H1.3** so R2 (B-04) is unblocked while the full run continues. `verify_signal()` checks **model-free** properties only and **raises `DemoIntegrityError`**; writes `data/DEMO_NOTES.md`.
- **Acceptance criteria:**
  - Two consecutive runs produce **byte-identical** Parquet checksums.
  - Row count ≥ 2.55M across `data/sensors/`; `fault_events` has exactly **60** rows with **≥10 of every failure mode**, plus **12** labelled `sensor_drift` intervals carrying **no** failure event.
  - All 6 defects are individually detectable by a test (`tests/test_generator.py`): skew present, spike rate ∈ [0.3%, 0.5%], dropout rate ∈ [1.0%, 1.2%], ≥1 duplicate pair, 2 machines °F-scaled, one 6h frozen window.
  - **`verify_signal()` asserts model-free facts and raises on any of them:** PUMP-07's onset is at `t0 + 7.5h ± 15min` and its vibration RMS ramps ≥4× baseline before failure; PRESS-03 has a drift signature and **no** failure event; CNC-11's planted mode ≠ its dominant historical fault code; CONV-14 has a contiguous 8h gap. **It does not mention τ** — that is A-15's job.
  - A 1-machine sample partition exists on disk **by H1.3**.
  - `data/DEMO_NOTES.md` documents expected output for all 4 planted scenarios (A-15 later appends the **measured** crossing time).
- **Demo relevance:** Yes — **PUMP-07 is demo moment #1**; PRESS-03 is the honesty closer.

### [B-03] 🔒 OpenAI-compatible LLM gateway client with fail-closed contract
- **Workstream:** AI Integration
- **Tag:** MVP
- **Depends on:** A-01
- **Estimated time:** 0h 25m
- **Description:** `llm/client.py` — the `LLMClient` protocol plus a **`LangChainLLMClient` adapter reproducing the OpenAI-compatible access pattern** against `https://llm-gateway.example.com` with `openai-compatible-chat`, wrapped in timeout/retry/circuit-breaker; `llm/cache.py` content-hash cache; `llm/embeddings.py` with TF-IDF as the default path; token accounting — plus a **gateway smoke test run now** so the offline decision is made at H1.4, not at H11.
- **Implementation notes:** The reference pattern this task must reproduce (provider-supplied, treat as the known-good handshake — do **not** re-derive the URL path or auth header form):
  ```python
  from langchain_openai import ChatOpenAI
  import httpx
  llm = ChatOpenAI(
      base_url="https://llm-gateway.example.com",
      model="openai-compatible-chat",
      api_key=os.environ["LLM_API_KEY"],
      http_client=httpx.Client(verify=settings.llm.tls_verify),
  )
  ```
  Wrap it, do not expose it: every caller sees `LLMClient.chat(messages, model_cfg) -> LLMResponse{text, prompt_tokens, completion_tokens, model, latency_ms, cached}`. `llm.provider` selects `langchain` (default) or `httpx`; the httpx adapter POSTs to `${LLM_BASE_URL}${llm.chat_path}` and is **optional for MVP** — build it only if the LangChain import misbehaves, it exists so no agent is coupled to a vendor SDK. **Never hard-code `verify=False`** — it is `settings.llm.tls_verify`, default `true`, and when false the client logs `WARN tls_verify=false host=llm-gateway.example.com` on construction (spec §7.5 escalation ladder: `verify=true` → `SSL_CERT_FILE` → `tls_verify: false` for this host only; the ERP client keeps `verify=True` hard-coded). `timeout_s: 20`, **`max_retries: 1`** (tenacity, exponential + jitter) — **2 attempts × 20s = 40s ≤ the 45s per-run timeout**; a third attempt could not complete inside the run budget, so the retry ceiling is *derived* from the run timeout rather than chosen independently (spec §3.8.6). Circuit breaker `{failures: 4, cooldown_s: 60}`. `max_tokens` is **450 (coordinator) / 350 (log summary)** — sized to the JSON schema, because output length is the primary latency lever (spec §6.2). **`_call_json()` returns `None` on ANY exception** — network, auth, timeout, TLS, malformed JSON, unsupported gateway parameter, **or `langchain_openai` failing to import** (spec §3.11 fail-closed contract). `parse_json()` is **tolerant**: strips ```` ```json ```` fences and any preamble/epilogue prose, extracts the first balanced JSON object — the configured model is not guaranteed to honour `response_format` through a provider passthrough, so `json_mode: "auto"` tries it once and permanently drops it for the session on a 4xx naming that parameter. Cache key `sha256(prompt_version + model + evidence_bundle)` in SQLite, TTL 900s. **Report tokens, not £** — `price_per_1k` defaults to 0.0 and any £ shown is labelled *indicative*; the gateway is internal infrastructure with no billed rate exposed to us.
- **The smoke test also *times* the gateway — this is the P2 decision point, moved from H9.0 to H1.4.** The two LLM calls are **60% of the ≤6s diagnosis budget** and are the only part of it we do not control. This task requests **one 300-token completion, records observed output tokens/s into `eval/latency_baseline.json`**, and applies the spec §6.2 table immediately: **≥150 tok/s** → proceed, two LLM calls. **60–150** → set `llm.log_summary_call_enabled: false`; **<60** → one call, `coordinator.max_tokens` → 300, and diagnosis becomes on-demand only. The alert path is ≤250ms and never waits on the LLM.
- **Acceptance criteria:**
  - **Smoke test hits `https://llm-gateway.example.com` with `openai-compatible-chat` and returns a completion, or cleanly logs the reason (`tls` / `auth` / `model_not_found` / `unreachable`) and the build continues on templates.** The reason is announced to the team at H1.4 — this is the gate.
  - **`eval/latency_baseline.json` exists** with observed output tok/s, first-token latency, and the §6.2 branch taken; the branch is announced with the reachability result and **not revisited**.
  - If the branch sets `log_summary_call_enabled: false`, `B-10` is verified to still return every non-`notes` field deterministically **before H5.0**, not discovered later.
  - Model name, base URL and TLS setting are read from config; provider-specific literals do not appear in agent code.
  - With `LLM_API_KEY` unset, `_call_json()` returns `None` in <10ms and **never raises**.
  - With `langchain_openai` uninstalled, the client falls back to the httpx adapter or returns `None` — it **never** raises `ImportError` into a request path.
  - `tls_verify: false` emits exactly one WARN per client construction and sets `tls_verified: false` on `GET /health`'s LLM dependency.
  - `parse_json()` recovers a valid object from: a fenced ```` ```json ```` block, a response with a "Here is the diagnosis:" preamble, and a trailing-prose response. Unrecoverable input returns `None`, not an exception.
  - Forced 5 consecutive failures → breaker opens; subsequent calls short-circuit without a network attempt for 60s.
  - Identical evidence bundle twice → second call served from cache in **≤10ms** with `cached: true`.
  - Every call appends prompt/completion tokens + model name + provider to the run record.
  - `tests/test_llm_client.py` covers: timeout, 500, malformed JSON, fenced JSON, missing key, missing SDK, `response_format` 4xx — all handled, none raising.
- **Demo relevance:** Yes — this is the only component that touches the LLM gateway, and the offline fallback banner is a talking point, not an apology.

### [B-04] 🔒 Preprocessing — noise cleaning + timestamp alignment
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-03 *(1-machine sample partition, H1.3)*
- **Estimated time:** 0h 25m
- **Description:** `core/preprocess.py` implementing spec §3.3's 8-step pipeline as pure functions: skew→UTC, dedupe, unit harmonisation, 60s resample, Hampel despike, gap flagging, frozen-sensor detection, and a `QualityReport`. This **is** the brief's "clean sensor noise and align timestamps" requirement. *(plan.md delta D1: reassigned R1 → R2.)*
- **Implementation notes:** Single public entry `preprocess_window(df, cfg) -> (df, QualityReport)`. Thresholds from `config.yaml` `preprocess:` block only. Hampel = rolling median ± 3·MAD over 11 samples; outliers replaced by rolling median and **flagged, never silently overwritten**. Gaps ≤5min → linear interpolate + flag `interpolated`; >5min → null + flag `missing`. Completeness <80% → `quality=INSUFFICIENT`. Zero variance ≥30min → `quality=SUSPECT`, channel excluded. Range clamps kill `1e308`/NaN propagation. **Every step is total — returns data+flags, never raises.**
- **Acceptance criteria:**
  - ERP naïve local timestamps and UTC sensor timestamps land on the **same 60s grid** (unit test with a known ±90s offset).
  - The two °F machines emerge in °C (assert 20–90°C range, not 70–190).
  - Injected 30σ spike is replaced by the rolling median and `spikes_removed` increments.
  - A 6h frozen pressure channel yields `quality=SUSPECT` with `pressure_bar` in `suspect_channels[]`.
  - CONV-14's 8h gap window yields `quality=INSUFFICIENT` and `completeness < 0.80`.
  - `preprocess_window` on a 24h × 8-channel window (2,880 rows) completes in **< 25ms**.
  - Feeding all-NaN input returns a report, does not raise.
- **Demo relevance:** Yes — the quality-flag ribbon on Machine Detail renders these flags.

### [C-02] Live Floor screen (on stubs)
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** C-01
- **Estimated time:** 1h 20m *(runs H1.0 → H2.4)*
- **Description:** Build the plant-floor screen — 15 machine tiles with live 60-point vibration sparklines, conic-gradient risk rings, status by colour **and** icon **and** label, a live alert feed, the plant KPI header, and the replay-speed control. Builds entirely against A-02 stubs.
- **Implementation notes:** Sparkline = lightweight SVG polyline (not Recharts — 15 of them shift every tick). Risk ring = CSS `conic-gradient` with a CSS custom property for the sweep. Alert feed has `aria-live="polite"`. KPI header: machines at risk · predicted failures in 7d · downtime avoided this month · £ saved. Replay-speed control posts to the stream speed param. Data via TanStack Query with `placeholderData` to kill flicker. **Zero blank states** — skeleton for every panel.
- **Acceptance criteria:**
  - 15 tiles render from `GET /machines` fixture data at 1280×800 with no horizontal scroll.
  - Each sparkline holds exactly 60 points and shifts left on new data without layout shift.
  - Risk ring sweep matches `risk/100`; verified at 0, 42, 87.
  - Every tile shows status as colour **+ icon shape + text label**; passes a grayscale screenshot check.
  - Alert feed announces new items to a screen reader (`aria-live` present, verified in devtools).
  - KPI header shows 4 numbers in JetBrains Mono tabular figures — no jitter as values change.
  - Every panel has a skeleton; **no blank state anywhere** on cold load.
- **Demo relevance:** **Yes — demo moment #1 stage.** This is the first thing a judge sees.

---

### Hour 2.0–3.0 — Training data, features, ERP history, Machine Detail 🔒
*Gate: `fv1` feature contract stamped; training data ready.*

---

### [A-04] 🔒 Model training pipeline
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** B-15
- **Estimated time:** 0h 35m *(H3.1 → H3.7; the ⛳H3.5 gate items A-05/A-15 follow immediately)*
- **Description:** `scripts/train.py` — label 6h windows against a 24h failure horizon, train the three model families, calibrate probabilities, tune τ, and write a hash-stamped manifest. Idempotent, prints a full metrics table.
- **Implementation notes:** `HistGradientBoostingClassifier` (failure, handles NaN natively) · `RandomForestClassifier(n_estimators=300)` (6-class fault mode) · `GradientBoostingRegressor(loss="quantile")` ×3 at q=0.1/0.5/0.9 (RUL interval) · `CalibratedClassifierCV(method="isotonic", cv=3)`. Label: `y=1` if a labelled failure occurs in `(t, t+24h]`; **exclude windows inside an active repair**. Split **by time AND by machine** — last 20% of days held out **plus 3 machines never seen in training**. τ tuned on validation to maximise F1 subject to **precision ≥ 0.80**, then written into **`models/MANIFEST.json` — never back into `config.yaml`** *(spec §3.5: training must not mutate a committed file)*. `MANIFEST.json`: **tuned τ**, feature version, training rows, metrics, timestamp, seed, **sha256 per artifact**. Global `permutation_importance` computed once at train time. The **fault-mode classifier is 5-class** over the failure modes; `sensor_drift` is trained from its 12 labelled drift intervals and reported separately as a decline-rate, because it never produces a failure event and would otherwise be a class with no positive labels.
- **Acceptance criteria:**
  - `python scripts/train.py` completes on CPU in **≤ 90s** and is re-runnable without manual cleanup.
  - Prints a metrics table: **per-event detection `k/n`**, balanced accuracy, precision, recall, F1, PR-AUC, plus the 5-class confusion matrix and the `sensor_drift` decline-rate.
  - Held-out split provably excludes 3 machine IDs (asserted in the script, printed by name) and contains **≥15 failure events with ≥3 per mode** — printed, so a thin test set is visible rather than assumed.
  - `models/MANIFEST.json` exists with `sha256` for every `.joblib`, `feature_version: "fv1"`, **and the tuned `threshold_tau`**.
  - Tuned τ satisfies **precision ≥ 0.80** on validation. **`git status` is clean after training** — no committed file is modified.
  - RUL quantile models emit p10 ≤ p50 ≤ p90 on 100% of held-out windows.
- **Demo relevance:** Yes — the honesty closer ("3 machines never seen in training").

### [B-05] 🔒 Feature engineering with a versioned contract
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** B-04
- **Estimated time:** 0h 20m *(→ H2.6)*
- **Description:** `core/features.py` — 41 features over a 6h rolling window with 1h stride, `FEATURE_VERSION = "fv1"` asserted at model load, a feature cache table, and unit tests covering both preprocess (B-04) and features.
- **Implementation notes:** Per channel (8×4): mean, std, min-max range, linear-trend slope. Vibration: RMS, kurtosis, crest factor, peak-to-peak, band-energy ratio via `numpy.fft` (**no scipy dependency**). Cross-channel: `temp_bearing − temp_ambient`, `current / rpm`, vibration-vs-load residual. Context: hours since last WO, WOs in 90d, machine age days, duty-cycle %, cycles since last PM. Quality: completeness, interpolated %, suspect-channel count. Cache in the `features` table keyed `(machine_id, window_end, fv)`.
- **Acceptance criteria:**
  - `extract(window) -> np.ndarray` returns **exactly 41 values** in a stable, documented order.
  - `FEATURE_VERSION == "fv1"` is written into `MANIFEST.json` and **a mismatch at load raises rather than serving** (test asserts the refusal).
  - Feature names list has 41 unique entries, each with a docstring naming its physical meaning.
  - Cache hit on the same `(machine, window, fv)` returns in **< 2ms**.
  - `tests/test_preprocess.py` + `tests/test_features.py` green — ≥10 assertions combined.
- **Demo relevance:** No (but the version-mismatch refusal is a resilience talking point).

### [B-15] 🔒 Serving path — `core/predict.py` + `core/tsdb.py` *(gap D7 — see §0.2)*
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** B-05
- **Estimated time:** 0h 30m *(H2.6 → H3.1)*
- **Description:** The two modules **no document previously assigned to anyone**. `core/predict.py` is the *serving* path — the `Predictor` protocol, the sole loader of `.joblib` artifacts, sha256 + feature-version verification, τ from `MANIFEST.json`, and batch scoring. `core/tsdb.py` is the `TSDBClient` protocol over DuckDB that serves every windowed Parquet read. **The ≥85% KPI is measured through `predict.py`; the Sensor Agent cannot read a window without `tsdb.py`.** Both were silently assumed to fall out of A-04 (`scripts/train.py`, which only *writes* artifacts) or B-11 (the Sensor Agent, which only *calls* them).
- **Implementation notes:** `Predictor` protocol: `score(features: np.ndarray, machine_meta) -> PredictionResult{p_failure_24h, rul_p10/p50/p90, mode_probs, top_features, predictor, model_version}`. Load once, lazily, at startup; **verify sha256 of every artifact against `models/MANIFEST.json` and assert `FEATURE_VERSION == "fv1"` before serving a single prediction** — a mismatch raises and A-11's rule-based fallback takes over. **τ is read from `MANIFEST.json`, never from `config.yaml`** (spec §3.5): training must not mutate a committed file, or a fresh clone is git-dirty after `seed.py` and the fusion/guardrail tests silently depend on whoever trained last. `config.predict.threshold_tau_default` is used **only** by the rule fallback. `core/tsdb.py`: `read_window(machine_id, t0, t1, channels) -> pd.DataFrame` with DuckDB **bound parameters** (no SQL string building, spec §7.4), column + predicate pushdown, and a connection singleton. Swapping in Timescale/Influx is a new class implementing the same protocol.
- **Acceptance criteria:**
  - `Predictor` scores a single 41-feature window in **< 2ms** and all 15 machines in one vectorised call in **< 200ms** warm.
  - Corrupting one byte of a `.joblib` → load **refuses** with a named hash-mismatch error; setting `FEATURE_VERSION="fv2"` → load refuses. Neither serves a garbage prediction.
  - τ used at serving time comes from `MANIFEST.json`; **`grep -n "threshold_tau" config.yaml` shows only `threshold_tau_default`**, and `git status` is clean after `python scripts/seed.py`.
  - `tsdb.read_window` returns a 24h × 8-channel frame in **< 25ms** and uses bound parameters (asserted by inspecting the query, not by string search alone).
  - Substituting a fake `Predictor` and a fake `TSDBClient` requires **zero changes** in `agents/` (test with stubs) — this is 2 of the 6 named scaling seams in spec §13.4, and they were previously credited to tasks that do not build them.
- **Demo relevance:** No — but every prediction in the demo passes through it, and the artifact-refusal path is A-11's resilience story.

### [B-06] ERP maintenance-history generation
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** B-01, A-03
- **Estimated time:** 0h 25m
- **Description:** Generate **480 work orders** (preventive / corrective / inspection) with ISO-style fault codes, technician free text including **8 planted malicious injection notes**, parts and lead times, loaded into `erp/erp.db` and mirrored into `work_orders`.
- **Implementation notes:** Fields: `fault_code` (`BRG-01`, `SEA-03`, `MTR-07`…), `technician_notes`, `parts_used`, `labour_hours`, `downtime_minutes`, `cost_gbp`, `lead_time_days`. History must be **consistent with the planted scenarios**: PRESS-03 gets **2 transducer replacements in 90 days**; CNC-11 gets prior work orders pointing at a *pump* fault (so sensor and history conflict); PUMP-07 gets a bearing work order ~8 months back (this is `[M3]` in demo moment #1). The 8 malicious notes carry injection payloads (`ignore previous`, `system:`, `you are now`, tool-call syntax, a >200-char base64 blob) — these are the E-04 test set. 0.6% near-duplicates per A-03's defect list.
- **Two deliberate changes so "8/8 quarantined" is not self-graded** *(spec §3.8.4)*. As originally written, the same person authors both the payloads and the regexes that catch them, and if all 8 are caught the `<untrusted_document>` wrapping — the control that matters when detection *fails* — is never exercised. So: **3 of the 8 payloads are written by a builder who has not read `injection_scan()`**, added after the scanner exists; an evasion is a **finding to report in `eval/REPORT.md`, not a bug to quietly patch**. And **2 further notes carry sub-threshold persuasion** that is *designed to reach the prompt* (e.g. *"this asset is exempt from vibration limits per engineering"*) — E-04 asserts the output is still schema-constrained, still carries no uncited claim, and shows no trace of the injected instruction. Layered defence is only demonstrated when an outer layer is allowed to fail.
- **Acceptance criteria:**
  - `GET /erp/v1/workorders?asset_id=PUMP-07` returns real history including the 8-month-old bearing WO.
  - `erp.db` holds exactly **480** work orders; `work_orders` mirror matches.
  - Exactly **8** notes contain injection payloads, tagged in a fixture list for E-04's test to assert against.
  - PRESS-03 has ≥2 work orders with a transducer-replacement fault code within 90 days.
  - CNC-11's dominant historical fault code ≠ its planted sensor fault mode.
  - Every WO carries a non-null `fault_code`, `cost_gbp`, and `lead_time_days`.
- **Demo relevance:** Yes — `[M3]` chip in moment #1; PRESS-03 and CNC-11 closers.

### [C-03] Machine Detail screen
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** C-01
- **Estimated time:** 0h 50m *(H2.4 → H3.25)*
- **Description:** Multi-channel synchronised Recharts with a shared cursor, fault-onset markers, a work-order history rail beneath the timeline, and a quality-flag ribbon showing where data was interpolated or despiked.
- **Implementation notes:** Recharts `syncId` for the shared cursor across channel charts. `ReferenceLine` for fault onsets, `ReferenceArea` for quality bands (this same mechanism is what C-04's citation scrubbing reuses — build it reusable). Downsample to ≤600 points client-side (LTTB) for smoothness. Quality ribbon = a thin band above the x-axis colouring `interpolated` / `despiked` / `missing` segments distinctly. Work-order rail = horizontal cards aligned to the timeline. Every chart gets a **"View as table" toggle** (spec §4.2 accessibility).
- **Acceptance criteria:**
  - Hovering any chart moves the cursor on **all** channel charts at the same timestamp.
  - Fault-onset markers appear at the ground-truth `onset_ts` for a machine with a labelled event.
  - Quality ribbon visibly differentiates interpolated vs despiked vs missing on CONV-14.
  - Work-order rail renders ≥1 card, aligned to its date on the shared time axis.
  - "View as table" toggle renders an accessible `<table>` of the same series.
  - 24h view of 8 channels renders in **< 500ms** with no layout shift.
- **Demo relevance:** Yes — the scrub target for demo moment #1's `[S2]` chip.

---

### Hour 3.0–4.0 — ⛳ H3.5 MODEL GATE, fusion, retrieval, agent base 🔒
*⛳ **H3.5 CHECKPOINT — MODEL GATE.** Models trained, accuracy ≥85% green, Live Floor + Machine Detail rendering. **If accuracy <85%: extend horizon 24h→48h in `config.yaml` and/or add lag features before anything else** (Risk 1). The KPI is not negotiable; the horizon is.*

---

### [A-05] 🔒 ⛳ KPI GATE #1 — accuracy ≥ 85%
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP
- **Depends on:** A-04
- **Estimated time:** 0h 15m *(→ H3.4)*
- **Description:** `tests/test_kpis.py` — **two** assertions over the held-out split (unseen days **and** 3 unseen machines): **per-event detection ≥ 85%** (the headline) and **balanced accuracy ≥ 0.85**. The KPI becomes executable here, not asserted in a doc.
- **Implementation notes:** Re-score the held-out split; do **not** trust `MANIFEST.json` metrics (a manifest goes stale). **Per-event detection** = for each held-out failure event, did any window inside `onset → failure` cross τ? Reported as `k/n` with the lead-time distribution. It is the headline because balanced accuracy is computed over ~24 *correlated* windows per event and therefore carries far more implied precision than `n` supports; per-event degrades honestly at small `n` and is what a plant actually buys. Both are reported, **each printed with its `n`**. Also assert precision ≥ 0.80 and recall ≥ 0.80 (spec §13.1). Report the **horizon next to every number** so a 48h fallback is never silently hidden.
- **Acceptance criteria:**
  - `pytest tests/test_kpis.py -k accuracy` **passes** and completes in < 30s.
  - Both numbers are asserted, and both failure messages name the value, the `n`, **and** the configured horizon.
  - The test re-scores the held-out split rather than trusting a cached metric.
  - Deliberately corrupting a model artifact makes the test fail (not error).
- **Demo relevance:** Yes — "Rigour" closer: `pytest` run live in front of judges.

### [A-15] 🔒 ⛳ Demo-integrity test — the planted scenarios, verified against the *trained model* *(gap D8 — see §0.2)*
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP
- **Depends on:** A-05, A-06
- **Estimated time:** 0h 15m *(H3.4 → H3.65, part of the ⛳H3.5 gate)*
- **Description:** `tests/test_demo_integrity.py` — the half of demo verification that **cannot** live in A-03. It replays each planted scenario from `t0` through the real pipeline with the **trained model and tuned τ** and asserts the behaviour the demo script promises.
- **Why this task exists:** A-03 was originally asked to assert that "PUMP-07 crosses the alert threshold ~50s into replay." That is **unsatisfiable at H2.5**: τ is produced by A-04 at H3.7, and `core/preprocess|features|predict` do not exist when the generator runs. Worse, **Risk 1's pre-agreed fallback (horizon 24h → 48h) re-tunes τ and therefore moves the moment PUMP-07 fires** — and no task owned re-verification. The single most important 50 seconds of the build was unowned from H3.5 onward.
- **Implementation notes:** Four assertions, one per planted scenario, each running the **real** `tsdb → preprocess → features → predict → fusion` path (no fixtures, no mocks): **PUMP-07** — replaying from `t0` at `speedup: 600`, the fused risk crosses τ between **40s and 75s** of wall-clock equivalent (`stream.demo_alert_window_s`); a window, not a point, because τ is model-dependent. **PRESS-03** — `sensor_drift_suspected` fires and the recommendation class is `verify_instrumentation`, **not** a mechanical repair. **CNC-11** — fusion returns `agreement: conflict` and confidence ×0.6. **CONV-14** — `quality=INSUFFICIENT` → `NO_PREDICTION` + a **data**-kind alert. On success, append the **measured** crossing time to `data/DEMO_NOTES.md` so the presenter's cue is the real number.
- **Acceptance criteria:**
  - All four assertions pass with the current model and τ; failure messages name the scenario and the observed value.
  - **Shifting τ by ±0.05 and re-running makes the PUMP-07 assertion fail** — proving the test is actually coupled to the model, not to a constant.
  - The test is **listed in `README.md` as mandatory after any retrain**, and the H3.5 checkpoint is not green without it.
  - `data/DEMO_NOTES.md` carries the measured crossing time, not the designed one.
  - Runs in **< 20s** so re-running it after a retrain is never a reason to skip it.
- **Demo relevance:** **Yes — it is the only thing standing between "PUMP-07 fires on cue" and finding out live.**

### [A-06] 🔒 Deterministic evidence fusion
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-05
- **Estimated time:** 0h 40m
- **Description:** `core/fusion.py` — the risk formula, bands, agreement, confidence, the **5 hard rules**, the A3→A0 autonomy ladder, and `explain()` returning a term-by-term breakdown. **This module is why the LLM cannot change a decision, only describe it.**
- **Implementation notes:** `risk = clamp(0..100, 55·p_failure_24h + 20·rul_pressure + 10·history_prior + 8·criticality_weight + 7·(1−data_quality))`; every weight from `config.yaml` `fusion.weights`. Bands: CRITICAL ≥75 · HIGH ≥55 · MEDIUM ≥35 · LOW <35. `agreement ∈ {agree, partial, conflict}`. `confidence = calibrated_prob_margin × data_quality × agreement_factor × evidence_count_factor`. **Hard rules returned verbatim, never folded silently in:** `insufficient_data` (no prediction + data-fault alert), `safety_critical_mode` (band floored CRITICAL, autonomy forced A0), `repeat_failure` (+12 risk, root-cause escalation required), `evidence_conflict` (confidence ×0.6, disagreement must be stated), `sensor_drift_suspected` (action class → `verify_instrumentation`). Autonomy ladder per spec §3.8.6 table (A0 also on cost > £5,000). **Total function** — always returns a decision, possibly `NO_PREDICTION`.
- **Acceptance criteria:**
  - `tests/test_fusion.py` covers **all 5 hard rules** firing independently, ≥12 assertions.
  - `motor_overheat` on a `critical` machine → band `CRITICAL`, autonomy `A0`, regardless of risk score.
  - completeness 0.62 → `NO_PREDICTION` + a **data**-kind alert (not a machine alert).
  - `explain()` returns 5 named terms whose sum equals the reported risk (± 0.01).
  - `evidence_conflict` on CNC-11 multiplies confidence by 0.6 and sets a `must_state_disagreement` flag.
  - Zero magic numbers — every constant resolves through `config.yaml`.
- **Demo relevance:** Yes — the risk waterfall on the Diagnosis screen.

### [B-07] Maintenance-log retrieval (TF-IDF first)
- **Workstream:** AI Integration
- **Tag:** MVP
- **Depends on:** B-06
- **Estimated time:** 0h 35m *(→ H3.5)*
- **Description:** `core/retrieval.py` — hybrid retrieval over work orders with **TF-IDF as the plan of record, not a fallback**, a `NumpyVectorStore` behind a `VectorStore` protocol, and SQL aggregation for MTBF / repeat failures / cost, returning ≤6 records tagged `[M1]…[M6]`.
- **Implementation notes:** `TfidfVectorizer(ngram_range=(1,2), max_features=4000)` is **the shipped path**. The LLM gateway is confirmed for the configured model **chat** only — **no embeddings model name is confirmed on `llm-gateway.example.com`**, so `llm.models.embeddings.enabled` ships `false` and dense embeddings are out of MVP scope. **Do not spend build time hunting for an embeddings model.** If one is confirmed later it drops in behind the same protocol with zero caller changes. At 480 work orders this costs almost nothing in ranking quality and buys guaranteed offline operation. `NumpyVectorStore` = exact cosine over a float32 matrix persisted to `data/index/logs.npz`; protocol is `add / search / persist / load` (spec §10.3 swap point). **Hybrid candidate set** = union of (top-8 vector-similar) ∪ (all WOs for this machine in 180d) ∪ (fleet WOs sharing the predicted fault code), re-ranked `0.5·similarity + 0.3·recency_decay(90d half-life) + 0.2·same_machine_model`. Structured aggregation is **SQL, not vector search** (these are facts): MTBF per machine and per fault code, repeat failure (same code ≤90d apart), mean repair cost/downtime, part lead times.
- **Acceptance criteria:**
  - Search returns **≤6** records with stable `[M1]…[M6]` tags, deterministic for identical input.
  - Retrieval over 480 work orders completes in **≤ 5ms** (timed assertion).
  - Works with **no network and no API key** (TF-IDF path) — test runs with `LLM_API_KEY` unset and makes **zero** calls to `llm-gateway.example.com`.
  - PUMP-07 bearing query surfaces the 8-month-old bearing WO in the top 3.
  - `aggregate_history("PRESS-03")` reports the 2 transducer replacements within 90d.
  - Swapping `NumpyVectorStore` for a stub implementing the protocol requires **zero caller changes** (test with a fake).
- **Demo relevance:** Yes — `[M#]` chips resolve to these records.

### [B-08] 🔒 Agent protocol and trace persistence
- **Workstream:** AI Integration
- **Tag:** MVP
- **Depends on:** B-02
- **Estimated time:** 0h 25m
- **Description:** `agents/base.py` — the `Agent` protocol, the `AgentResult` dataclass, shared retry/backoff, trace emission, and persistence of every agent call to `agent_runs`. This is the traceability the evaluation criteria demand and the data source for the UI's agent-trace timeline.
- **Implementation notes:** `AgentResult = {ok, output, evidence[], confidence, tokens, latency_ms, errors[], retries, fallback_used}`. Every run persists `trace_id, agent, version, input_hash, output, latency_ms, prompt_tokens, completion_tokens, cost_gbp (indicative), model_name, provider, retries, fallback_used, errors` — `model_name` + `provider` are recorded per run so a diagnosis is always attributable to the exact model that produced it, and so a mid-build model swap is visible in the trace rather than silent. One `trace_id` per diagnosis run threaded through every agent and every log line (structlog bound context). Retry decorator via tenacity with per-agent config.
- **Acceptance criteria:**
  - A fake agent run writes exactly one `agent_runs` row with all 14 fields non-null (`errors` may be `[]`; `model_name`/`provider` read `n/a` for the deterministic agents).
  - `GET /runs/{trace_id}` (stubbed here, real in A-07) returns the rows for that trace in order.
  - Retry decorator produces `retries: 2` in the result after 2 transient failures.
  - `grep '"trace_id":"<id>"' logs/app.jsonl` reconstructs the run in order.
  - An agent raising an unexpected exception still produces `ok=False` + a persisted row (never a lost run).
- **Demo relevance:** Yes — powers the agent-trace timeline in moment #1.

### [C-04] Diagnosis screen
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** C-01, C-03
- **Estimated time:** 1h 10m *(H3.5 → H4.7)*
- **Description:** The Coordinator's output rendered: fault mode + confidence gauge, reasoning steps, **Facts / Assumptions / Limits as three visually distinct blocks**, recommended actions with parts and labour, the `fusion.explain()` risk waterfall, and interactive `[S#]`/`[M#]` citation chips that **scrub and shade the sensor chart** or pop the work-order card.
- **Implementation notes:** Citation chips reuse C-03's `ReferenceArea` mechanism — hovering `[S2]` sets a shaded band on the sensor chart at that evidence item's `(channel, t0, t1)` and scrolls it into view; hovering `[M3]` pops a work-order card (date, fault code, note, cost). Facts / Assumptions / Limits must be **visually distinct** (different surface tint + icon + heading) — this is the brief's "separate facts from assumptions" criterion made visible. **Every block of AI prose sits on a tinted surface with a "Generated" chip**; template output shows `deterministic-template` instead. Risk waterfall from `explain()`'s 5 terms. Confidence gauge is a needle, number snaps (never animates a number the user must read now).
- **Acceptance criteria:**
  - Hovering `[S2]` shades the **exact** `(t0, t1)` window on the sensor chart below, within 100ms.
  - Hovering `[M3]` pops a work-order card with date, fault code, technician note, cost.
  - Facts, Assumptions and Limits render as three visually distinguishable blocks; `limits` is never empty.
  - Risk waterfall shows 5 named terms summing to the displayed risk.
  - AI prose carries a "Generated" chip; when `generated:false` it reads `deterministic-template`.
  - Confidence gauge value matches the API to 2 dp; the number does not animate.
  - **Clicks from alert → full cited diagnosis = 1** (verified by walking the flow).
- **Demo relevance:** **Yes — the payload of demo moment #1.**

---

### Hour 4.0–5.0 — Guardrails first, specialist agents, SVG schematic 🔒
*Gate: guardrail tests exist **before** the Coordinator does.*

---

### [E-01] 🔒 Guardrails + Guardrail Agent (tests written first)
- **Workstream:** Security
- **Tag:** MVP
- **Depends on:** A-06
- **Estimated time:** 0h 40m
- **Description:** `core/guardrails.py` + `agents/guardrail_agent.py` — **deterministic code, not an LLM** (a guard you can't unit-test isn't a guard). Enforces numeric grounding, citation resolution, strict schema, an unsafe-action denylist, and assumption discipline, logging every rejection. *(plan.md delta D2: reassigned R2 → R1. **Write `tests/test_guardrails.py` FIRST.**)*
- **Implementation notes:** (1) **Numeric grounding, over a *normalised* bundle** — extract every number from the draft **with its adjacent unit**, canonicalise, and allow only values present in the normalised evidence bundle (± **2% relative** tolerance), plus small integers 0–24 and 4-digit years; an ungrounded number ≥25 → reject. **`normalise_numeric()` is not optional polish — without it the guard rejects the system's own correct output.** The demo's own alert line, *"4.2 days RUL, 87% confidence"*, contains `87`, which is ≥25 and absent from a bundle storing `confidence: 0.87`. Every bundle quantity is therefore emitted in every representation the prose may legitimately use: probability `0.87` ⇄ `87` ⇄ `87%`; duration `4.2 d` ⇄ `101 h` ⇄ `6048 min`; temperature `78.4 °C` ⇄ `173.1 °F`; cost `£4,200` ⇄ `4.2k`; percentage points `62.5` ⇄ `0.625`. (2) **Citation validity** — every `[S#]`/`[M#]` resolves to a real evidence row; every `recommended_action` carries ≥1 citation; unresolvable → reject. (3) **Schema** — strict Pydantic v2, 1–4 actions, non-empty `limits`. (4) **Unsafe-action denylist** — bypass safety interlock · disable an alarm · override a pressure relief setting · continue operating with a predicted overheat · defer maintenance on a CRITICAL asset → reject **and force A0**. (5) **Injection detection** hook (implementation in E-04). (6) **Assumption discipline** — uncited statements in `facts[]` are **moved** to `assumptions[]`, not rejected. Every rejection logged with rule, offending content, outcome.
- **Acceptance criteria:**
  - `tests/test_guardrails.py` is committed **before** `coordinator_agent.py` exists — ≥18 assertions, one per rule branch.
  - **The demo's own strings ship as fixtures asserted to PASS**: the alert line *"bearing degradation predicted, 4.2 days RUL, 87% confidence"* and a full sample diagnosis both clear the guard against a bundle holding `0.87` and `4.2`. *(A guard that rejects the demo is not a guard — it is a silent switch to the template path wearing a rejection log, and it would have looked like the system working.)*
  - Unit-pair round-trips are covered: `87%`/`0.87`, `101 h`/`4.2 d`, `173.1 °F`/`78.4 °C`, `4.2k`/`£4,200` — each grounds, and each **fails** when moved outside the 2% band.
  - A draft containing "vibration reached 12.7 mm/s" with no such number in the bundle is **rejected** with rule `numeric_grounding`.
  - `[S9]` where only S1–S5 exist → rejected with rule `citation_validity`.
  - "Bypass the interlock to keep running" → rejected **and** autonomy forced to `A0`.
  - An uncited sentence in `facts[]` appears in `assumptions[]` after the pass (moved, not dropped).
  - A valid draft passes all 6 checks and is returned unchanged.
  - Every rejection writes a row queryable for the Trust screen; **≥1 real rejection exists to show a judge**.
- **Demo relevance:** Yes — guardrail rejection log is a demoable feature, not invisible plumbing.

### [B-11] 🔒 Sensor Agent (100% deterministic)
- **Workstream:** AI Integration
- **Tag:** MVP
- **Depends on:** B-08, A-04
- **Estimated time:** 0h 25m
- **Description:** `agents/sensor_agent.py` — owns the sensor pipeline for one machine-window end to end. **No LLM, by design.** Emits predictions plus `[S1]…[S5]` evidence bound to concrete `(channel, t_start, t_end, stat, value)` tuples.
- **Implementation notes:** Tools: `read_window` (DuckDB) → `preprocess_window` → `extract_features` → `predict` → `detect_anomaly_windows`. Output `{p_failure_24h, rul_p10/p50/p90, mode_probs{}, top_features[], anomaly_windows[], quality}`. Evidence rows **persisted before any prompt is built** (spec §6.1 CP6). Retry: 3 attempts at 0.5/1/2s on transient read failure, then `ok=False, reason="sensor_unavailable"` — the orchestrator continues with history-only evidence. `quality=INSUFFICIENT` → **refuses to predict** and raises a data-integrity alert routed to the maintenance-systems owner, not the technician.
- **Acceptance criteria:**
  - Agent makes **zero** LLM calls (assert `llm.client` call count == 0 in its test).
  - Emits 3–5 `[S#]` evidence items, each resolving to a real `(channel, t0, t1, stat, value)` row in `evidence`.
  - CONV-14's gap window → `ok=True` with `refused=true`, a data-integrity alert, and **no** prediction row.
  - Simulated DuckDB failure → 3 retries then `ok=False, reason="sensor_unavailable"` without raising.
  - End-to-end latency for one machine-window **< 400ms** warm.
- **Demo relevance:** Yes — "Sensor 340ms" node in the agent trace.

### [B-10] 🔒 Maintenance-Log Agent
- **Workstream:** AI Integration
- **Tag:** MVP
- **Depends on:** B-08, B-07, B-01
- **Estimated time:** 0h 40m
- **Description:** `agents/maintenance_log_agent.py` — fetches history over **live HTTP** to the mock ERP, runs retrieval, makes exactly **one** LLM call producing schema-validated JSON, and degrades through retry → circuit breaker → local cache.
- **Implementation notes:** Tools: `erp_search_workorders` (live HTTP, 5s timeout) · `vector_search` · `aggregate_history` (MTBF, repeat failures, cost/downtime per fault code) · `parts_lookup(fault_code)`. LLM output schema: `{recurring_modes[], effective_repairs[], ineffective_repairs[], parts_needed[], typical_downtime_min, notes}` — **every field traceable to an `[M#]` tag**, schema-validated before leaving the agent, parsed through the same tolerant `parse_json()` as B-09. **This is the call to sacrifice first** if diagnosis p95 exceeds 6 s (spec §12 Risk P2): a deterministic SQL aggregation already produces every field except `notes`, so dropping the call costs prose, not facts — worth pre-testing that switch given the configured model's variable latency behind a shared gateway. Retry 3× exponential + jitter on 5xx/timeout, then **circuit-open 60s** and return cached history from the local `work_orders` mirror stamped `source: "cache"`. Zero history → fleet priors **explicitly labelled `assumption`**. On LLM failure → deterministic aggregation, never an error.
- **Acceptance criteria:**
  - Exactly **1** LLM call per run (asserted); output validates against the schema or falls back deterministically.
  - `ERP_FAULT_MODE=flaky` → run still completes, result stamped `source: "cache"` when the breaker opens.
  - `ERP_FAULT_MODE=down` → run completes from cache in **< 2s** total, no exception escapes.
  - Every returned claim carries an `[M#]` tag resolving to a real work order.
  - A machine with zero history returns fleet priors with `kind: "assumption"` on every item.
  - With `LLM_API_KEY` unset, the agent returns a deterministic structured summary.
- **Demo relevance:** Yes — "Log 1.2s" node; supplies `[M3]` for moment #1.

### [C-05] 2D SVG machine schematic
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** C-03
- **Estimated time:** 0h 25m *(H4.7 → H5.1)*
- **Description:** A hand-drawn 2D SVG machine schematic with colour-coded component hotspots rendered from the `components[]` payload; clicking a component filters the sensor chart to that component's channels. **This is the 3D fallback — built first, ships regardless of whether D-05 happens.** *(plan.md delta D6: unbudgeted in spec §11, placed in R3's Phase-2 slack.)*
- **Implementation notes:** Inline SVG, no asset files. Components: bearing, shaft, seal, motor housing, impeller — each an SVG path with a `data-component` attribute, tinted by its contributing risk using the status ramp. **Renders from the identical `components[]` data the 3D twin would use — no duplicated logic** (spec §4.5). Lives in the Machine Detail right-hand panel, the exact slot D-05 would occupy.
- **Acceptance criteria:**
  - Schematic renders 5 named components from `GET /machines/{id}` `components[]`.
  - Each component's fill matches its risk band colour; the predicted fault component is visually distinguished.
  - Clicking a component filters the sensor charts to that component's channels.
  - Component labels are text, not colour-only.
  - Occupies the Machine Detail right panel and requires **zero changes** if `ui.features.digital_twin` later flips to `true`.
- **Demo relevance:** Yes — ships in Machine Detail whether or not the 3D twin survives.

---

### Hour 5.0–6.0 — Real routes, SSE, Coordinator, orchestrator 🔒
*Gate: stubs are gone; the UI is on real endpoints.*

---

### [A-07a] 🔒 Ingest, StreamClock, alert generation, SSE, gate routes
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-06, B-02, B-15
- **Estimated time:** 0h 50m *(H5.05 → H5.95)*
- **Split from a single 0:50 "A-07" *(gap D9 — see §0.2)*.** The original task was the entire ingest stack **plus ~25 real routes plus SSE plus alert generation** in fifty minutes. That was not an estimate. It is now two tasks, and **only this one blocks the ⛳H6.5 gate** — because the gate tests *stream → prediction → alert → diagnosis → dashboard*, not the whole of Appendix B. **Alert generation is named explicitly here** because it appeared in no task before: thresholding predictions into alerts, the stable content-derived `alerts.id`, dedupe, and the alert state machine were all implicitly inside a task that had no room for them.
- **Description:** `core/ingest.py` (`SensorSource` protocol / `ParquetReplaySource` / `StreamClock` at 600× **from `t0` = day 56** / bounded queues / range validation / dead-letter queue), **alert generation**, the SSE endpoint `GET /stream/events`, and real implementations of the routes the H6.5 gate exercises (`/machines`, `/machines/{id}`, `/machines/{id}/telemetry`, `/machines/{id}/prediction`, `POST /machines/{id}/diagnose`, `/diagnoses/{id}`, `/runs/{trace_id}`, `/alerts`, `/meta`).
- **Implementation notes:** `asyncio.Queue(maxsize=2000)` per machine for **backpressure, not unbounded growth**. Every batch passes schema + physical-range validation (`0 ≤ vibration ≤ 200`, `−40 ≤ temp ≤ 300`); rejects → `ingest_dlq` **with a reason — nothing silently dropped**. Idempotent on `(machine_id, ts)`. `POST /ingest/batch` uses the same validation path so a real PLC gateway can post today. **SSE, not WebSockets** (spec §3.9): three event types `telemetry | alert | run_state`, `Last-Event-ID` support. **Alert generation**: a prediction crossing τ emits an alert whose `id` is a **content hash of `(machine_id, kind, fault_mode, onset_bucket)`** — stable across ticks so a degrading machine produces *one* alert that updates, not one per tick; `kind ∈ {machine, data}` so CONV-14's data-integrity alert never looks like a mechanical fault. Startup verifies `data/plant.db` + `MANIFEST.json` (through `B-15`), warms the prediction cache for all 15 machines **from data before `t0`**. Per-machine source failure is isolated — one dead stream never stops the others.
- **Acceptance criteria:**
  - `GET /stream/events` pushes `telemetry` events to a connected browser at 600× replay; `curl -N` shows a live stream.
  - Killing one machine's source leaves the other 14 streaming (test with a raising fake source).
  - An out-of-range row (`vibration=1e308`) lands in `ingest_dlq` with a reason and the batch **continues**.
  - Replaying the same batch twice creates no duplicate rows, **and produces no duplicate alerts** (same content hash).
  - A machine degrading across 10 consecutive ticks yields **one** alert row that updates, not 10.
  - The gate routes above return real data; `tests/test_contract.py` (from A-02) still passes unchanged.
  - `GET /runs/{trace_id}` returns prompts, raw responses, guardrail verdicts and timings for a real run.
  - Alert-only path (no LLM) measured at **≤ 250ms** p95.
- **Demo relevance:** Yes — **PUMP-07 crossing the threshold live on screen is this task.**

### [A-07b] Remaining Appendix-B routes *(split from A-07, gap D9)*
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-07a
- **Estimated time:** 0h 40m *(H5.95 → H6.6 — **not gate-blocking**, may land just after ⛳H6.5)*
- **Description:** Replace the remaining A-02 stubs with real implementations: `PATCH /alerts/{id}`, `/approvals*` (shell — B-13 fills the behaviour), `/schedule`, `/ingest/batch`, `/ingest/dlq`, `/ledger` (shell — A-09 fills it), `/eval/report`, `/audit*`, `/rehearsal/*` (404 when the flag is off), `/health`, `/metrics`.
- **Implementation notes:** `POST /ingest/batch` uses the **same** validation path as the replay source so a real PLC gateway can post today with no code change (spec §3.2). Routes belonging to later tasks land here as **contract-correct shells returning real-shaped empty results** — never fixtures, never `NotImplementedError` — so `tests/test_contract.py` stays green and the frontend never sees a stub after H6.6.
- **Acceptance criteria:**
  - **Every** Appendix-B route returns real data or an honest empty result; `grep -rn "fixture" api/` is clean.
  - `tests/test_contract.py` passes unchanged against real implementations.
  - `POST /ingest/batch` and the replay source share one validation function (verified by import, not duplication).
  - A route whose feature flag is off returns **404**, not 500 and not an empty 200.
- **Demo relevance:** No — but a stub surviving into the demo is what this task prevents.

### [B-09] 🔒 Coordinator Agent + prompt v1 + template fallback
- **Workstream:** AI Integration
- **Tag:** MVP
- **Depends on:** A-06, B-03, B-10, B-11
- **Estimated time:** 0h 40m
- **Description:** `agents/coordinator_agent.py` — calls `fusion.fuse()` **first** so risk, band, confidence, autonomy, action class and scheduling window are **fixed before the LLM runs**, then makes one LLM call producing strict JSON, with a deterministic template fallback that is MVP, not contingency.
- **Implementation notes:** `agents/prompts/v1/coordinator.md`, `PROMPT_VERSION` stamped on every output alongside model + timestamp. Output schema exactly per spec §3.8.3: `fault_diagnosis` (≤60 words), `reasoning_steps[]` (3–5, each ending in citation tags), `facts[]`, `assumptions[]`, `recommended_actions[]` (action, priority, rationale with citations, parts, est_labour_hours, window), `limits`, `citations[]`. Prompt states the rule the guardrail enforces: **never state a number not in the evidence bundle; separate facts from assumptions; if evidence conflicts, say so.** **Written for the configured model:** the schema is restated in the prompt as an explicit JSON skeleton with a "return the JSON object and nothing else — no markdown fences, no preamble" instruction, because `response_format` may not survive a provider passthrough (spec §3.11). Parsing goes through `llm.parse_json()`'s tolerant extractor, so a fenced or prose-wrapped reply still succeeds; only genuinely unparseable output falls to the template. Evidence bundle hard-capped at **6,000 tokens**, dropping lowest-ranked evidence first and **recording what was dropped**. **The Coordinator holds no ERP write tool — that capability is not in its tool list.** Deterministic template is built **only from fusion output** so it cannot hallucinate; stamped `generated:false, model:"deterministic-template"`.
- **Acceptance criteria:**
  - `fusion.fuse()` is called before any LLM call (asserted by call-order test) and **its numbers appear unchanged** in the final output.
  - Output validates against the strict Pydantic schema; `limits` non-empty; 1–4 actions.
  - Every `recommended_action` carries ≥1 citation that resolves.
  - With `LLM_API_KEY` unset, a complete diagnosis is still produced, stamped `generated:false, model:"deterministic-template"`.
  - With a key set, at least one diagnosis is produced live by `openai-compatible-chat` and stamped with that model name + `PROMPT_VERSION` + provider.
  - A fenced ```` ```json ```` reply and a preamble-prefixed reply both parse successfully (fixture-driven test, no network).
  - Token budget enforced: a 20-item bundle is trimmed to ≤6,000 tokens with a `dropped_evidence[]` record.
  - Attempting to call an ERP write tool from this agent raises `AttributeError` (the tool does not exist).
  - **≤2 LLM calls per diagnosis** across Coordinator + Maintenance-Log (asserted).
- **Demo relevance:** **Yes — this is the prose in demo moment #1, and the offline stamp is the resilience talking point.**

### [B-12] 🔒 Orchestrator state machine
- **Workstream:** AI Integration
- **Tag:** MVP
- **Depends on:** B-09, E-01
- **Estimated time:** 0h 30m
- **Description:** `agents/orchestrator.py` — the explicit `DiagnosisRun` state machine with one trace ID end to end: `INIT → FANOUT ∥ → FUSE → DIAGNOSE → REFLECT → ROUTE → [ACT] → DONE`. Hand-rolled (~200 lines), **no agent framework**.
- **Implementation notes:** FANOUT = `asyncio.gather(sensor, maintlog)`, **20s per-agent timeout, 45s per-run**; exceeded → partial-evidence path, never a hang. FUSE **always runs, even if one arm failed**. DIAGNOSE retries ≤2 on transport error. REFLECT: guardrail rejection → re-invoke Coordinator **once** with the rejection reasons appended → second rejection → deterministic template. ROUTE by autonomy: A3 auto-log · A2 dashboard alert · A1 technician ack · A0 planner approval. The two specialist agents **never talk to each other** — supervised fan-out/fan-in with a reflection loop. Every state transition emits an SSE `run_state` event.
- **Acceptance criteria:**
  - One `trace_id` appears in `agent_runs` for all agents in a run; `GET /runs/{trace_id}` reconstructs it in order.
  - Sensor arm forced to fail → run still completes with history-only evidence and the Coordinator is **told the sensor arm is missing**.
  - Guardrail rejection → exactly **one** retry, then template; visible as a retry node in the trace.
  - A 25s hanging agent is cancelled at 20s and the run completes within 45s.
  - `run_state` SSE events fire for every transition, in order.
  - **Contingency (Risk 5):** a `--sequential` config flag drops `asyncio.gather` for serial execution, costing ~1.2s and removing the concurrency failure surface. Verify this flag works **now**, before it is needed.
- **Demo relevance:** **Yes — the agent-trace timeline judges watch draw itself.**

### [C-06] Agent-trace timeline component
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** C-04, A-02
- **Estimated time:** 0h 30m
- **Description:** A horizontal timeline of the run — Sensor ∥ Log → Fuse → Diagnose → Reflect → Route — where each node fills as it completes showing its **real** latency, and a guardrail rejection renders as an amber node that visibly forks into a retry.
- **Implementation notes:** Data from `GET /runs/{trace_id}` (real `agent_runs` latencies — **never fabricated timings**). Parallel arms drawn as two rows converging at FUSE. Amber fork for rejection→retry. Connectors are `<path>` elements so C-09 can add a `pathLength` draw-on without restructuring.
- **Acceptance criteria:**
  - Six node types render with their **actual** `latency_ms` from the API, in JetBrains Mono.
  - Sensor and Log render as parallel arms converging at Fuse.
  - A run with a guardrail rejection shows an amber node forking into a retry node.
  - Timeline renders correctly for a **partial** run (one arm failed) without breaking layout.
  - Connectors are SVG paths ready for `pathLength` animation.
- **Demo relevance:** **Yes — "watch the agentic mechanics" in moment #1.**

### [C-08] Stub→real cutover, SSE wiring, zero-blank-state sweep
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** A-07a, C-02, C-04
- **Estimated time:** **1h 30m** *(0:35 at H5.95, 0:55 at H7.35)* — **re-estimated from 1:00 *(gap D9 — see §0.2)*. Integration cutover across four screens plus SSE reconnect plus a six-route blank-state sweep was the least protected hour in the plan, and cutover is where hackathon demos die.**
- **Description:** Cut every screen from A-02 stubs to real endpoints, wire the SSE `EventSource` with `Last-Event-ID` reconnect and a 10s polling fallback, add the health strip, and sweep the entire app for blank states.
- **Implementation notes:** `EventSource` on `/stream/events` handling `telemetry | alert | run_state`; `onerror` → exponential-backoff reconnect with `Last-Event-ID`, then TanStack Query polling every 10s as a hard fallback, showing a "Reconnecting…" chip. Health strip in the header from `GET /health` (6 dependencies). **Zero-blank-state rule:** every panel has data, a skeleton, an empty state, or an honest "no data, here's why".
- **Acceptance criteria:**
  - All four MVP screens read **real** API data; no fixture imports remain (`grep -r fixture ui/src` is clean).
  - Killing the backend mid-stream shows "Reconnecting…", then data resumes via polling within 10s; restarting the backend resumes SSE.
  - Header health strip shows **6** dependency states with colour + label.
  - **Zero blank states** — walk all 6 routes with an empty DB and screenshot each; every panel shows a skeleton, an empty state, or a reason.
  - No layout shift on data load (skeletons match final dimensions).
- **Demo relevance:** Yes — a blank panel in front of a judge is the failure mode this task exists to prevent.

---

### Hour 6.0–7.0 — ⛳ H6.5 GO/NO-GO, auth, approvals, Schedule 🔒
*⛳ **H6.5 — THE MVP CUTLINE.** End-to-end must work live: **stream → prediction → alert → diagnosis → dashboard**. **If red: all stretch work stops immediately**; H6.5–H8.0 goes to finishing it and Phase 4/5 is the sacrificial budget — **now 0:40, not 2:30 (plan.md §3.2), so this gate is less forgiving than it used to look.** H11.0–H12.0 rehearsal stays protected regardless.*

---

### [E-02] 🔒 JWT auth, argon2id, RBAC
- **Workstream:** Security
- **Tag:** MVP
- **Depends on:** A-07a
- **Estimated time:** 0h 45m
- **Description:** `POST /auth/login` issuing an HS256 JWT in an httpOnly cookie, argon2id password hashing, a `require_role()` FastAPI dependency applied to every mutating route, 4 seeded users, and a 403 matrix test.
- **Implementation notes:** JWT `{sub, role, jti, exp}`, 8h TTL, **`httpOnly` + `SameSite=Strict` cookie — not `localStorage`** (an XSS bug must not exfiltrate the session). argon2-cffi `argon2id`, memory 64MB, time 3. Seeded users: `operator`, `technician`, `planner`, `admin` per the spec §7.2 matrix. `api/auth.py` exposes a `verify_token()` seam an enterprise deployment repoints at OIDC without touching route code. **Enforced by the API, never by the UI alone.**
- **Acceptance criteria:**
  - `tests/test_security.py` asserts **every mutating endpoint returns 403 for every insufficient role** — a full matrix, ≥16 cases.
  - `operator` calling `POST /approvals/{id}/approve` → 403; `planner` → 200.
  - Login sets an `httpOnly; SameSite=Strict` cookie; the token is **not** readable from `document.cookie`.
  - Expired/tampered JWT → 401 with the error envelope, no stack trace.
  - **Zero endpoints without auth** except `/health` and `/auth/login` (asserted by iterating the route table).
  - Passwords stored as argon2id hashes; no plaintext anywhere in the DB.
- **Demo relevance:** Yes — "Login as planner" opens the demo; RBAC is the security closer.

### [B-13] 🔒 Approval workflow + ERP Action Agent
- **Workstream:** AI Integration
- **Tag:** MVP
- **Depends on:** B-12, E-02
- **Estimated time:** 0h 40m
- **Description:** The `approvals` table + approve/reject routes (**reason mandatory on reject**) and `agents/erp_action_agent.py` — the **only** component with ERP write capability, whose approval precondition is enforced **in code, not by prompt**.
- **Implementation notes:** One tool: `create_work_order(payload)`. **Precondition:** an `approvals` row with `state="approved"` by a user holding the required role for this autonomy level must exist — otherwise `PermissionError`, logged as a **blocked unauthorised tool execution** security event. `Idempotency-Key = sha256(prediction_id)`. Retry 3× on 5xx/timeout only — **never on 4xx**. Partial failure writes a `pending_erp_sync` row drained by a background task with backoff, so an ERP outage **delays** a work order rather than losing it. Approve/reject routes carry `require_role()` per the autonomy level.
- **Acceptance criteria:**
  - Calling the ERP Action Agent with **no** approval row raises `PermissionError` and writes a security-event log line — **test-proven**.
  - Approving twice creates **one** work order in the ERP (same `Idempotency-Key`, same returned ID).
  - A 4xx from the ERP produces **zero** retries; a 5xx produces exactly 3.
  - `ERP_FAULT_MODE=down` during a write → a `pending_erp_sync` row exists and drains when the ERP returns; **0 work orders lost**.
  - Reject without a reason → 422; with a reason → 200 and the reason is persisted.
  - After an approval, `GET /erp/v1/workorders` shows the new work order.
- **Demo relevance:** **Yes — the safety/governance closer: "No LLM holds a write tool."**

### [C-07] Schedule & Approvals screen
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** C-01, A-02
- **Estimated time:** 0h 50m
- **Description:** A 14-day predicted-vs-planned maintenance calendar, the approval queue with autonomy-level badges, and the evidence drawer with typed-reason reject and optimistic updates.
- **Implementation notes:** Approve/Reject are deliberately **not one click** (spec §4.3): a slide-out drawer shows the full evidence bundle and **exactly what will be written to the ERP**; reject requires a typed reason. High-risk actions should feel weighty. Optimistic update via TanStack Query `onMutate` with rollback + toast on failure. Autonomy badges A3/A2/A1/A0 with distinct shapes, not just colours.
- **Acceptance criteria:**
  - 14-day calendar renders predicted vs planned windows distinguishably.
  - Approval queue shows autonomy badges; A0 items name the required approver role.
  - Reject with an empty reason is blocked client-side **and** returns 422 server-side.
  - The drawer displays the exact ERP payload that will be POSTed.
  - Optimistic approve updates instantly and **rolls back with a toast** when the API is forced to fail.
  - `operator` role sees approve controls hidden **and** gets 403 if they force the call.
- **Demo relevance:** Yes — the approval step of the safety/governance closer.

---

### Hour 7.0–8.0 — Audit chain, hardening, injection defence, eval scaffold 🔒
*Gate: every mutating endpoint 403s the wrong role; the audit chain verifies.*

---

### [E-03] Hash-chained audit ledger
- **Workstream:** Security
- **Tag:** MVP
- **Depends on:** E-02
- **Estimated time:** 0h 25m
- **Description:** `core/audit.py` — an append-only hash-chained ledger where every consequential event carries `hash = sha256(prev_hash ‖ canonical_json(entry))`, plus `GET /audit/verify` recomputing the chain and reporting the first broken link, plus a tamper-detection test.
- **Implementation notes:** Chained events: prediction issued, guardrail rejection, approval, rejection with reason, ERP write, config change, blocked tool execution. Canonical JSON = sorted keys, no whitespace, so the hash is reproducible. **The audit ledger is never pruned** (retention stated in `DEPLOYMENT.md`).
- **Acceptance criteria:**
  - `GET /audit/verify` returns `{intact: true, entries: N}` on a clean run.
  - Modifying one `payload_json` row directly in SQLite makes `/audit/verify` return `intact: false` **naming the first broken seq**.
  - A full demo run appends ≥6 entries covering ≥4 distinct action types.
  - Hash is reproducible: recomputing over the same rows twice yields identical values.
  - `tests/test_audit.py` includes the tamper test.
- **Demo relevance:** Yes — `/audit/verify` shown in the browser as the governance closer.

### [E-05] Rate limits, security headers, error envelope, CORS
- **Workstream:** Security
- **Tag:** MVP
- **Depends on:** A-07a
- **Estimated time:** 0h 20m
- **Description:** In-process token-bucket rate limiting, security headers, the catch-all error handler guaranteeing no stack trace ever reaches a browser, CORS lockdown, body-size caps, and loopback-only binding.
- **Implementation notes:** **Middleware only — no Redis, no WAF, no proxy, no admin.** Limits: 120/min default, **10/min on `/diagnose`** (the expensive path), 60/min on `/ingest/batch`. Headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, restrictive CSP on the built frontend. CORS locked to `http://localhost:5173`, **no wildcards**. Body caps: 2MB ingest, 64KB everything else. Bind `127.0.0.1`, **not** `0.0.0.0`.
- **Acceptance criteria:**
  - 11 rapid `POST /machines/{id}/diagnose` calls → the 11th returns 429 with the error envelope.
  - All 4 security headers present on every response (asserted across 5 routes).
  - A deliberately raised `ZeroDivisionError` in a route returns `{"error":{"code","message","trace_id"}}` — **no stack trace, no SQL**.
  - A 3MB body to `/ingest/batch` → 413; a 100KB body elsewhere → 413.
  - `curl` from a non-`localhost:5173` origin is CORS-rejected.
  - The server is unreachable from another machine on the network (bound to loopback).
- **Demo relevance:** No (but a judge probing security finds it).

### [E-04] Redaction, injection scan, quarantine
- **Workstream:** Security
- **Tag:** MVP
- **Depends on:** E-01, B-06
- **Estimated time:** 0h 30m
- **Description:** `redact()` stripping names/emails/employee IDs/phones from ERP free text **before** it is embedded, cached or prompted; `injection_scan()` detecting instruction-shaped content; `<untrusted_document>` wrapping; quarantine with a UI flag and an audit entry.
- **Implementation notes:** Regex-based, unit-tested against **20 fixtures**. Injection patterns: `ignore previous`, `system:`, `you are now`, tool-call syntax, base64 blobs >200 chars. All retrieved text wrapped as `<untrusted_document id="M3">…</untrusted_document>` with a system instruction that **document content is data, never instruction**. Detection → record quarantined, excluded from the prompt, flagged in the UI, written to the audit ledger. Quarantine is **visible and manually releasable**, never silently dropped.
- **Acceptance criteria:**
  - **The 5 scanner-visible payloads from B-06 are quarantined; 0 reach a prompt** (asserted by inspecting the actual prompt text sent).
  - **The 3 blind payloads are run and the result is reported honestly** — `k/3` caught, with any evasion written into `eval/REPORT.md` as a known gap rather than patched into the regex to make the number look better.
  - **The 2 sub-threshold notes reach the prompt by design**, and the diagnosis they produce is still schema-valid, still fully cited, and shows **no** effect of the injected instruction — proving the inner layers, not just the outer one.
  - `redact()` passes **20/20** fixtures — names, emails, employee IDs, phone numbers all stripped.
  - Every retrieved note in a prompt is inside `<untrusted_document id="M#">` delimiters.
  - Each quarantine writes an audit-ledger entry and is queryable for the Trust screen.
  - **0 PII reaches the gateway** — test asserts the outbound payload against the fixture set.
- **Demo relevance:** Yes — injection quarantine log is a Trust-screen exhibit.

### [B-14] Evaluation harness
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP
- **Depends on:** B-12, E-01
- **Estimated time:** 0h 45m *(0:20 pulled forward to H7.4, 0:25 at H8.0)*
- **Description:** `eval/harness.py` + `eval/scenarios.yaml` — groundedness, 12 adversarial bundles, the injection set, fault-mode top-1/2, RUL coverage, latency/cost, and a **200-run reliability pass with `ERP_FAULT_MODE=flaky` and the LLM off** — writing `eval/REPORT.md` plus a JSON summary the Trust screen renders.
- **Implementation notes:** Gates per spec §3.12: citation coverage ≥0.95, hallucination set **12/12 decline-or-hedge with 0 fabrications**, fault-mode top-1 ≥0.70, RUL p10–p90 coverage ∈ [0.75, 0.95], diagnosis **p95 ≤ 6s**, **≤3,500 tokens/diagnosis** (tokens, not £ — the LLM gateway exposes no billed rate to us, so a £ figure would be invented; report it as indicative or not at all). Adversarial bundles: empty evidence, contradictory evidence, injected instructions. **Run the harness at least once with the live gateway** so the reported latency reflects the configured model through managed networking, not the template path — a p95 measured offline is meaningless for Risk P2. `python scripts/evaluate.py` is the entry point.
- **Acceptance criteria:**
  - `python scripts/evaluate.py` writes `eval/REPORT.md` with tables + a confusion matrix, and a JSON summary.
  - **12/12** adversarial bundles produce a decline or a hedge — **0 fabrications**.
  - 200-run reliability pass: **0 unhandled exceptions, 100% produce a diagnosis**.
  - Citation coverage ≥ 0.95 reported; numeric-grounding first-try pass rate ≥ 0.95 reported.
  - Latency p50/p95 per stage and **tokens/diagnosis** reported, with the model name and whether the run was live or templated stated next to them.
  - **If p95 > 6s (Risk P2):** apply the pre-decided fallback — (a) drop the log-summary LLM call for deterministic aggregation (~1.8s saved); (b) if still over, diagnosis becomes on-demand only. The ≤250ms alert path never waits on the LLM either way.
- **Demo relevance:** Yes — feeds the Trust screen and `eval/REPORT.md`.

### [C-10] Login screen + auth wiring *(gap **T2** — see §0)*
- **Workstream:** Frontend & Experience
- **Tag:** MVP
- **Depends on:** E-02, C-01
- **Estimated time:** 0h 15m
- **Description:** A `/login` route posting to `POST /auth/login`, a route guard redirecting unauthenticated users, a role-aware nav that hides what the current role cannot do, and a logout control. *(Flagged: no plan.md/spec §11 task covers this, but §8.1 requires "Login as `planner`".)*
- **Implementation notes:** Cookie is `httpOnly` so the client cannot read the token — determine the role from a `GET /me`-style call or the login response body (role only, never the token). **The UI hides what you can't do; the API refuses it** — never rely on hiding alone. **The cookie only works because C-01's dev proxy makes the browser same-origin with the API** *(D10)*: `SameSite=Strict` treats `localhost:5173` → `127.0.0.1:8000` as cross-site and would drop the cookie on every request. If login 401s here, the cause is an absolute URL somewhere in `ui/src` — **do not** "fix" it by relaxing `SameSite` or enabling credentialed CORS; that trades an XSS-resistant session for a demo shortcut.
- **Acceptance criteria:**
  - Logging in as `planner` lands on Live Floor with the session cookie set **and sent on the next request** (devtools Network tab, not inferred from a 200).
  - `grep -rn "127\.0\.0\.1\|localhost:8000" ui/src` returns nothing.
  - Visiting `/schedule` unauthenticated redirects to `/login`.
  - Logged in as `operator`, approve controls are hidden **and** a forced API call still returns 403.
  - Logout clears the session and redirects to `/login`.
  - Wrong password shows a plain inline error, no stack trace, no field-level enumeration.
- **Demo relevance:** Yes — the first 5 seconds of the demo.

### [C-08 cont.] Live Floor ↔ real API polish
*(Second 0:30 half of C-08 — skeletons everywhere, zero-blank-state sweep. Acceptance criteria as listed above.)*

---

### Hour 8.0–9.0 — ⛳ H8.5, downtime simulator, KPI gate #2 ⭐
*⛳ **H8.5:** security + HITL complete; both KPI gates are now the only remaining P0 risk.*

---

### [A-08] Counterfactual downtime simulator
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-04, B-06
- **Estimated time:** 0h 30m
- **Description:** `core/downtime_sim.py` — replay the identical 60-day ground-truth event stream under three maintenance policies and report downtime minutes and £, per machine and plant-wide, **charging a full 90-minute planned window for every false positive** so the number cannot be gamed by lowering τ.
- **Implementation notes:** Policy A reactive → `MTTR_unplanned` (mean 320 min). Policy B fixed-schedule → PM every 30 days regardless: planned windows (90 min) + failures still missed. Policy C predictive → act when `p_failure ≥ τ` within the recommended window: planned windows + failures the model missed. `reduction_pct = (A − C) / A × 100`. £ from each machine's hourly downtime cost **plus** parts and labour from ERP history. **τ overridable by parameter** (this is what D-01's slider drives). Pure NumPy so a re-run is < 80ms.
- **Acceptance criteria:**
  - Returns per-machine and plant-wide downtime minutes **and** £ for all three policies.
  - Lowering τ measurably **increases** false-positive cost (verified at τ = 0.2 vs 0.42 vs 0.6) — the gaming defence works.
  - A full re-run with a new τ completes in **< 80ms**.
  - Missed failures are reported explicitly with their cost included (this is D-01's "what the model missed" toggle).
  - Same τ twice → identical output (deterministic).
- **Demo relevance:** Yes — the engine behind demo moment #2.

### [A-09] 🔒 ⛳ KPI GATE #2 — downtime reduction ≥ 20%
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP
- **Depends on:** A-08
- **Estimated time:** 0h 15m
- **Description:** `tests/test_kpis.py::test_downtime_reduction_at_least_20` asserting the simulator's reduction ≥ 20%, plus the `GET /ledger?tau=` endpoint serving the same computation to the UI. **The demo and the test are one artifact.**
- **Implementation notes:** The endpoint calls the **same** `downtime_sim` function the test calls — no parallel implementation. `tau` is an optional query param defaulting to the value in `MANIFEST.json`.
- **Disclose the entailment, don't wait to be caught by it *(gap D11 — see §0.2)*.** Substituting the policy definitions, `reduction ≥ 0.20 ⟺ recall ≥ 0.278 + 0.39·(FP/N)`. Since A-05 already gates **recall ≥ 0.80** and the false-alarm target is ≤1/machine/30d, **this gate is entailed by KPI #1 plus two config constants** (`MTTR_unplanned = 320`, `planned_window = 90`) — it is recall expressed in minutes and pounds, not a second independent result. A judge can derive this in a minute, so `eval/REPORT.md` **prints the inequality beside the number** and `MODEL_CARD.md` repeats it. What stays defensible: we ran the counterfactual over the identical event stream, we charged ourselves for our own false positives, and the τ slider shows the **trade-off curve**, which recall alone does not.
- **If the gate is red *(Risk P3 — previously the only KPI with no contingency at all)*, 20-minute box, in order:** (a) widen `recommended_window` so a correct prediction is actioned inside one planned window instead of two; (b) if `FP/N` is the driver, **raise** τ — the inequality shows the gate is more sensitive to false positives than to recall; (c) still red → publish the measured number with the inequality and mark the target missed on the Trust screen with the 20% line still drawn. **Never touch `MTTR_unplanned` or `planned_window`** — moving the constants the result is derived from is how a KPI becomes a lie.
- **Acceptance criteria:**
  - `pytest tests/test_kpis.py::test_downtime_reduction_at_least_20` **passes**; failure message reports the actual %, the recall it implies, and which lever (a)/(b) applies.
  - `eval/REPORT.md` contains the entailment inequality immediately below the reduction figure.
  - `GET /ledger` returns three policies with minutes, £, and the reduction %, validating against its schema.
  - `GET /ledger?tau=0.30` returns a different, internally consistent result in **< 200ms**.
  - The route and the test invoke the identical function (verified by import, not by duplication).
- **Demo relevance:** **Yes — "This is the pytest gate, not a slide."**

### [D-01] ⭐ Downtime Ledger screen + τ slider
- **Workstream:** Standout Feature
- **Tag:** Stretch
- **Depends on:** A-09
- **Estimated time:** 0h 50m
- **Description:** Three bars racing upward under Framer Motion springs, a count-up headline resolving to the reduction % and £ avoided, the 20% target line drawn across the chart, a "what the model missed" toggle, and a **τ slider that re-runs all three policies live**.
- **Implementation notes:** Bars from `GET /ledger`; τ slider debounced, hitting `GET /ledger?tau=` and re-animating (<80ms budget end to end). Headline count-up via `requestAnimationFrame` easing. 20% target line as a `ReferenceLine`. "What the model missed" toggle reveals the uncaught failures **with their cost included** — the honesty beat. **Nothing in the MVP imports this screen.**
- **Acceptance criteria:**
  - Three bars render with real values and animate to position in ≤ 600ms.
  - Dragging the τ slider updates all three bars in **< 80ms** perceived.
  - Headline count-up resolves to the reduction % and £ from the API, then **snaps and stays readable**.
  - 20% target line is drawn and labelled.
  - "What the model missed" toggle lists uncaught failures with cost.
  - Deleting this route breaks nothing else (verified by removing it and running the suite).
- **Demo relevance:** **Yes — demo moment #2 in its entirety.**

---

### Hour 9.0–10.0 — Health/metrics, fallback predictor, seed scripts, Trust screen, animation ⛳
*⛳ **H10.0 — THE CUT LINE.** Cut bottom-up per §4. No debate; the order is already decided.*

---

### [A-10] Health and metrics endpoints
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-07a
- **Estimated time:** 0h 20m
- **Description:** `GET /health` returning per-dependency status with latency and circuit-breaker state, and `GET /metrics` returning JSON counters. **This is the first task to drop if H9.0–10.0 needs bug-triage time** (§0 warning; plan.md §7 DoD-A lists `/metrics` as not required).
- **Implementation notes:** Six dependencies: `db, tsdb, models, erp, llm, stream`. Counters: requests, errors by code, agent latencies (p50/p95), guardrail rejections by rule, LLM prompt/completion tokens, DLQ depth, circuit-breaker trips. The `llm` dependency reports `{reachable, model_name, provider, tls_verified, breaker_state}` — `tls_verified: false` is surfaced, never hidden.
- **Acceptance criteria:**
  - `GET /health` returns **6** dependencies with status + latency_ms + breaker state.
  - Stopping the mock ERP flips `erp` to degraded within one poll; the UI health strip reflects it.
  - `GET /metrics` returns valid JSON with all 7 counter families.
  - `/health` is reachable **without** auth (the only such route besides `/auth/login`).
- **Demo relevance:** Yes — the health strip degrading live during the resilience closer.

### [A-11] Rule-based fallback predictor + artifact verification
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-04
- **Estimated time:** 0h 20m
- **Description:** A transparent rule-based predictor with documented per-channel thresholds that takes over when model artifacts are missing, hash-mismatched, or feature-version-mismatched — with every output stamped `predictor: "rule_fallback"`. **The dashboard never goes blank; it goes honest.**
- **Implementation notes:** sha256 of each artifact verified against `models/MANIFEST.json` at load; `FEATURE_VERSION` asserted. Thresholds documented in the module docstring and sourced from `config.yaml`. UI banner: "Fallback predictor active — accuracy not guaranteed".
- **Acceptance criteria:**
  - Deleting a `.joblib` → the system starts, serves rule-fallback predictions, and stamps every one `rule_fallback`.
  - Corrupting one byte of a `.joblib` → load **refuses** with a hash-mismatch message and falls back.
  - Setting `FEATURE_VERSION` to `fv2` → load refuses rather than serving garbage.
  - The UI shows the fallback banner whenever any prediction is stamped `rule_fallback`.
- **Demo relevance:** Yes — a resilience answer if a judge asks "what if the model is broken?"

### [A-13] Seed and run-all scripts *(gap T1 — see §0)*
- **Workstream:** Backend
- **Tag:** MVP
- **Depends on:** A-03, A-04, B-06, B-07
- **Estimated time:** 0h 20m
- **Description:** `scripts/seed.py` (generate data → train models → build the retrieval index → seed users → verify) and `scripts/run_all.py` (start backend :8000 + mock ERP :8001 with `subprocess.Popen`). **Without these the 4-command demo cannot start.**
- **Implementation notes:** `seed.py` is **idempotent**, verifies its own output via `verify_signal()` **and then runs `tests/test_demo_integrity.py` (A-15) after training**, supports `--if-stale` and `--verify-only`. `run_all.py` uses `subprocess.Popen` — **no orchestration tool, no service registration** — streams both children's logs prefixed, and shuts both down cleanly on Ctrl-C.
- **Acceptance criteria:**
  - On a **fresh clone**, `python scripts/seed.py` completes in **≤ 90s** and exits 0.
  - Running `seed.py` twice does not duplicate data or fail.
  - `seed.py` **raises** if `verify_signal()` fails **or if the post-training demo-integrity test fails** — a broken demo cannot be seeded, and the model-dependent half is checked at the only point it can be.
  - `python scripts/run_all.py` brings up :8000 and :8001; `GET /health` shows 6 green dependencies.
  - Ctrl-C terminates **both** child processes; no orphans remain.
  - **Total fresh-clone path is 4 commands and ≤ 3 minutes excluding the 90s seed.**
- **Demo relevance:** **Yes — this is literally how the demo starts.**

### [D-02] ⭐ Trust & Evals screen
- **Workstream:** Standout Feature
- **Tag:** Stretch
- **Depends on:** B-14, E-01, E-04
- **Estimated time:** 0h 30m
- **Description:** The eval report rendered live — accuracy vs the 85% line, downtime reduction vs the 20% line, the guardrail rejection log by rule, the injection quarantine log, latency/cost/token totals, DLQ contents, circuit-breaker states, model + prompt version stamps, and the **LLM-offline chaos toggle**. *(plan.md delta D3: reassigned R3 → R2, who authors the eval JSON it renders.)*
- **Implementation notes:** Data from `GET /eval/report`, `/metrics`, `/ingest/dlq`, `/health`. Target lines drawn on both KPI charts. The LLM-offline toggle is a deliberate presenter control for the resilience closer.
- **Acceptance criteria:**
  - Accuracy and reduction render with their **target lines drawn** (85% / 20%).
  - Guardrail rejection log shows **≥1 real entry** with the rule that fired.
  - Injection quarantine log shows the **8** planted notes.
  - Latency p50/p95 and token totals render from real counters (£ only if labelled *indicative*).
  - The LLM-offline toggle flips the system to templates **live** and the Diagnosis screen shows the amber banner.
  - Model + prompt version stamps are visible and read `openai-compatible-chat` via `provider: langchain` on a live run.
  - **TLS posture is shown**: `tls_verified: true|false` for the gateway, amber when false — the security exception is disclosed on screen, not buried in a log.
- **Demo relevance:** Yes — the rigour and resilience closers.

### [C-09] Framer Motion pass
- **Workstream:** Frontend & Experience
- **Tag:** Stretch *(plan.md labels this "MVP-additive"; Risk 4 makes it the first frontend item cut, so it is tagged Stretch to keep the cutline clean)*
- **Depends on:** C-02, C-04, C-06
- **Estimated time:** 0h 35m
- **Description:** The animation pass, deliberately kept as **one separate task so it can be deleted, not unpicked**: alert `AnimatePresence` entrance, risk-ring `useSpring`, count-up numbers, `layoutId` tile→detail morph, agent-trace `pathLength` draw-on, staggered grid, and `MotionConfig` reduced-motion.
- **Implementation notes:** `useSpring` at `stiffness 120, damping 20` on rings and gauges. `AnimatePresence` for alerts (240ms `easeOut` in; height collapse out). `layoutId` shared-element morph from tile → Machine Detail header. `motion.path` `pathLength` draw-on for trace connectors. Staggered `variants` at 40ms/child on first grid paint. **Global rule: nothing animates longer than 600ms; nothing animates that carries a number the user must read now** — the number snaps, the context animates.
- **Acceptance criteria:**
  - New alerts slide+fade in over ~240ms; dismissed alerts collapse their height.
  - Clicking a tile **morphs** it into the Machine Detail header (no page cut).
  - Agent-trace connectors draw on as each stage completes.
  - **`prefers-reduced-motion: reduce` degrades every animation to an instant state change** — verified in devtools emulation.
  - No animation exceeds 600ms; risk/RUL/£ numbers snap rather than animating.
  - Removing this task's diff leaves all screens functional (it is additive only).
- **Demo relevance:** **Yes — this is what makes moment #1 and #2 land.**

---

### Hour 10.0–11.0 — Stretch only ⭐
*Entered **only if the H10.0 checkpoint is fully green.** ⛳ **H11.0: hands off all stretch.** Anything unfinished is flagged off, not debugged.*

---

### [D-03] ⭐ Rehearsal backend
- **Workstream:** Standout Feature
- **Tag:** Stretch
- **Depends on:** A-07, B-12
- **Estimated time:** 0h 45m
- **Description:** `POST /rehearsal/inject` writing `is_simulated=true` sensor rows **through the identical preprocessing/feature/model/agent/guardrail path as real data**, `POST /rehearsal/reset` restoring the exact prior state, and `GET /rehearsal/scoreboard` reporting detection rate, lead time and mode accuracy.
- **Implementation notes:** **No special-casing, no scripted output** — an injected row is an ordinary sensor row with a flag. Inject params: `{machine_id, mode, severity, onset}`. Reset deletes every `is_simulated` row and restores all 15 machines to their original risk. Requires `technician` role or above.
- **Acceptance criteria:**
  - Injection flows through the identical pipeline (asserted: no branch on `is_simulated` in `core/`).
  - Injecting `bearing_wear` at high severity raises the machine's risk and fires an alert within one replay tick.
  - `POST /rehearsal/reset` restores **all 15 machines to their exact original risk scores** — test-proven.
  - Scoreboard reports detection rate, hours of lead time, and mode accuracy across rehearsals.
  - Injecting `sensor_drift` → the system **declines** to raise a mechanical fault (the best trick it has).
  - `operator` role → 403 on inject.
- **Demo relevance:** Yes — "how do I know it works?", answered live.

### [D-04] ⭐ Rehearsal Console UI
- **Workstream:** Standout Feature
- **Tag:** Stretch *(**mutually exclusive with D-05** — plan.md delta D4: R3 owns one, never both; decided at the H10.0 checkpoint)*
- **Depends on:** D-03
- **Estimated time:** 0h 30m
- **Description:** Machine / mode / severity / onset picker, a live risk-ring watch as the injected fault develops, and the running scoreboard. Self-contained behind `features.rehearsal`.
- **Implementation notes:** A single route `/rehearsal`, not imported by any MVP screen. Flag off → route not registered and **not visible in the nav**.
- **Acceptance criteria:**
  - Picker submits all 4 params and shows the resulting alert within one tick.
  - Risk ring for the target machine visibly climbs during the rehearsal.
  - Scoreboard updates after each rehearsal.
  - Setting `features.rehearsal: false` removes the route **and the nav entry** with no other change.
  - A reset from the UI returns the floor to its pre-rehearsal state.
- **Demo relevance:** Yes — optional live "inject a fault" moment.

### [D-05] ⭐ 3D digital twin — **HARD 45-MINUTE BOX**
- **Workstream:** Standout Feature
- **Tag:** Stretch *(**mutually exclusive with D-04**)*
- **Depends on:** C-05
- **Estimated time:** 0h 45m *(hard box — not an estimate)*
- **Description:** A low-poly pump/motor assembly built from r3f primitives in the Machine Detail right panel, per-component risk tint, a pulsing fault component with a leader line, `OrbitControls`, click→channel filter, loaded as a lazy chunk behind a WebGL guard, flag default `false`.
- **Implementation notes:** `three` 0.169 + `@react-three/fiber` 8.17 + `drei` 9.114 — **npm only**. ~600 triangles from primitives, **no external model files, no asset pipeline, no license risk**. `React.lazy` + `Suspense` so Three.js is a **separate chunk the rest of the app never imports**. WebGL capability check + error boundary → falls back to C-05's SVG. Renders from the **identical `components[]` data** as the SVG — no duplicated logic. `ui.features.digital_twin` default `false` until proven working.
- **Acceptance criteria:**
  - Renders 5 tinted components in the Machine Detail right panel; the predicted fault component pulses with a leader line.
  - `OrbitControls` drag works; auto-rotate is slow and non-distracting.
  - Clicking a component filters the sensor chart to that component's channels (same behaviour as C-05).
  - Disabling WebGL in devtools → the SVG schematic renders instead, **no error visible to the user**.
  - The Three.js chunk is **separate** in `vite build` output and is not fetched when the flag is off.
  - **Risk 8 cut rule: if it is not visibly working at H10.75, `ui.features.digital_twin` stays `false`, the SVG ships, and ZERO other code changes are made. Do not "just fix one thing" — the box is hard.**
- **Demo relevance:** Yes if shipped; **if cut, the presenter says nothing about 3D.**

### [D-06] ⭐ Audit ledger viewer
- **Workstream:** Standout Feature
- **Tag:** Stretch
- **Depends on:** E-03
- **Estimated time:** 0h 20m
- **Description:** An `/audit` screen listing ledger entries with the `/audit/verify` result and a tamper indicator. *(The chain itself is MVP — E-03. Only the viewer is cuttable.)*
- **Implementation notes:** Table of `seq, ts, actor, action, subject`, with hash + prev_hash on expand. Verify banner: green "chain intact (N entries)" or red naming the first broken link.
- **Acceptance criteria:**
  - Lists ledger entries newest-first with all 5 summary columns.
  - Verify banner reflects `/audit/verify` and turns red when a row is tampered with.
  - `admin`/`planner` can view; `operator` gets 403.
  - Cutting this route leaves `/audit/verify` demoable directly in the browser.
- **Demo relevance:** Yes — the governance closer, upgraded from a raw JSON response.

### [A-12] ⭐ Operational scripts
- **Workstream:** Backend
- **Tag:** Stretch
- **Depends on:** A-13
- **Estimated time:** 0h 20m
- **Description:** `scripts/replay.py --trace-id=X` (re-run a stored run against current code and diff the output), `scripts/prune.py` (30-day retention on `agent_runs` + LLM cache, **never the audit ledger**), `scripts/backup.py` (copy out approvals + audit ledger before a reseed).
- **Implementation notes:** These are spec §10.2/§10.4 maintainability artifacts, not demo path.
- **Acceptance criteria:**
  - `replay.py --trace-id=X` re-runs a stored run and prints a field-level diff.
  - `prune.py` deletes `agent_runs` older than 30 days and **leaves `audit_ledger` untouched** (asserted).
  - `backup.py` writes a timestamped copy of approvals + audit ledger.
- **Demo relevance:** No.

### [E-06] ⭐ Fairness test
- **Workstream:** Security
- **Tag:** Stretch
- **Depends on:** B-05, A-04
- **Estimated time:** 0h 15m
- **Description:** `tests/test_fairness.py` — assert crew/shift features are absent from the feature vector and that prediction rates do not differ by crew beyond noise.
- **Implementation notes:** Crew IDs are `CREW-A/B/C`, never names (spec §3.1). Documented limitation: history-trained models inherit historical maintenance bias — surfaced in `docs/MODEL_CARD.md`.
- **Acceptance criteria:**
  - Test asserts no feature name contains `crew`, `shift`, or `operator`.
  - Prediction positive-rate difference between crews is within a stated noise band.
  - The known limitation is written into `docs/MODEL_CARD.md`.
- **Demo relevance:** No (but answers a bias question directly).

---

### Hour 11.0–12.0 — Harden, document, rehearse 🔒 *(never cut, even if P0 is incomplete)*

---

### [F-01] 🔒 Full test suite green + eval report
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP
- **Depends on:** all MVP tasks
- **Estimated time:** 0h 20m *(all three builders)*
- **Description:** Run the complete suite (~60 tests across 12 modules), fix or explicitly quarantine failures, and regenerate `eval/REPORT.md` via `python scripts/evaluate.py`.
- **Implementation notes:** Modules: contract, schema, generator, preprocess, features, kpis, fusion, guardrails, security, audit, agents, resilience. Coverage target ≥75% on `core/` and `agents/`.
- **Acceptance criteria:**
  - `pytest` → **~60 tests, 100% pass**, including **both KPI gates**.
  - Coverage on `core/` and `agents/` ≥ 75%.
  - `eval/REPORT.md` regenerated with **both KPI numbers above their target lines**.
  - Suite completes in a time short enough to run live in front of a judge (< 3 min).
  - Any quarantined test is named in `README.md` with a reason — no silent skips.
- **Demo relevance:** **Yes — "run `pytest` live" is the rigour closer.**

### [F-02] 🔒 Documentation deliverables
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP
- **Depends on:** F-01
- **Estimated time:** 0h 20m *(R1 + R2)*
- **Description:** Write the 5 authored docs (2 more are generated): `README.md`, `DEPLOYMENT.md`, `USER_GUIDE.md`, `docs/AGENTS.md`, `docs/MODEL_CARD.md`. **Accurate, not aspirational** — describe what shipped, including what was cut.
- **Implementation notes:** `README` = what it is, 4-command quickstart, architecture summary, demo script. `DEPLOYMENT` = install, config reference, **a dedicated "OpenAI-compatible LLM gateway" section** (obtain a key, `LLM_BASE_URL=https://llm-gateway.example.com`, the configured model name, `llm.provider` choice, the **TLS escalation ladder ending in `tls_verify: false` with its scope and residual risk stated**, how to swap the model in one line, and how to run with no key at all), backup, retention, capacity. `USER_GUIDE` = per-role walkthrough + how to read a diagnosis + what confidence means + **what the system cannot do**. `AGENTS` = roles, handoffs, prompts, tools, escalation. `MODEL_CARD` = training data, metrics by machine type, known failure modes, fairness notes, intended and out-of-scope use, **and the difficulty controls (held-out machines, sensor_drift trap) published rather than hidden**.
- **Acceptance criteria:**
  - All 5 authored docs exist; with generated `eval/REPORT.md`, `data/DEMO_NOTES.md` and OpenAPI `/docs` that is **7/7 deliverables**.
  - `README` quickstart is **verbatim executable** — verified during F-03.
  - `USER_GUIDE` contains an explicit "what this system cannot do" section.
  - `MODEL_CARD` names the 3 held-out machines and the `sensor_drift` trap, and states plainly that **the sklearn models produce every number while `openai-compatible-chat` only narrates** — the LLM is not part of the ≥85% claim.
  - `DEPLOYMENT` states the TLS posture actually used at demo time, whichever rung of the ladder it landed on.
  - **No doc claims a feature that was cut** (cross-checked against the §5 status board).
- **Demo relevance:** Yes — judges read the README.

### [F-03] 🔒 Clean-machine rehearsal ×2 (second one offline)
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP
- **Depends on:** F-02
- **Estimated time:** 0h 20m *(all three builders)*
- **Description:** From a **fresh clone**, run the 4 commands and perform the full demo — **twice**. The second run with the **network physically disabled** to prove the offline deterministic-template path. This is protected time and is never cut, even if P0 is incomplete.
- **Implementation notes:** Fresh clone → `python -m venv .venv` → `pip install -r requirements.txt` → `cp .env.example .env` → `python scripts/seed.py` → `python scripts/run_all.py`. Rehearsal #2: disable the network adapter, then run the identical script.
- **Acceptance criteria:**
  - Fresh clone to running demo = **4 commands, ≤ 3 minutes excluding the 90s seed**.
  - Both rehearsals complete the full core flow: login → Live Floor → PUMP-07 alert → 1-click Diagnosis → citation scrub → A0 approval → work order in the mock ERP.
  - **Rehearsal #2 with the network disabled completes end to end**, with the amber "Offline diagnosis mode" banner and the `deterministic-template` stamp visible.
  - `GET /health` shows 6 dependencies in both runs (llm degraded in run #2 — expected and shown).
  - Any step that failed is fixed and the rehearsal re-run, not noted for later.
- **Demo relevance:** **Yes — this is the demo.**

### [F-05] Demo-script dry run of both 30-second moments
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP
- **Depends on:** F-03
- **Estimated time:** 0h 15m *(R3, concurrent with F-02 docs)*
- **Description:** Rehearse both judge-facing moments to their time budgets, twice each, with the presenter speaking the actual lines. *(plan.md's H11.0–12.0 R3 row says "plus demo-script dry run of both 30-second moments" — this makes it a numbered, ownable task.)*
- **Implementation notes:** Script text in §5.3 below. Moment #1 budget **≤30s**, moment #2 **≤20s**. Time both with a stopwatch.
- **Acceptance criteria:**
  - Moment #1 runs in **≤ 30s**, twice, with PUMP-07 firing on cue.
  - Moment #2 runs in **≤ 20s**, twice.
  - Presenter delivers the framing line and both payoff lines without reading them.
  - The closer is chosen and rehearsed for at least **two** of the four audience variants.
  - Replay clock is reset to t0 after each rehearsal.
- **Demo relevance:** **Yes — it is the demo, rehearsed.**

### [F-04] Stretch-rollback drill *(gap T3 — contingency only)*
- **Workstream:** Integration-QA-Demo
- **Tag:** MVP *(protective — executed only if triggered)*
- **Depends on:** any D-* task attempted
- **Estimated time:** 0h 10m
- **Description:** If any stretch feature breaks the core flow during integration, execute the pre-agreed rollback: flip its flag off, unregister its route, remove it from the nav, and re-verify the core flow. **No debugging of stretch features after H11.0.**
- **Implementation notes:** Rollback per feature — 3D twin: `ui.features.digital_twin: false` (SVG ships, zero other changes). Rehearsal: `features.rehearsal: false` (route unregistered). Ledger UI: `features.ledger: false` (the number survives in `eval/REPORT.md` and `GET /ledger`). Trust screen / audit viewer: remove the nav entry. **A cut feature is not mentioned, not apologised for, and not visible in the nav.**
- **Acceptance criteria:**
  - After rollback, `pytest` is green and the full core flow runs end to end.
  - The cut feature's route returns 404 and its nav entry is **gone**, not disabled.
  - `config.yaml` feature flags match the §5 status board **exactly**.
  - No documentation references the cut feature.
  - Executed inside 10 minutes, or the feature is cut wholesale without further attempts.
- **Demo relevance:** Yes, indirectly — it is what prevents a broken screen appearing on stage.

---

## 2. Critical Path Tasks

From plan.md §4. **These must not slip.** The spine (**11:20**, revised from 9:30 by deltas D7–D9):

```
A-01 → A-02 → B-02 → A-03 → B-04 → B-05 → B-15 → A-04 → A-05 → A-15 ⛳H3.5
     → A-06 → E-01 → B-12 ⛳H6.5 → B-13 → A-09 → F-03
```

Full blocking set (plan.md §5 `Blocking = Yes`): **A-01, A-02, A-03, A-04, A-05, A-06, A-07a, A-09,
B-01, B-02, B-03, B-04, B-05, B-08, B-09, B-10, B-11, B-12, B-13, C-01, E-01, E-02, F-01, F-02, F-03**
— plus **A-13** (the demo cannot start without `seed.py`/`run_all.py`), **B-15** (nothing loaded the
trained artifacts) and **A-15** (nothing verified the demo against the trained model). `A-07b` is
explicitly **not** blocking.

**The four edges that decide the day:**

1. **`A-02` API contract freeze by H1.0** — 25 minutes that buy R3 eleven unblocked hours. If it slips, the frontend slips one-for-one and Risk R4 fires. If H1.0 is missed, R1 drops everything else until it lands. **It also freezes the browser-origin rule** (D10) — a screen holding an absolute API URL cannot be authenticated.
2. **`A-03 → B-04 → B-05 → B-15 → A-04`** — strictly serial, spans two owners. Mitigated by R1 emitting a 1-machine sample Parquet partition at **H1.3** so R2 develops preprocessing against real data while the full 2.59M-row generation still runs. **`B-15` was absent from this chain in every document**: `train.py` wrote artifacts that nothing loaded.
3. **`A-06 fusion` before `B-09 Coordinator`** — non-negotiable ordering. The LLM must receive numbers that already exist (spec §1.2). Building the Coordinator first inverts the architecture's core rule.
4. **`A-04 → A-05 → A-15`** — the demo's 50-second moment is a *model-dependent* property and can only be verified once τ exists. The old plan asserted it inside the generator, an hour before τ was computed.

**Checkpoint failure responses (pre-decided, no debate at the time):**
- **⛳H1.4 gateway slower than 150 tok/s** → apply the spec §6.2 branch **immediately** (drop the log-summary LLM call; below 60 tok/s also cut `max_tokens` to 300 and make diagnosis on-demand). Do not defer this to the eval harness at H9.0.
- **⛳H3.5 A-05 red** → extend horizon 24h→48h in `config.yaml`; still red → add lag features. Do **nothing else** until green. Report the horizon next to the number, always — **and re-run A-15**, because re-tuning τ moves the moment PUMP-07 fires.
- **⛳H6.5 red** → all stretch work stops; all three builders converge on the broken link H6.5–H8.0; WS-D cancelled outright; H11.0–H12.0 rehearsal still protected.
- **⛳H8.5 A-09 red** → Risk P3's 20-minute ladder: widen `recommended_window`, then raise τ, then publish the measured number with the entailment inequality. **Never move `MTTR_unplanned` or `planned_window`.**
- **Orchestrator debugging spiral >30 min after H5.0** → flip B-12's `--sequential` flag (costs ~1.2s, removes the concurrency failure surface entirely).

---

## 3. MVP Completion Gate

**When every task below is done, the core product works end to end. Nothing after this gate is required for a working demo.**

Tasks are grouped by the **workstream that builds them**, and each task appears **exactly once** in
this table. (An earlier version of this document counted several tasks twice across §3 and §6.2,
totalling 45 in one place and 48 in the other; the assignment below is now the single source of truth
and §6.2 defers to it.)

| Workstream | MVP task IDs | Count |
|---|---|---|
| Backend | A-01, A-02, A-03, A-04, A-06, **A-07a**, **A-07b**, A-08, A-10, A-11, **A-13**, B-01, B-02, B-04, B-05, **B-15**, B-06 | 17 |
| AI Integration | B-03, B-07, B-08, B-09, B-10, B-11, B-12, B-13 | 8 |
| Frontend & Experience | C-01, C-02, C-03, C-04, C-05, C-06, C-07, C-08, **C-10** | 9 |
| Security | E-01, E-02, E-03, E-04, E-05 | 5 |
| Integration-QA-Demo | A-05 *(KPI gate #1)*, **A-15** *(demo integrity)*, A-09 *(KPI gate #2)*, B-14, F-01, F-02, F-03, F-05, **F-04** *(contingency)* | 9 |

**Total: 48 MVP tasks** (45 previously + B-15, A-15, and the A-07 split).

**The gate is passed when this sentence is true, demonstrated live, not asserted:**

> A fresh clone runs in 4 commands; a `planner` logs in; the Live Floor paints 15 tiles with moving
> sparklines within 2s; **PUMP-07 crosses the alert threshold ~50s into replay, on screen**; one click
> opens a Diagnosis with a real agent trace, resolving citations, and separated Facts / Assumptions /
> Limits; hovering `[S2]` scrubs and shades the exact sensor window; an A0 approval routed through the
> evidence drawer creates a work order in the mock ERP; `GET /audit/verify` returns intact; and
> `pytest` is green **including both KPI gates**.

**Everything not in the table above is Stretch and can be cut without touching a single line of MVP code** — plan.md §2 never-blocking rule: WS-D has zero inbound edges from A/B/C/E/F.

---

## 4. Stretch Task Queue

**Ordered by judge-impact per hour of effort. If time is short, cut from the BOTTOM. Never cut at random.**

> **The queue is now opt-in from ⛳H8.5, not opt-out at H10.0.** plan.md §3.2 restates the buffer as
> **0:40** (it was never 2:30 — that figure sat alongside 3:40 of scheduled stretch). **Ranks 4–8 are
> cut by default** and are attempted only if H8.5 is green *and* ≥1:00 of float is genuinely unspent.
> Ranks 1–3 (D-01, C-09, D-02 — 1:55, all R2/R3) are the realistic ceiling.

| # | Task | Effort | Judge impact | Why this rank | If cut, what ships instead |
|---|---|---|---|---|---|
| **1** | **D-01** ⭐ Downtime Ledger screen + τ slider | 0:50 | **Highest** | It *is* demo moment #2. Turns the 20% KPI from a claim into a watchable measurement, and the τ slider is the single most interactive thing in the build. | Plain table + `eval/REPORT.md`; **the 20% number is unaffected** (A-08/A-09 are MVP). |
| **2** | **C-09** Framer Motion pass | 0:35 | **High** | Cheap per minute and it is what makes moments #1 and #2 *land*. Risk 4 makes it the first frontend cut, so it must be earned, not assumed. | Screens work identically, just instant. Nothing breaks. |
| **3** | **D-02** ⭐ Trust & Evals screen | 0:30 | **High** | Renders both KPI gates, the guardrail rejection log and the injection quarantine in one place — it answers the evaluation criteria directly. Carries the LLM-offline chaos toggle. | `eval/REPORT.md` opened in the editor. |
| **4** | **D-06** ⭐ Audit ledger viewer | 0:20 | **Medium-high per hour** | 20 minutes for a complete governance closer. Best impact-to-effort ratio in the queue. | `GET /audit/verify` shown in the browser — still convincing. |
| **5** | **D-03 + D-04** ⭐ Rehearsal backend + Console UI | 0:45 + 0:30 | **High but expensive** | Answers "how do I know it works?" live, and `sensor_drift` declining is the best trick the system has. But 1:15 total is a large bet at hour 10. | Nothing — flag off, screen not routed, **never mentioned**. |
| **6** | **D-05** ⭐ 3D digital twin | 0:45 **hard box** | **Medium — high variance** | Memorable if it works, a time sink if it doesn't. **Mutually exclusive with D-04** (plan.md D4: R3 owns one, never both — decide at H10.0). | `ui.features.digital_twin` stays `false`; C-05's SVG schematic ships with identical hotspots. **Zero other changes.** Presenter says nothing about 3D. |
| **7** | **E-06** ⭐ Fairness test | 0:15 | **Low-medium** | Non-visual, but it is a direct, cheap answer to a bias question a judge may ask. | The `MODEL_CARD` bias note stands alone. |
| **8** | **A-12** ⭐ Operational scripts | 0:20 | **Lowest** | Real maintainability value, zero judge-facing surface. Cut first. | plan.md §7 DoD-A explicitly lists these as "Not required". |

**Cut-line discipline (plan.md §3.1, decided now so there is no debate at H10.0):**
**P0 never cut** → core flow + both KPI gates + auth/RBAC/audit + guardrails + Live Floor / Machine Detail / Diagnosis. **If P0 is incomplete at H10.0, the entire queue above is cancelled** and H10.0–H12.0 goes to P0.
**P1** = D-01, C-07, D-02 → cut the animation, keep the data. **P2** = D-03/D-04. **P3** = D-05. **P4** = command palette, light mode, extra micro-interactions → cut without discussion (they are not tasks in this file, by design).

---

## 5. Pre-Demo Verification Checklist

### 5.1 Core flow — live, no excuses *(F-03)*

- [ ] Fresh clone → venv → `pip install -r requirements.txt` → `cp .env.example .env` → `python scripts/seed.py` → `python scripts/run_all.py`. **4 commands, ≤3 min excluding the 90s seed.**
- [ ] Backend :8000, mock ERP :8001, UI :5173 all up; `GET /health` shows **6 green dependencies**.
- [ ] Login as `planner`; Live Floor paints 15 tiles with moving sparklines within 2s.
- [ ] **PUMP-07 crosses the alert threshold 45–60s into replay, live on screen** (planted by A-03 relative to `t0`, verified against the *trained model and tuned τ* by **A-15**).
- [ ] Alert → **1 click** → Diagnosis with agent trace, citations, Facts / Assumptions / Limits.
- [ ] Hovering `[S2]` scrubs + shades the exact sensor window; `[M3]` pops the work order.
- [ ] A0 approval → evidence drawer → approve → **work order appears in the mock ERP** (`GET /erp/v1/workorders`).
- [ ] Both KPI numbers visible on screen with their target lines drawn.
- [ ] `pytest` runs live in a terminal if asked: all green, **both KPI gates included**.

### 5.2 Standout status board — fill in at H11.0, no ambiguity *(F-04 input)*

| Feature | Built / Cut | If cut, what ships instead |
|---|---|---|
| ⭐ D-01 Downtime Ledger UI + τ slider | ☑ Built ☐ Cut | Plain table + `eval/REPORT.md`; the 20% number is unaffected |
| C-09 Framer Motion pass | ☑ Built ☐ Cut | Instant state changes; every screen still works |
| ⭐ D-02 Trust & Evals screen | ☑ Built ☐ Cut | `eval/REPORT.md` opened in the editor |
| ⭐ D-06 Audit ledger viewer | ☑ Built ☐ Cut | `GET /audit/verify` shown in the browser |
| ⭐ D-03/D-04 Fault Rehearsal Console | ☐ Built ☑ Cut | Nothing — flag off, screen not routed, never mentioned |
| ⭐ D-05 3D digital twin | ☑ Built ☐ Cut | C-05's 2D SVG schematic, identical hotspots — presenter says nothing about 3D |

**Rule: a cut feature is not mentioned, not apologised for, and not visible in the nav.** If a cut requires code changes, that is **F-04**, capped at 10 minutes.

### 5.3 Fallback plan if AI calls fail live

1. **Nothing to do.** Alerts, risk scores, RUL, the ledger and the audit chain **never touched the LLM**. The deterministic template diagnosis renders with an amber "Offline diagnosis mode" banner and a `deterministic-template` stamp.
2. Gateway *slow* rather than down → the circuit breaker opens after 4 failures (60s cooldown). Wait it out, or hit the **LLM-offline toggle on the Trust screen** deliberately.
3. **Rehearsed:** F-03 run #2 was executed with the network physically disabled. If it happens live, say so: *"That's the offline path — it's the same demo, deterministically narrated."*
4. Cached diagnoses (15-min TTL) replay in ~5ms, so any diagnosis already shown once is instant on re-open.
5. **Last resort:** `data/DEMO_NOTES.md` documents the expected output of each planted scenario — narrate from it against a static screen rather than debugging on stage.

### 5.4 Judge-facing demo script *(F-05)*

> **Measured run record (F-05):** PUMP-07 crossed τ=0.420000 at 52.3s of the 600× replay and the
> live alert was observed at 56.1s in both stopwatch takes. The floor
> showed risk 62/100, 49.6% failure probability in 24h, and 6.2 days RUL. The offline diagnosis
> persisted one coordinator stage at 4.0ms; absent stages remain unfilled. The live ledger returned
> Reactive 19,200 min, Fixed-schedule 21,900 min, Predictive 10,230 min, a 46.72% reduction and
> £385,058 avoided. The repeatable command is `python scripts/demo_rehearsal.py --interactive`; it
> fetches a fresh `GET /runs/{trace_id}` and resets the replay clock before every take.

**Frame (15s):** *"Plants lose money to unexpected failures, and fault diagnosis needs an expert who isn't there at 3am. Every number you're about to see is computed by a deterministic model. The AI only explains numbers that already exist, and it has to cite its evidence."*

**Moment #1 — "It sees it coming, and it shows its work." (≤30s · Live Floor → Diagnosis)**
1. Floor is calm and green; 15 tiles, live sparklines. The replay cue is measured at 52.3s.
2. PUMP-07's sparkline climbs; the risk ring sweeps amber → red; select the tile showing risk 62/100,
   49.6% failure probability and 6.2 days RUL.
3. One click → Diagnosis. The persisted trace shows coordinator 3.0ms in offline fallback; no absent
   stage is narrated.
4. Hover `[S2]` → **the chart below scrubs and shades the exact 6-hour window the model reacted to.**
   Hover `[M3]` → “Drive-end bearing replacement”, dated 2025-09-27.
5. **Line:** *"Not a black box. Every sentence is anchored to data you can see."*

**Moment #2 — "The 20% is computed, not claimed." (≤20s · Downtime Ledger)**
1. Three bars race up: Reactive 19,200 min · Fixed-schedule 21,900 min · Predictive 10,230 min.
2. Count-up headline resolves to **46.72% downtime reduction / £385,058 avoided**, with the 20% target line drawn.
3. Toggle "what the model missed" — 21 failures the predictor did **not** catch, with their cost included.
4. **Line:** *"Same event stream, three policies, and we charge ourselves a full planned window for every false positive. This is the pytest gate, not a slide."*

**Closer (20s) — pick two, by audience:**
- **Safety/governance:** approve an A0 recommendation → evidence drawer → work order lands in the ERP → `GET /audit/verify` shows the intact hash chain. *"No LLM holds a write tool."*
- **Resilience:** set `ERP_FAULT_MODE=flaky` or flip LLM-offline **live** → the dashboard degrades visibly and keeps working. *"Resilience you can watch beats resilience described."*
- **Rigour:** run `pytest` live — ~60 tests, both KPI gates green.
- **Honesty (if asked "isn't the synthetic data rigged?"):** show the held-out split — 3 machines never seen in training, split by time **and** machine — plus the `sensor_drift` trap on PRESS-03 that the system correctly *declines* to call a mechanical fault.

### 5.5 Pre-demo hard checks — do these at H11.75

- [ ] Laptop on mains; sleep/screensaver disabled; notifications off.
- [ ] Browser zoom 100%, window 1280×800, one tab per screen pre-opened and logged in.
- [ ] **Replay clock reset to t0** so PUMP-07 fires on cue — not mid-fault from a rehearsal.
- [ ] `python scripts/seed.py` re-run clean (**no stale `is_simulated` rows** from D-03 rehearsal testing).
- [ ] Terminal font size up; `logs/app.jsonl` tail ready in a spare tab in case a judge asks for a trace.
- [ ] **Feature flags in `config.yaml` match the §5.2 status board exactly.**

---

## 6. Traceability Check

### 6.1 spec.md success metrics (§13) → task IDs

| spec.md metric | Target | Task(s) that make it true |
|---|---|---|
| Fault prediction accuracy | ≥85% balanced | A-03, B-04, B-05, A-04, **A-05 (gate)** |
| Precision / recall @ τ | ≥0.80 / ≥0.80 | A-04 (τ tuning), A-05 |
| Downtime reduction | ≥20% | A-08, **A-09 (gate)**, D-01 (UI) |
| Fault-mode top-1 accuracy | ≥70% | A-04, B-14 |
| Detection lead time | ≥24h median | A-04, B-14, D-03 (scoreboard) |
| RUL interval coverage | 75–95% | A-04, B-14 |
| False-alarm rate | ≤1/machine/30d | A-04 (precision floor), A-08 |
| Rehearsal detection rate | ≥90% | D-03, D-04 |
| Citation coverage | 100% | E-01, B-09, B-14 |
| Numeric grounding first-try | ≥95% | E-01, B-09, B-14 |
| Hallucination adversarial set | 12/12 | B-14, E-01 |
| Injection detection | 8/8 quarantined | B-06 (planted), E-04, B-14 |
| Facts/assumptions separation | 100% | B-09, E-01, C-04 |
| Diagnosis p95 latency | ≤6s | B-12, B-14 (+ Risk P2 fallback) |
| Tokens per diagnosis | ≤3,500, ≤2 calls *(tokens not £ — internal gateway, no billed rate)* | B-03, B-09, B-10, B-14 |
| Mutating endpoints reject wrong role | 100% | E-02 |
| ERP writes without approval | 0 | B-13 |
| Audit chain verification | intact + tamper detected | E-03, D-06 |
| Secrets in git/logs/responses | 0 | A-01, E-05 |
| PII reaching the gateway | 0 | E-04 |
| Endpoints without auth | 0 except `/health`, `/auth/login` | E-02, A-10 |
| Window read + preprocess + features | ≤100ms | B-04, B-05, **B-15** |
| Retrieval over 480 WOs | ≤5ms | B-07 |
| Named scaling seams as protocols | 6/6 | A-07a (`SensorSource`), B-02 (`DB_URL`), B-07 (`VectorStore`), **B-15 (`Predictor`)**, B-01 (`ERPClient`), **B-15 (`TSDBClient`)** — *the last two were previously credited to A-11 and A-07, neither of which builds them* |
| Demo runs with network disabled | Yes | B-03, B-07, B-09, **F-03 run #2** |
| Reliability: 200 flaky runs | 0 unhandled | B-14 |
| Failure modes with degraded behaviour | 13/13 | A-07a, A-11, **B-15**, B-03, B-10, B-13, C-08, E-05, C-05 |
| Test count / pass rate | ~55, 100% | F-01 |
| Any run reconstructable from a trace ID | Yes | B-08, B-12, A-07a |
| Magic numbers in business logic | 0 | A-01, A-06 |
| Fresh clone → demo | ≤4 commands, ≤3 min | **A-13**, F-03 |
| Documentation deliverables | 7/7 | F-02 (5 authored) + A-03 (`DEMO_NOTES.md`) + B-14 (`REPORT.md`) + A-02 (OpenAPI) |
| Clicks alert → cited diagnosis | 1 | C-02, C-04 |
| Clicks diagnosis claim → raw data | 1 | C-04 |
| Demo moment #1 / #2 runtime | ≤30s / ≤20s | C-02+C-04+C-06+C-09 / D-01, **F-05** |
| Blank states in the app | 0 | C-08 |
| WCAG AA, no colour-only status, reduced-motion | 100% / yes / yes | C-01, C-02, C-09 |

### 6.2 plan.md workstreams → task IDs

**Counting convention:** a task is counted under the workstream that *builds* it, exactly as in §3.
Task **ID prefixes do not equal workstreams** — A-05/A-09/A-15 are Integration-QA tasks with `A-`
IDs, and B-01/B-02/B-04/B-05/B-06/B-15 are Backend tasks with `B-` IDs, because IDs were inherited
verbatim from plan.md §5 and are deliberately stable. The counts below therefore reconcile to §3's
**48**; they previously summed to 48 against a stated 45 by double-counting those tasks.

| plan.md workstream | Represented by | MVP tasks | Stretch tasks |
|---|---|---|---|
| **A — Backend/API & data layer** | A-01…A-04, A-06, **A-07a/b**, A-08, A-10, A-11, **A-13**, B-01, B-02, B-04, B-05, B-06, **B-15** | 17 | A-12 |
| **B — AI/LLM & agents** | B-03, B-07…B-13 | 8 | — |
| **C — Frontend & Experience** | C-01…C-08, **C-10** | 9 | C-09 |
| **D — Standout features** | D-01…D-06 | 0 *(by design — zero inbound edges from P0)* | 6 |
| **E — Security & infra hardening** | E-01…E-05 | 5 | E-06 |
| **F — Integration, QA, demo prep** | A-05, **A-15**, A-09, B-14, F-01…F-05 | 9 | — |
| | **Total** | **48** | **8** |

### 6.3 spec.md core user flows → task IDs

| Flow (spec §4.1 / §4.7) | Tasks |
|---|---|
| Line operator: "is my machine OK right now?" | C-01, C-02, A-07a, C-10 |
| Technician: "what's wrong, what part, what's the evidence?" | C-03, C-04, C-05, C-06, B-09, B-10, B-11, E-01 |
| Planner: "what do I schedule, what does it cost, what do I approve?" | C-07, B-13, E-02, A-08, D-01 |
| Data-integrity path (CONV-14, refuse rather than guess) | B-04, A-06, B-11, C-08 |
| False-positive trap (PRESS-03, decline a mechanical fault) | A-03, B-06, A-06, B-10 |
| Conflicting evidence (CNC-11, state the disagreement) | A-03, B-06, A-06, B-09, C-04 |
| Offline / degraded operation | B-03, B-09, B-10, A-11, C-08, F-03 |

### 6.4 Constraint audit

| Constraint | Status |
|---|---|
| **No Docker / no containers** | ✅ Zero Dockerfiles, zero compose files, zero container references in any task. |
| **No admin-rights installs** | ✅ `python -m venv` + pip wheels (all Windows prebuilt, no compiler) · `npm install` into `ui/node_modules` · SQLite ships in CPython · DuckDB/PyArrow are in-process wheels · ports 8000/8001/5173 are unprivileged and loopback-bound. |
| **No system services / daemons** | ✅ Two `uvicorn` processes started by `subprocess.Popen` (A-13). No service registration, no PATH modification, no registry writes. |
| **12-hour window** | ✅ Every task carries its plan.md hour allocation. Three unbudgeted gaps flagged in §0 with named sources of time and a named first-cut (A-10). |
| **Core flow non-negotiable** | ✅ 48 MVP tasks constitute the §3 gate; all 9 stretch tasks (D-01…D-06, C-09, A-12, E-06) have zero inbound edges from MVP code. |
| **Frontend polish + Standout kept distinct** | ✅ C-09 (animation) and D-01…D-06 (standout) are separately tagged tasks, never folded into backend work — by design, so they can be **deleted, not unpicked**. |

---

**End of tasks.md.** 48 MVP tasks produce a working, demoable product; the 8-entry stretch queue in §4
maximises judging impact in whatever time remains, cut strictly from the bottom — **and cut at ⛳H8.5,
because the real buffer is 0:40, not 2:30.**
