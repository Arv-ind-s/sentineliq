# plan.md — **SentinelPdM** 12-Hour Execution Plan

> Derived from [spec.md](spec.md) §11 (12-Hour Build Plan). **No re-architecture.** Every duration below
> traces to a spec.md task ID; where spec.md's own arithmetic does not close, it is **flagged in
> §0 Traceability Deltas** and resolved by reassignment only — no task is added, removed, or re-scoped.
>
> **Team:** R1 backend/ML · R2 agents/LLM/integration · R3 frontend. Hours are elapsed wall-clock.
> **Hard constraints:** no Docker, no containers, no admin installs, no system services. Every tool below
> is a PyPI wheel into a user-space venv, an npm package into `ui/node_modules`, an embedded file DB, or
> an API-key HTTPS call.

---

## 0. Traceability Deltas (read first)

Five mismatches between spec.md §11's task durations and its own phase windows. **No schedule change —
resolved by reassignment inside the same window.** Each is called out again at the point of use.

| # | Spec.md says | Actual | Resolution (this plan) |
|---|---|---|---|
| **D1** | Phase 1 (H1.0→H3.5, 2:30 window) assigns R1 tasks 1.1–1.6 = **3:30** | R1 over by **1:00** | 1.2 (defect injection) **merged into 1.1** as one generator artifact (it is the same file); 1.4 (`preprocess.py` + `features.py` + tests, 0:45) **reassigned R1 → R2**, who has 1:30 free. R1 → 2:20, R2 → 2:10. |
| **D2** | Phase 2 (H3.5→H6.5, 3:00) assigns R2 tasks 2.1,2.2,2.3,2.5,2.6,2.7 = **3:20** | R2 over by **0:20** | 2.6 (`guardrails.py` + Guardrail Agent + tests, 0:40) **reassigned R2 → R1** (deterministic code, no LLM — spec §3.8.4; R1 has 1:30 free). R2 → 2:40, R1 → 2:10. |
| **D3** | Phase 4 (H8.5→H10.0, 1:30) assigns R3 tasks 4.3,4.4,4.5 = **1:55** | R3 over by **0:25** | 4.5 (Trust & Evals screen, 0:30) **reassigned R3 → R2**, who authors the eval JSON it renders (4.2). R3 → 1:25, R2 → 1:15. |
| **D4** | Phase 5 assigns 5.1 to "R2+R3" **and** 5.2 (0:45 hard box) to R3, inside a 1:00 window | R3 double-booked by **0:30** | 5.1 split: **R2 owns the rehearsal backend** (inject/score/reset endpoints). R3 owns **either** 5.2 (3D) **or** the rehearsal UI — never both. Decided at the H10.0 checkpoint. |
| **D5** | §11 footer: "Critical path (0,1,2,3,6) = 9:35, leaving 2:25 slack" | Phases 0+1+2+3+6 = **9:30**; Phases 4+5 = **2:30** | Arithmetic slip of 5 min. Plan uses **9:30 / 2:30** *(both later superseded by D7–D9: the real figures are **11:20 / 0:40** — see §0.1 and §3.2)*. |
| **D6** | Two mandated artifacts have **no §11 line item**: SQLAlchemy 13-table schema (Appendix C) and the 2D SVG machine schematic (§4.5, "built first", ships if 3D is cut) | Unbudgeted **~0:50** | Schema (0:25) → R2, Phase 0 (R2 has 0:30 free there). SVG schematic (0:25) → R3, Phase 2 (R3 has 1:50 free there). |

### 0.1 Adversarial-review deltas (D7–D11) — **these change the schedule, not just its owners**

D1–D6 were reassignments inside a fixed budget. The five below are different: an adversarial pass
found work that does not exist anywhere and estimates that do not survive contact. They are resolved
by **adding time and cutting stretch**, not by reshuffling, and the buffer is restated honestly in
§3.2 as a result.

| # | Finding | Cost | Resolution |
|---|---|---|---|
| **D7** | **`core/predict.py` and `core/tsdb.py` have no task in any document.** `predict.py` is the serving path the ≥85% KPI is measured through; `tsdb.py` is the DuckDB seam every windowed read depends on. Both were assumed to be side effects of `train.py` or the Sensor Agent. The `Predictor` protocol was even credited to A-11, the *fallback* predictor. | **+0:30** | **B-15** added, MVP, 0:30, **R2**, H2.6→H3.1 (sits beside `preprocess`/`features`, which R2 already owns via D1). Everything downstream of it shifts ~0:20; the H6.5 gate still closes — see §3. |
| **D8** | **A-03's demo verification is unsatisfiable in build order.** It asserts PUMP-07 crosses τ, but τ does not exist until A-04 (H3.2) and `core/` doesn't exist at H2.5. Worse, the Risk-1 horizon fallback re-tunes τ and **moves the moment PUMP-07 fires**, with nobody owning re-verification. | **+0:15** | Verification split (spec §3.1): A-03 keeps signal-level `verify_signal()`; **A-15** added, MVP, 0:15, R1, H3.4→H3.65 — `tests/test_demo_integrity.py`, model-dependent, **re-run after every retrain**. It joins the ⛳H3.5 gate. |
| **D9** | **A-07 (0:50) and C-08 (1:00) are the two worst estimates in the plan.** A-07 was the entire ingest stack *plus* ~25 real routes *plus* SSE *plus* alert generation. C-08 was the whole stub→real cutover of 4 screens plus SSE reconnect plus a 6-route blank-state sweep, in two half-hour slices. | **+1:10** | **A-07 split**: A-07a (0:50, gate-blocking — ingest/StreamClock/DLQ/alerts/SSE + the routes H6.5 tests) and **A-07b** (0:40, not gate-blocking — remaining Appendix-B routes, may land after H6.5). **C-08 re-estimated 1:00 → 1:30.** |
| **D10** | **The session cookie cannot reach the API in a browser.** `SameSite=Strict` + UI on `localhost:5173` + API on `127.0.0.1:8000` is cross-*site*: the cookie is never sent, login 401s, and `EventSource` fails the same way. C-10 budgeted 0:15 for this, at H7.0. | **+0:00** | Design fix, not a time fix: **Vite dev proxy makes the browser same-origin with the API** (spec §7.1); the origin rule is **frozen inside A-02** so no screen hardcodes the other host. Costs one config block in C-01. |
| **D11** | **KPI #2 had no contingency.** KPI #1 has a detailed pre-decided fallback; the downtime gate had none, and it sits at H8.5 with 3 hours left. It is also **algebraically entailed** by KPI #1 (spec §3.12), which a judge can derive in a minute. | **+0:00** | Risk **13** added in §6 with a 20-minute pre-decided lever ladder. The entailment is **disclosed** in `eval/REPORT.md` and `MODEL_CARD.md` rather than left to be discovered. |

**Net: +1:55 of MVP work.** Paid for out of the Phase 4–5 block, which is why §3.2 now reports a
**0:40** buffer rather than 2:30, and why the stretch cut moves from H10.0 to **⛳H8.5**.

---

## 1. Scope Recap

**Build:** SentinelPdM — a five-agent predictive-maintenance and fault-diagnosis system for 15 synthetic
manufacturing machines. A seeded generator produces 60 days × 8 sensor channels (2.59M readings, **60 labelled
fault events + 12 drift intervals**, 480 ERP work orders) with deliberate noise, clock skew, dropouts,
duplicates, unit mismatch and a frozen sensor. Preprocessing cleans and time-aligns it; scikit-learn models emit `P(failure ≤ 24h)`, an RUL
interval and a fault-mode distribution; `fusion.py` deterministically computes risk, band, confidence and
autonomy level. **Only then** does the Coordinator LLM narrate — never numerically. A deterministic Guardrail
Agent enforces numeric grounding, citation resolution, schema, an unsafe-action denylist and injection
quarantine; an autonomy ladder (A3→A0) routes to named human approvers; the ERP Action Agent is the sole
writer and requires an approval row in code. A React dashboard streams it over SSE across six screens.

**MVP boundary (spec §11 "MVP vs stretch"):** synthetic data with fault events · preprocessing · trained
models ≥85% balanced accuracy · all 5 agents + orchestrator · guardrails · HITL approval ladder · live HTTP
ERP integration · auth/RBAC/audit ledger · SSE dashboard with **Live Floor, Machine Detail, Diagnosis,
Schedule** · both KPI pytest gates · test suite · docs.
**Everything else is stretch:** Downtime Ledger *UI* (the number survives as a table), Trust screen,
Rehearsal Console, 3D twin, command palette, the full animation pass.

**Scope-creep test:** if a proposed task is not in the MVP list above and it is before H10.0, it does not
get built.

---

## 2. Workstreams

| ID | Workstream | Owner | Depends on | New tooling — Docker-free / admin-free confirmation |
|---|---|---|---|---|
| **A** | **Backend/API & data layer** — config, ORM schema, generator, preprocess-consumers, StreamClock/SSE, routes, mock ERP | R1 (R2 assists) | — (originates the critical path) | ✅ Python 3.12 venv (`python -m venv`), pip wheels: FastAPI 0.115, uvicorn 0.32, SQLAlchemy 2.0.36, Pydantic 2.9, DuckDB 1.1.3, PyArrow 17, NumPy 2.1, pandas 2.2.3. Storage = SQLite (ships in CPython) + Parquet files. Ports 8000/8001, loopback, unprivileged. **No server process to install.** |
| **B** | **AI/LLM & agents** — LLM gateway client, prompts v1, 5 agents, orchestrator, retrieval, fallback/template path | R2 | A (schema + contract), ML artifacts from A | ✅ `langchain-openai` 0.2 + `openai` (pure-Python wheels) as the **OpenAI-compatible** path to `https://llm-gateway.example.com`, `httpx` 0.27 as the zero-SDK alternate adapter — both behind one `LLMClient` protocol. Model: `openai-compatible-chat`, API key in `.env` only. scikit-learn 1.5.2 TF-IDF is the **default** embedding path (no gateway embeddings model confirmed). `tenacity` 9.0 for retries. **No local weights, no GPU, no vector DB process, no admin install.** |
| **C** | **Frontend & Experience** — 6 screens, SSE wiring, Recharts, citation-chip scrubbing, agent-trace timeline, Framer Motion pass, 2D SVG schematic | R3 | A2 API contract freeze **only** (builds on stubs from H1.0) | ✅ Node 22.15 already user-space; `npm install` writes only to `ui/node_modules`. Vite 6, React 19, TS 5.7, Tailwind 4 (`@tailwindcss/vite`), TanStack Query 5, Recharts 2.13, Framer Motion 11.11, react-router-dom 7, lucide-react. Prod = `vite build` → served by FastAPI `StaticFiles`. **No web server install.** |
| **D** | **Standout features** — ⭐Downtime Ledger UI + τ slider, ⭐Fault Rehearsal Console, ⭐Evidence/audit ledger *viewer*, 3D digital twin | R2 + R3 | Full MVP flow green at H10.0 | ✅ `three` 0.169 + `@react-three/fiber` 8.17 + `drei` 9.114 — npm only, **lazy `React.lazy` chunk, feature-flagged off** (`ui.features.digital_twin`), WebGL capability check + error boundary. Rehearsal is npm/pip-free (reuses existing stack). |
| **E** | **Security & infra hardening** — JWT/argon2/RBAC, audit chain, guardrails, redact + injection scan, rate limits, headers, CORS, error envelope | R1 (R2 on LLM-specific controls) | A (routes exist), B (agent outputs to guard) | ✅ pip wheels: `argon2-cffi` 23.1, `PyJWT` 2.9, `python-dotenv` 1.0.1. Rate limiting + security headers are **in-process middleware** — no Redis, no WAF, no proxy, no admin. |
| **F** | **Integration, QA, demo prep** — KPI gates, eval harness, full suite, docs, clean-machine + offline rehearsal | All | Everything | ✅ `pytest` 8.3, plain Python scripts. `scripts/run_all.py` uses `subprocess.Popen` — no orchestration tool, no service registration. |

**Never-blocking rule:** **D** has zero inbound edges from A/B/C/E/F. Nothing in the MVP imports anything in
D. Cutting D entirely at H10.0 requires **zero** code changes elsewhere (3D: flag stays off, SVG ships;
Rehearsal: self-contained screen behind `features.rehearsal`).

---

## 3. Hour-by-Hour Timeline

Legend: **⛳** checkpoint · **🔒** critical path · **⭐** standout/stretch · `[id]` = spec.md §11 task.

| Hour | R1 — Backend/Data (A, E) | R2 — Agents/LLM (B, E) | R3 — Frontend (C) | Gate at end of hour |
|---|---|---|---|---|
| **H0.0–1.0** 🔒 | `[0.1]` Repo scaffold, venv, `requirements.txt`, `config.yaml`, `.env.example`, `core/config.py` (0:25) → `[0.2]` **Freeze API contract**: `api/schemas.py` + Appendix-B stub routes returning fixture JSON, with R3 (0:25) | `[0.4]` `erp/` mock service skeleton + fixture data on :8001 (0:30) → **D6:** `core/db.py` (SQLite WAL, `busy_timeout=5000`) + `core/models.py` 13 tables (0:25) | `[0.2]` Contract freeze **with R1** (0:25) → `[0.3]` Vite+React+TS+Tailwind scaffold, `theme.css` tokens, layout shell, TanStack Query client, router (0:35) | **Contract frozen.** `GET /machines` returns fixture JSON; `ui` renders shell against it. R3 is now unblocked for 11 hours. |
| **H1.0–2.0** 🔒 | `[1.1+1.2]` `data/generate.py`: 15 machines, 7,200 cycles, 8 channels, 6 fault signatures, 34 labelled events **with defects injected inline** (skew, 0.4% spikes, 1.1% dropouts, dupes, °F, frozen channel). **Emit a 1-machine sample partition by H1.3 so R2 is unblocked.** (1:30 total, runs to H2.5) | `[1.8]` `llm/client.py` — `LLMClient` protocol + LangChain adapter, timeout/retry/circuit-breaker, **LLM gateway smoke test (`llm-gateway.example.com` × the configured model) — the H1.4 go/no-go on TLS, key, and model name** (0:25) → **D1:** `[1.4a]` `core/preprocess.py`: skew→UTC, dedupe, unit harmonisation, 60s resample, Hampel despike, gap flags, frozen detect, QualityReport (0:25) | `[1.9]` **Live Floor**: 15 tiles, 60-pt sparklines, conic risk rings, status ramp, alert feed, KPI header — on stubs (1:20, runs to H2.4) | **LLM gateway reachable with the configured model, or offline path confirmed and announced to the team.** Sample Parquet exists. |
| **H2.0–3.0** 🔒 | …generator completes H2.5 → `[1.5]` `scripts/train.py`: label windows (24h horizon), train HistGB + RF + 3× quantile GBR, isotonic calibration, tune τ (precision ≥0.80), write `models/MANIFEST.json` **including τ — never back into `config.yaml`** (0:35, H2.6→3.2) | **D1:** `[1.4b]` `core/features.py` 41 features fv1 + unit tests for preprocess & features (0:20, →H2.6) → **D7:** `[B-15]` `core/predict.py` (`Predictor`, sha256 + fv assert, τ from MANIFEST) + `core/tsdb.py` (`TSDBClient`) (0:30, →H3.1) → `[1.3]` ERP work-order history: 480 WOs, fault codes, technician free text, load `erp/erp.db` (0:20, →H3.3) | `[1.10]` **Machine Detail**: synchronised multi-channel Recharts, fault-onset markers, work-order rail, quality-flag ribbon (0:50, H2.4→3.25) | Features contract `fv1` stamped; **serving path exists**; training data ready. |
| **H3.0–4.0** 🔒 | `[1.6]` **KPI GATE #1** — per-event detection **and** balanced accuracy (0:15, →H3.4) → **D8:** `[A-15]` `tests/test_demo_integrity.py` — PUMP-07 crosses τ in the 40–75 s window, PRESS-03/CNC-11/CONV-14 behave (0:15, →H3.65) — **⛳ H3.5** → `[2.4]` `core/fusion.py` + `explain()` + 5 hard rules + autonomy ladder + tests (0:40, →H4.35) | `[1.7]` `core/retrieval.py` — TF-IDF path first (works offline), `NumpyVectorStore`, hybrid re-rank, MTBF/fault-code SQL aggregation (0:35, H3.3→3.9) → `[2.1]` `agents/base.py` protocol, `AgentResult`, retry/backoff, trace emission, `agent_runs` writes (0:25, →H4.3) | Finish Machine Detail → **⛳ H3.5** → `[2.9]` **Diagnosis screen** begins: confidence gauge, reasoning steps, Facts/Assumptions/Limits blocks, citation chips (1:10, H3.5→4.7) | **⛳ H3.5 CHECKPOINT — MODEL GATE.** Models trained, both accuracy metrics green, **demo-integrity test green**, Live Floor + Machine Detail rendering on fixtures. **If accuracy <85%: extend horizon to 48h and/or add lag features before anything else** (Risk 1) — **and re-run A-15, because re-tuning τ moves the moment PUMP-07 fires.** The KPI is not negotiable; the horizon is. |
| **H4.0–5.0** 🔒 | …fusion completes H4.35 → **D2:** `[2.6]` `core/guardrails.py` + Guardrail Agent — **tests written first**, including **the demo's own alert and diagnosis strings as passing fixtures** and unit/scale normalisation (0:40, H4.35→5.05) | `[2.2]` Sensor Agent — 100% deterministic, evidence tags `[S1..S5]` bound to `(channel,t0,t1,stat,value)` (0:25, H4.3→4.7) → `[2.3]` Maintenance-Log Agent — live ERP HTTP + retrieval + strict-JSON summary + circuit breaker → cache fallback (0:40, →H5.4) | …Diagnosis screen → **D6:** 2D SVG machine schematic with colour-coded hotspots, rendered from `components[]` (0:25, H4.7→5.1) | Guardrail tests exist **before** the Coordinator does — and they prove the guard **accepts** the demo, not just that it rejects fakes. |
| **H5.0–6.0** 🔒 | **D9:** `[2.8a]` `core/ingest.py` + `StreamClock` (600× from `t0`=day 56) + bounded queues + DLQ + **alert generation** + SSE `/stream/events` + the routes the H6.5 gate tests (0:50, H5.05→5.95) → `[2.8b]` remaining Appendix-B routes (0:40, →H6.6, **not gate-blocking**) | `[2.5]` Coordinator Agent + `agents/prompts/v1/` + strict JSON schema + deterministic template fallback (0:40, H5.4→6.05) → `[2.7]` `agents/orchestrator.py` `DiagnosisRun` state machine: FANOUT ∥ → FUSE → DIAGNOSE → REFLECT → ROUTE (0:30, →H6.35) | Agent-trace timeline component (nodes, real latencies, amber retry fork) (0:30, H5.1→5.6) → **D9:** `[C-08]` cutover to real SSE + `/machines` begins (0:35, H5.95→6.5) | SSE + alerts live. UI on real endpoints for the gate path. |
| **H6.0–7.0** 🔒 | …`[2.8b]` completes H6.6 → **⛳ H6.5** → `[3.1]` JWT + argon2id + `require_role()` RBAC + 4 seeded users + 403 tests on every mutating endpoint + **the exact unauthenticated-route set asserted** (0:45, →H7.35) | …orchestrator completes H6.35 → **⛳ H6.5** → `[3.3]` Approval workflow: `approvals` table, approve/reject routes (reason mandatory), **ERP Action Agent** with in-code approval precondition + `Idempotency-Key` + `pending_erp_sync` (0:40) | …cutover pass → **⛳ H6.5** → `[3.5]` **Schedule & Approvals** screen: 14-day calendar, approval queue with autonomy badges, evidence drawer with typed-reason reject (0:50, H6.5→7.35) | **⛳ H6.5 — THE GO/NO-GO (MVP CUTLINE).** End-to-end must work live: **stream → prediction → alert → diagnosis → dashboard**. **If red: all stretch work stops immediately**; H6.5–H8.0 goes to finishing it, and Phase 4/5 is the sacrificial budget — now **0:40**, not 2:30, so this gate is less forgiving than it was. |
| **H7.0–8.0** 🔒 | …auth → `[3.2]` `core/audit.py` hash-chained ledger + `GET /audit/verify` + tamper test (0:25, →H8.0) → `[3.6]` Rate limits (120/10/60 per min), security headers, error envelope, CORS, body-size caps (0:20, →H8.2) | `[3.4]` `redact()` + `injection_scan()` + `<untrusted_document>` wrapping + quarantine + tests — **incl. the 3 blind payloads and the 2 deliberately-passing notes** (0:30) → **pull-forward:** `[4.2]` eval harness scaffold (0:20) | …Schedule screen → **D9:** `[C-08]` cont. — SSE reconnect + polling fallback, health strip, skeletons, zero-blank-state sweep of all 6 routes (0:55, H7.35→8.3) → `[C-10]` login + route guard (0:15) | Every mutating endpoint 403s the wrong role. Audit chain verifies. **Login works in a real browser** (same-origin proxy, D10). |
| **H8.0–9.0** | `[4.1]` `core/downtime_sim.py` 3 policies (reactive / fixed-30d / predictive), false positives charged a 90-min window, £ from machine cost + ERP parts/labour → **KPI GATE #2** `test_downtime_reduction_at_least_20` (0:45, H8.2→8.95) — **⛳ H8.5 falls inside this task; if the gate is red, Risk 13's 20-minute ladder starts immediately** | …`[4.2]` `eval/harness.py`: groundedness, 12 adversarial bundles, the injection set, latency/tokens, 200-run reliability with `ERP_FAULT_MODE=flaky` + LLM off (0:45 total) | ⭐`[4.3]` **Downtime Ledger screen**: three racing bars (Framer springs), count-up headline, 20% target line, "what the model missed" toggle, **τ slider** re-running all 3 policies live (0:50, H8.3→9.15) | **⛳ H8.5 — THE STRETCH CUT (moved up from H10.0, §3.2).** Security + HITL complete; both KPI gates are the only remaining P0 risk. **Stretch below rank 3 is cut here by default** unless ≥1:00 of float is genuinely unspent. |
| **H9.0–10.0** | KPI gate #2 green → floating support: fix whatever H6.5–H8.5 surfaced; `GET /health` per-dependency strip; `GET /metrics` counters (0:40) | **D3:** `[4.5]` **Trust & Evals** screen — accuracy vs 85% line, reduction vs 20% line, guardrail rejection log, injection quarantine log, latency/cost/tokens, DLQ, version stamps (0:30) | `[4.4]` **Framer Motion pass**: alert `AnimatePresence` entrance, risk-ring `useSpring`, count-ups, `layoutId` tile→detail morph, agent-trace `pathLength` draw-on, `MotionConfig` reduced-motion (0:35) | **⛳ H10.0 — THE CUT LINE.** See §3.1. |
| **H10.0–11.0** ⭐ | Floating: bug triage from the H10.0 sweep; `scripts/replay.py`; prune/backup scripts | ⭐`[5.1a]` **Rehearsal backend**: `POST /rehearsal/inject` (writes `is_simulated=true` rows through the *identical* path), `/reset` (state-restore test), `/scoreboard` (0:45) | **ONE OF (D4):** ⭐`[5.2]` 3D digital twin — r3f low-poly assembly, per-component risk tint, pulse + leader line, `OrbitControls`, lazy chunk, WebGL guard — **HARD 45-MIN BOX** · **OR** ⭐`[5.1b]` Rehearsal Console UI | **⛳ H11.0:** hands off all stretch. Anything unfinished is flagged off, not debugged. |
| **H11.0–12.0** 🔒 | `[6.1]` Full suite green + `python scripts/evaluate.py` → `eval/REPORT.md` (0:20, all) · `[6.2]` `README.md`, `DEPLOYMENT.md`, `USER_GUIDE.md`, `docs/AGENTS.md`, `docs/MODEL_CARD.md` (0:20, R1+R2) · `[6.3]` **Clean-machine rehearsal ×2 — the second with the network physically disabled** (0:20, all) | ← same | ← same, plus demo-script dry run of both 30-second moments | **H12.0 SHIP.** |

### 3.1 ⛳ Cut Line — pre-agreed priority order (spec §11)

**The first assessment is at ⛳H8.5, not H10.0** (§3.2: the real buffer is 0:40, so waiting until
H10.0 to discover an overrun leaves no time to act on it). H10.0 remains a second, harder checkpoint.
Assess honestly and **cut from the bottom up**. No debate at either point; the order is decided now.

| Priority | Item | Cut rule |
|---|---|---|
| **P0 — never cut** | Stream → predict → alert → diagnose → approve → ERP write · both KPI gates · auth/RBAC/audit · guardrails · Live Floor + Machine Detail + Diagnosis screens | **If P0 is incomplete at H10.0, everything below is cancelled** and H10.0–H12.0 goes to P0. |
| **P1** | Downtime Ledger UI · Schedule screen · Trust screen | Cut the animation, keep the data. The 20% survives as a plain table + `eval/REPORT.md`. |
| **P2** | ⭐ Fault Rehearsal Console | Cut entirely — self-contained screen behind `features.rehearsal`. |
| **P3** | ⭐ 3D digital twin | `ui.features.digital_twin` stays `false`; the 2D SVG schematic (built H4.7) ships. **Zero other changes.** |
| **P4** | Command palette · light mode · extra micro-interactions | Cut without discussion. |

### 3.2 Buffer accounting — **restated honestly (this replaces a 2:30 figure that was never real)**

| Block | Duration | Nature |
|---|---|---|
| Critical path — Phases 0,1,2,3,6, as originally scoped | **9:30** | Non-negotiable (D5: spec said 9:35). |
| **D7–D9 additions to the critical path** | **+1:55** | B-15 `predict.py`/`tsdb.py` 0:30 · A-15 demo-integrity test 0:15 · A-07b route completion 0:40 · C-08 re-estimate 0:30. **None of this is new scope — it is work that always had to happen and was not written down.** |
| **Revised critical path** | **11:20** | |
| **Remaining buffer** | **0:40** | This is the real number. |
| H11.0–H12.0 integration + rehearsal | **1:00, never zero, never cut** | Test suite + docs + **two full clean-machine rehearsals, one offline**. Protected even if P0 is incomplete — a rehearsed partial demo beats an unrehearsed complete one. **Never the source of borrowed time.** |

**Per-builder load at zero slippage** (MVP only, excluding the shared Phase-6 hour):

| | MVP work | Day | Float |
|---|---|---|---|
| **R1** — backend/data + security | 8:50 | 12:00 | ~1:10 after Phase 6 |
| **R2** — agents/LLM/integration | 8:10 | 12:00 | ~1:50 after Phase 6 |
| **R3** — frontend | 7:55 | 12:00 | ~2:05 after Phase 6 |

**What this means, said plainly.** Each builder's MVP load consumes ~70–75% of the day *if every one
of 47 tasks lands exactly on estimate*, which will not happen. The float in the right-hand column is
not stretch budget — **it is the debugging allowance**, and WS-D competes with it directly. Hence the
two structural changes: the stretch cut moves to **⛳H8.5**, and by default only the top three stretch
items (D-01 Ledger UI, C-09 animation, D-02 Trust screen — 1:55 total, all R3/R2) are attempted at
all. Everything below rank 3 is opt-in, not opt-out.

**The old plan claimed 2:30 of "genuine slack" while scheduling 3:40 of WS-D stretch into it.** That
was the single least defensible number in this document, and it is now corrected rather than carried.

---

## 4. Dependency Graph

```
[A-01 scaffold+config]
   ├─> [A-02 API CONTRACT FREEZE + ORIGIN DECISION] ═════════> [C-* ALL FRONTEND]  (only edge into C; cut at H1.0)
   ├─> [B-02 db.py + models.py 13 tables]
   │        ├─> [A-03 generate.py + defects] ──> [B-04 preprocess] ──> [B-05 features fv1] ──> [B-15 predict.py + tsdb.py] ──> [A-04 train.py]
   │        │            │                                                                          │
   │        │            └─> [B-06 ERP history → erp.db] ──> [B-07 retrieval TF-IDF]                │
   │        │                          ▲                              │                             │
   │        └─> [B-01 erp/ skeleton] ──┘                              │                             ▼
   │                                                                  │                    [A-05 KPI GATE #1] ─> [A-15 DEMO INTEGRITY] ⛳H3.5
   ├─> [B-03 llm/client.py + H1.4 LATENCY CALIBRATION]                │                             │
   │            │                                                     │                             ▼
   │            ▼                                                     ▼                    [A-06 fusion.py + explain]
   │     [B-09 Coordinator Agent] <──────── [B-10 MaintLog Agent] <───┘                              │
   │            ▲                                  ▲                                                │
   │            │                          [B-08 agents/base.py]                                    │
   │            │                                  │                                                │
   │            │                          [B-11 Sensor Agent] <─────────────────────────────────────┘
   │            │                                  │
   │            └──────> [E-01 guardrails + Guardrail Agent] ──> [B-12 orchestrator] ⛳H6.5 GO/NO-GO
   │                                                                     │
   └─> [A-07a ingest + StreamClock + alerts + SSE] ────> [A-07b rest of Appendix B] ──> │
                                                                          ├─> [E-02 JWT/RBAC] ─> [B-13 approvals + ERP Action Agent]
                                                                          ├─> [E-03 audit chain]
                                                                          ├─> [A-08 downtime_sim] ─> [A-09 KPI GATE #2]
                                                                          └─> [B-14 eval harness] ─> [C-08 Trust screen]
                                                                                    │
                                                                                    └─> [F-* tests, docs, rehearsal]

D-* (Ledger UI, Rehearsal, 3D twin) ──── NO INBOUND EDGES FROM P0. Deletable at H10.0 with zero refactor.
```

**Critical path (11:20):**
`A-01 → A-02 → B-02 → A-03 → B-04 → B-05 → B-15 → A-04 → A-05 → A-15 (⛳H3.5) → A-06 → E-01 → B-12 (⛳H6.5) → B-13 → A-09 → F-03 rehearsal`

**The four edges that decide the day:**
1. **`A-02` API contract freeze at H0.5.** 25 minutes that buy R3 eleven unblocked hours. If it slips, the
   frontend slips one-for-one and Risk R4 fires. **It now also freezes the browser-origin decision** (D10),
   because a screen that hardcodes `127.0.0.1:8000` cannot be authenticated.
2. **`A-03 → B-04 → B-05 → B-15 → A-04`** is strictly serial and spans two owners. Mitigated by R1 emitting
   a 1-machine sample Parquet partition at **H1.3** so R2 develops preprocessing against real data while the
   full 2.59M-row generation still runs. **`B-15` was missing from this chain entirely** — `train.py` writes
   artifacts, but nothing loaded them.
3. **`A-06 fusion` before `B-09 Coordinator`.** Non-negotiable ordering: the LLM must receive numbers that
   already exist (spec §1.2). Building the Coordinator first would invert the architecture's core rule.
4. **`A-04 → A-05 → A-15`.** The demo's 50-second moment is a *model-dependent* property, so it can only be
   verified after τ exists — and it must be re-verified after any retrain. The old plan asserted it inside
   the generator, an hour before τ was computed.

---

## 5. Task Breakdown per Workstream

`Spec` column = spec.md §11 task ID. **Blocking** = sits on the critical path.

### WS-A — Backend/API & Data Layer (R1)

| ID | Task | Spec | Est | Blocking | Tag |
|---|---|---|---|---|---|
| A-01 | Repo scaffold, venv, `requirements.txt`, `config.yaml`, `.env.example`, `core/config.py` (Pydantic Settings, fail-fast, secret-in-yaml check) | 0.1 | 0:25 | **Yes** | MVP |
| A-02 | **Freeze API contract** — `api/schemas.py` + all Appendix-B stub routes returning fixture JSON (with R3) | 0.2 | 0:25 | **Yes** | MVP |
| A-03 | `data/generate.py` — 15 machines/3 lines, 7,200 cycles, 8 channels, 6 signatures, **60 labelled events + 12 drift intervals**, **all 6 deliberate defects inline**, 4 planted demo scenarios (PUMP-07 / PRESS-03 / CNC-11 / CONV-14) positioned relative to **`t0` = day 56**, **`verify_signal()`** (model-free) that raises, `data/DEMO_NOTES.md` | 1.1+1.2 | 1:30 | **Yes** | MVP |
| A-04 | `scripts/train.py` — window labelling (24h horizon), HistGB classifier + RF fault-mode + 3× quantile GBR RUL, isotonic calibration, τ tuning at precision ≥0.80, split by time **and** 3 unseen machines, `models/MANIFEST.json` + sha256 **+ τ (never written back to `config.yaml`)** | 1.5 | 0:35 | **Yes** | MVP |
| A-05 | **KPI GATE #1** — `tests/test_kpis.py`: **per-event detection ≥85%** (headline) **and** balanced accuracy ≥0.85 on held-out, each printed with its `n` | 1.6 | 0:15 | **Yes** | MVP |
| **A-15** | **`tests/test_demo_integrity.py`** *(D8)* — model-dependent verification: PUMP-07 crosses τ in the **40–75 s** window from `t0`; PRESS-03 not called mechanical; CNC-11 → `evidence_conflict`; CONV-14 → `NO_PREDICTION`. **Re-run after every retrain.** Updates `DEMO_NOTES.md` with the measured crossing time | 1.6b | 0:15 | **Yes** | MVP |
| A-06 | `core/fusion.py` — risk formula, bands, agreement, confidence, 5 hard rules, autonomy ladder A3–A0, `explain()` term breakdown, tests | 2.4 | 0:40 | **Yes** | MVP |
| A-07a | `core/ingest.py` (`SensorSource`/`ParquetReplaySource`/`StreamClock` 600× from `t0`, bounded queues, range validation, `ingest_dlq`) + **alert generation** + SSE `/stream/events` + the routes the H6.5 gate exercises | 2.8a | 0:50 | **Yes** | MVP |
| **A-07b** | Remaining Appendix-B routes replacing stubs *(D9 — split out; **not** gate-blocking, may land after H6.5)* | 2.8b | 0:40 | No | MVP |
| A-08 | `core/downtime_sim.py` — 3 policies over identical events, 90-min penalty charged per false positive, £ from machine cost + ERP parts/labour, per-machine + plant-wide, τ overridable | 4.1a | 0:30 | No | MVP |
| A-09 | **KPI GATE #2** — `test_downtime_reduction_at_least_20` + `GET /ledger?tau=` | 4.1b | 0:15 | **Yes** | MVP |
| A-10 | `GET /health` per-dependency status + circuit-breaker state; `GET /metrics` JSON counters | §9 | 0:20 | No | MVP |
| A-11 | Rule-based fallback predictor + feature-version/hash assertions at model load | §3.5 | 0:20 | No | MVP |
| A-12 | `scripts/replay.py`, `scripts/prune.py`, `scripts/backup.py`, `scripts/run_all.py` | §10 | 0:20 | No | Stretch |

### WS-B — AI/LLM & Agents (R2)

| ID | Task | Spec | Est | Blocking | Tag |
|---|---|---|---|---|---|
| B-01 | `erp/` mock service skeleton on :8001 — 6 endpoints, own API key, own `erp.db`, `ERP_FAULT_MODE` chaos switch | 0.4 | 0:30 | **Yes** | MVP |
| B-02 | `core/db.py` (SQLite WAL, `busy_timeout=5000`, 32MB cache) + `core/models.py` — **13 tables per Appendix C** | *D6* | 0:25 | **Yes** | MVP |
| B-03 | `llm/client.py` — `LLMClient` protocol + **`LangChainLLMClient` (default, OpenAI-compatible)** and `HttpxLLMClient` adapters against `https://llm-gateway.example.com` / `openai-compatible-chat`; `llm.tls_verify` escalation ladder; 20s timeout, 2 retries, circuit breaker (4/60s), `llm/cache.py` content-hash cache, `llm/embeddings.py` TF-IDF default, token accounting, tolerant `parse_json()`, **`_call_json()` returns `None` on any exception** | 1.8 | 0:25 | **Yes** | MVP |
| B-04 | `core/preprocess.py` — skew→UTC, dedupe, unit harmonisation, 60s resample, Hampel despike, gap flags, frozen-sensor detect, `QualityReport` + unit tests | 1.4a | 0:25 | **Yes** | MVP |
| B-05 | `core/features.py` — 41 features, `FEATURE_VERSION="fv1"` contract + assertion, feature cache table + tests | 1.4b | 0:20 | **Yes** | MVP |
| **B-15** | **`core/predict.py`** (`Predictor` protocol, sole `.joblib` loader, sha256 + `fv1` assertion, τ read from `MANIFEST.json`, batch scoring) **+ `core/tsdb.py`** (`TSDBClient` protocol, DuckDB windowed reads) *(D7 — previously unassigned in all three documents)* | 1.4c | 0:30 | **Yes** | MVP |
| B-06 | ERP maintenance history generation — 480 work orders, ISO fault codes, technician free text (incl. **8 planted malicious notes, 3 of them authored blind to `injection_scan()` + 2 sub-threshold notes designed to reach the prompt**), parts/lead times → `erp/erp.db` | 1.3 | 0:20 | No | MVP |
| B-07 | `core/retrieval.py` — TF-IDF first (offline-safe), `NumpyVectorStore` protocol, hybrid candidate union + re-rank, MTBF/repeat-failure/cost SQL aggregation, `[M1..M6]` tags | 1.7 | 0:35 | No | MVP |
| B-08 | `agents/base.py` — `Agent` protocol, `AgentResult`, retry/backoff, trace emission, `agent_runs` persistence | 2.1 | 0:25 | **Yes** | MVP |
| B-09 | **Coordinator Agent** + `agents/prompts/v1/coordinator.md` + strict JSON schema + deterministic template fallback | 2.5 | 0:40 | **Yes** | MVP |
| B-10 | **Maintenance-Log Agent** — live ERP HTTP, retrieval, one LLM call → schema-validated JSON, retry/circuit-breaker → cache path, fleet-prior escalation labelled `assumption` | 2.3 | 0:40 | **Yes** | MVP |
| B-11 | **Sensor Agent** — deterministic, no LLM, `[S1..S5]` evidence bound to concrete tuples, INSUFFICIENT → refuse + data-integrity alert | 2.2 | 0:25 | **Yes** | MVP |
| B-12 | `agents/orchestrator.py` — `DiagnosisRun` state machine, `asyncio.gather` fan-out (20s/agent, 45s/run), FUSE, DIAGNOSE, REFLECT loop (≤1 retry), ROUTE by autonomy | 2.7 | 0:30 | **Yes** | MVP |
| B-13 | Approval workflow — `approvals` table + routes (mandatory reject reason) + **ERP Action Agent**: in-code approval precondition, `Idempotency-Key=sha256(prediction_id)`, no retry on 4xx, `pending_erp_sync` | 3.3 | 0:40 | **Yes** | MVP |
| B-14 | `eval/harness.py` + `eval/scenarios.yaml` — groundedness, 12 adversarial bundles, injection set, fault-mode top-1/2, RUL coverage, latency/cost, **200-run reliability with `ERP_FAULT_MODE=flaky` + LLM off** → `eval/REPORT.md` + JSON | 4.2 | 0:45 | No | MVP |

### WS-C — Frontend & Experience (R3)

| ID | Task | Spec | Est | Blocking | Tag |
|---|---|---|---|---|---|
| C-01 | Vite+React 19+TS+Tailwind 4 scaffold, `theme.css` tokens (dark control-room + status ramp), layout shell, TanStack Query client, router, skeleton primitives, **and the `vite.config.ts` dev proxy that makes the browser same-origin with the API** *(D10 — this is what makes the `SameSite=Strict` cookie work at all)* | 0.3 | 0:35 | **Yes** | MVP |
| C-02 | **Live Floor** *(demo moment #1 stage)* — 15 tiles, 60-point shifting vibration sparklines, conic-gradient risk rings, status by colour **+ icon shape + label**, live alert feed (`aria-live`), plant KPI header, replay-speed control | 1.9 | 1:20 | No | MVP |
| C-03 | **Machine Detail** — synchronised multi-channel Recharts with shared cursor, fault-onset markers, work-order history rail, quality-flag ribbon (interpolated/despiked bands) | 1.10 | 0:50 | No | MVP |
| C-04 | **Diagnosis** — fault mode + confidence gauge, reasoning steps, **Facts / Assumptions / Limits as three visually distinct blocks**, `[S#]`/`[M#]` citation chips that **scrub + shade the sensor chart** / pop the work-order card, `fusion.explain()` risk waterfall, "Generated" chip on all AI prose | 2.9 | 1:10 | No | MVP |
| C-05 | **2D SVG machine schematic** — colour-coded component hotspots from `components[]`, click filters chart channels. **The 3D fallback; built first, ships regardless** | §4.5 / *D6* | 0:25 | No | MVP |
| C-06 | **Agent-trace timeline** — Sensor ∥ Log → Fuse → Diagnose → Reflect → Route, real per-node latency, amber rejection fork | §4.3 | 0:30 | No | MVP |
| C-07 | **Schedule & Approvals** — 14-day predicted-vs-planned calendar, approval queue with autonomy badges, evidence drawer, typed-reason reject, optimistic `onMutate` + rollback toast | 3.5 | 0:50 | No | MVP |
| C-08 | Stub→real cutover: SSE `EventSource` with `Last-Event-ID` reconnect + 10s polling fallback, health strip, zero-blank-state sweep of **all 6 routes**, skeletons everywhere *(D9 — re-estimated from 1:00. Integration cutover across four screens is where hackathons die; it was the least protected hour in the plan)* | 2.8 | **1:30** | No | MVP |
| C-09 | **Framer Motion pass** — alert `AnimatePresence`, risk-ring `useSpring` (120/20), count-up numbers, `layoutId` tile→detail morph, `motion.path` trace draw-on, staggered grid, `MotionConfig` reduced-motion | 4.4 | 0:35 | No | MVP-additive |

### WS-D — Standout Features (R2 + R3) — **stretch, never blocking**

| ID | Task | Spec | Est | Blocking | Tag |
|---|---|---|---|---|---|
| D-01 | ⭐ **Downtime Ledger screen** — three bars racing under springs, count-up headline (`30.9% / £142k`), 20% target line, "what the model missed" toggle, **τ slider re-running all 3 policies live (<80ms)** | 4.3 / §5.1 | 0:50 | No | Stretch (backend A-08/A-09 is MVP) |
| D-02 | ⭐ **Trust & Evals screen** — accuracy vs 85% line, reduction vs 20% line, guardrail rejections by rule, injection quarantine log, latency/cost/token totals, DLQ contents, circuit-breaker states, model+prompt version stamps, **LLM-offline chaos toggle** | 4.5 | 0:30 | No | Stretch |
| D-03 | ⭐ **Rehearsal backend** — `POST /rehearsal/inject` writing `is_simulated=true` rows through the identical pipeline, `/reset` with state-restore test, `/scoreboard` (detection rate, lead time, mode accuracy) | 5.1a | 0:45 | No | Stretch |
| D-04 | ⭐ **Rehearsal Console UI** — machine/mode/severity/onset picker, live risk-ring watch, scoreboard | 5.1b | 0:30 | No | Stretch (**alternative to D-05**, D4) |
| D-05 | ⭐ **3D digital twin** — r3f low-poly pump/motor from primitives (~600 tris, no asset files), per-component risk tint, pulsing fault component + leader line, `OrbitControls`, click→channel filter, `React.lazy` chunk, WebGL guard + error boundary, flag default `false`. **HARD 45-MIN BOX** | 5.2 | 0:45 | No | Stretch (**alternative to D-04**, D4) |
| D-06 | ⭐ Audit **ledger viewer** UI (`/audit`, `/audit/verify` result, tamper indicator) | §5.3 | 0:20 | No | Stretch (chain itself is MVP = E-03) |

### WS-E — Security & Infra Hardening (R1, R2 on GenAI controls)

| ID | Task | Spec | Est | Blocking | Tag |
|---|---|---|---|---|---|
| E-01 | `core/guardrails.py` + **Guardrail Agent** — **tests written first**: numeric grounding **over a unit/scale-normalised bundle** (`0.87` ⇄ `87%`, days ⇄ hours, °C ⇄ °F, £ ⇄ £k), citation resolution, strict Pydantic shape, unsafe-action denylist, assumption discipline, rejection logging. **The demo's own alert and diagnosis strings ship as fixtures asserted to PASS** — a guard that rejects the demo is a silent switch to the template path wearing a rejection log | 2.6 | 0:40 | **Yes** | MVP |
| E-02 | JWT (HS256, 8h, httpOnly SameSite=Strict cookie) + argon2id + `require_role()` dependency + 4 seeded users + `tests/test_security.py` 403 matrix | 3.1 | 0:45 | **Yes** | MVP |
| E-03 | `core/audit.py` hash-chained ledger (`sha256(prev_hash‖canonical_json)`) + `GET /audit/verify` + tamper-detection test | 3.2 | 0:25 | No | MVP |
| E-04 | `redact()` (names/emails/employee IDs/phones, 20 fixtures) + `injection_scan()` + `<untrusted_document>` wrapping + quarantine + UI flag + audit entry | 3.4 | 0:30 | No | MVP |
| E-05 | Rate limiting (120/10/60 per min, in-process token bucket), security headers, error envelope + catch-all handler, CORS lockdown, 2MB/64KB body caps, `127.0.0.1` bind | 3.6 | 0:20 | No | MVP |
| E-06 | `tests/test_fairness.py` — crew/shift features absent from the vector; prediction rates equal by crew within noise | §7.5 | 0:15 | No | Stretch |

### WS-F — Integration, QA, Demo Prep (All)

| ID | Task | Spec | Est | Blocking | Tag |
|---|---|---|---|---|---|
| F-01 | Full suite green (~60 tests) + `python scripts/evaluate.py` → `eval/REPORT.md` | 6.1 | 0:20 | **Yes** | MVP |
| F-02 | `README.md`, `DEPLOYMENT.md` (managed-proxy/`SSL_CERT_FILE` notes, retention, capacity), `USER_GUIDE.md` (per role + **what the system cannot do**), `docs/AGENTS.md`, `docs/MODEL_CARD.md` | 6.2 | 0:20 | **Yes** | MVP |
| F-03 | **Clean-machine rehearsal ×2** — fresh clone → 4 commands → demo. **Second run with the network disabled** to prove the offline template path | 6.3 | 0:20 | **Yes** | MVP |

---

## 6. Risk & Contingency

Carried from spec.md §12 (numbering preserved) plus two planning-specific risks (**P1/P2**).

| # | Risk | Trigger condition (observable) | Pre-decided fallback — **no debate at the time** |
|---|---|---|---|
| **1** | **Accuracy misses 85%** | `A-05` red at **H3.4** | **Immediately** extend prediction horizon 24h → 48h in `config.yaml`; if still red, add lag features. Do **nothing else** until green. Report the horizon next to the number, always. Budget: eat R1's Phase-2 slack (0:50); if it costs more than that, `A-06 fusion` slips and the H6.5 gate is at risk → escalate to the whole team. |
| **4** | **Frontend eats the schedule** (classic hackathon failure) | `A-02` not frozen by **H1.0**, **or** C-02+C-03 not rendering on fixtures at **⛳H3.5** | Contract freeze is P0 — if H1.0 slips, R1 drops everything else until it lands. If C-02/C-03 are late at H3.5, R3 **skips C-09 animation entirely** and goes straight to C-04 Diagnosis (the demo-critical screen). Animation is a separate task by design so it can be deleted, not unpicked. |
| **P1** | **⛳H6.5 GO/NO-GO is red** — end-to-end flow not working | At H6.5: any of stream / prediction / alert / diagnosis / dashboard broken | **All stretch work stops.** All three builders converge on the broken link H6.5–H8.0. Phase 4/5 is the sacrificial budget — **0:40, not 2:30 (§3.2)**; WS-D is cancelled outright. H11.0–H12.0 rehearsal is **still protected** — a rehearsed partial demo beats an unrehearsed complete one. |
| **3** | **OpenAI-compatible LLM gateway unavailable / rate-limited during the demo** | `B-03` smoke test fails at H1.4, **or** circuit breaker open during rehearsal | Deterministic template path is MVP, not contingency — it is built in `B-09` regardless. Rehearsal #2 at **H11.5 runs with the network physically disabled**. If the gateway is down at demo time: present as-is; the "Offline diagnosis mode" banner + `deterministic-template` stamp is an *honesty* talking point, not an apology. |
| **3b** | **`openai-compatible-chat` not routable for our key** (renamed, deprecated, unentitled) | `B-03` smoke test at H1.4 returns 404/400 on the model name | The name lives in **one** config key. In order: (a) call the gateway's model-list endpoint and pick another chat model it advertises; (b) if none, run the build on the template path. **Hard 10-minute box — do not debug entitlement at hour 1.** Everything downstream is model-agnostic, so a swap costs one line and zero retests. |
| **3c** | **The configured model wraps its JSON in prose or fences** | Coordinator output fails Pydantic parse >20% at `B-09` | Already designed for: `json_mode: auto` drops `response_format` on 4xx, `parse_json()` strips fences and preamble, guardrail rejects → one reflection retry → template. **No new work if it happens** — just confirm the tolerant parser is exercised by a test at B-03. |
| **P2** | **AI latency blows the ≤6s p95 budget** — the two LLM calls are **60% of the budget** and the only part we do not control. 2.4s for a 450-token completion assumes ~190 tok/s from a shared gateway | **Measured at ⛳H1.4**, not at H9.0: `B-03`'s smoke test times a 300-token completion and writes `eval/latency_baseline.json` | **Pre-decided, applied at H1.4 with no debate** (spec §6.2 calibration table): **≥150 tok/s** → proceed. **60–150** → drop the log-summary LLM call now (deterministic SQL aggregation covers every field but `notes`); one LLM call per diagnosis. **<60** → one call, `coordinator.max_tokens` → 300, diagnosis on-demand only; if p95 still exceeds 6s, **report the measured number in `eval/REPORT.md` and label the target missed**. Waiting until H9.0 to learn this was the original plan's mistake — by then there is no time to act. **We do not pre-warm the cache to make a slow call look fast**: the agent trace shows real latencies. |
| **P3** | **KPI GATE #2 (≥20% downtime reduction) red** — previously the *only* KPI with no contingency anywhere | `A-09` red at **H8.95** | **20-minute box, in order:** (a) widen `recommended_window` so a correct prediction is actioned inside one planned window, not two; (b) if `FP/N` is the driver, **raise** τ — the §3.12 entailment shows the gate is more sensitive to false positives than to recall; (c) still red → publish the measured number with the entailment inequality beside it and mark the target missed. **Never touch `MTTR_unplanned` (320) or `planned_window` (90)** — moving the constants the result is derived from is how a KPI becomes a lie. |
| **P4** | **Login silently 401s in the browser** — `SameSite=Strict` across `localhost:5173` → `127.0.0.1:8000` is cross-*site*, so the cookie is never sent; `EventSource` fails identically | Any authenticated fetch from the UI returns 401 despite a successful login | **Already prevented, not mitigated**: `C-01` ships the Vite dev proxy so the browser is same-origin with the API, and `A-02` freezes the origin rule for every screen. If it still appears, the cause is a hardcoded absolute URL — `grep -rn "127.0.0.1\|localhost:8000" ui/src` finds it in seconds. **Do not "fix" it by loosening `SameSite` or opening CORS with credentials**; that trades an XSS-resistant session for a demo shortcut. |
| **8** | **3D twin burns hours** | Not visibly working at **⛳H10.0 + 45 min**, i.e. H10.75 | `ui.features.digital_twin` stays `false`; the SVG schematic (C-05, built H4.7) ships. **Zero other code changes** — that is the entire point of the lazy-chunk + flag scoping. Do not "just fix one thing"; the box is hard. |
| **5** | **Agent orchestration debugging spiral** | >30 min lost on a single orchestrator bug after H5.0 | Fall back to **sequential** execution (drop `asyncio.gather` fan-out, run Sensor then MaintLog serially) — costs ~1.2s latency, removes the entire concurrency failure surface. `GET /runs/{trace_id}` + `agent_runs` persistence exist precisely so a bad run is reproduced, not re-argued. |
| **11** | **Managed-proxy TLS interception breaks gateway calls** | SSL verification error on the `B-03` smoke test | Fixed ladder, **15-minute box**: (1) `verify=true`; (2) `SSL_CERT_FILE` → approved private CA bundle (documented in `DEPLOYMENT.md`); (3) set `llm.tls_verify: false` only for a controlled local demonstration — **gateway host only**, WARN-logged every startup, shown on `/health` and the Trust screen. ERP and every other client keep `verify=True` hard-coded. If even step 3 fails, run on the template path. |

---

## 7. Definition of Done — per Workstream

Stop when these are true. Anything beyond is polish, and polish is P4.

| WS | "Complete enough to demo" |
|---|---|
| **A — Backend/Data** | `python scripts/seed.py` runs clean on a fresh clone in ≤90s, leaves `git status` clean (τ goes to `MANIFEST.json`, never back into `config.yaml`), and both verification stages pass — `verify_signal()` in the generator **and** `tests/test_demo_integrity.py` after training (each raises on a broken demo). Both KPI gates green in pytest. `GET /machines`, `/machines/{id}/telemetry`, `/machines/{id}/prediction`, `POST /machines/{id}/diagnose`, `/ledger`, `/stream/events` all return real data. SSE pushes telemetry + alerts to a connected browser at 600× replay. `/health` shows 6 dependencies. **Not required:** `/metrics` dashboards, `scripts/replay.py`, prune/backup. |
| **B — AI/Agents** | All 5 agents run through `orchestrator.DiagnosisRun` with one trace ID end-to-end; `GET /runs/{trace_id}` returns prompts, raw responses, guardrail verdicts and timings. Coordinator emits schema-valid JSON with resolving citations. **With `LLM_API_KEY` unset the entire flow still completes** and is stamped `generated:false, model:"deterministic-template"`. ERP Action Agent refuses to write without an approval row (test-proven). At least one live diagnosis has been generated by `openai-compatible-chat` through the LLM gateway and shown end-to-end. **Not required:** embeddings via gateway (TF-IDF is the plan of record), the `httpx` adapter (LangChain adapter alone satisfies MVP), prompt tuning beyond v1. |
| **C — Frontend** | Live Floor, Machine Detail, Diagnosis, Schedule render **real** data with **zero blank states** — every panel has data, a skeleton, an empty state, or an honest "no data, here's why". Citation chip hover scrubs the sensor chart (demo moment #1 works). Agent-trace timeline draws with real latencies. Works at 1280×800. `prefers-reduced-motion` degrades all animation to instant. **Not required:** light mode, command palette, mobile layout, sub-1.0s FCP. |
| **D — Standout** | **Binary per feature: shipped-and-demoable, or flagged off and unmentioned.** No half-built screen is ever shown. Ledger: three bars + τ slider respond in <80ms. Rehearsal: inject → alert → diagnosis → `/reset` restores all 15 machines to their original risk (test-proven). 3D: renders in the Machine Detail side panel with WebGL guard + working SVG fallback. |
| **E — Security** | `tests/test_security.py` green: every mutating endpoint 403s every insufficient role. `GET /audit/verify` returns intact, and the tamper test detects a modified row. Guardrail rejection log has ≥1 real entry to show. 8/8 planted injection notes quarantined. `redact()` green on 20 fixtures. No secret in `config.yaml`, git, logs, or any response. **Not required:** fairness suite (E-06), CSP tuning beyond defaults. |
| **F — QA/Demo** | ~60 tests pass. `eval/REPORT.md` generated with both KPI numbers above their lines. All 7 docs exist and are accurate (not aspirational). **Two clean-machine rehearsals completed, the second offline.** Both 30-second demo moments run inside their time budget, twice. |

---

## 8. Demo Readiness Checklist (H11.0 → H12.0)

### 8.1 Core flow — live, no excuses

- [ ] Fresh clone → `venv` → `pip install -r requirements.txt` → `cp .env.example .env` → `python scripts/seed.py` → `python scripts/run_all.py`. **4 commands, ≤3 min excluding the 90s seed.**
- [ ] Backend :8000, mock ERP :8001, UI :5173 all up; `GET /health` shows 6 green dependencies.
- [ ] Login as `planner`; Live Floor paints 15 tiles with moving sparklines within 2s.
- [ ] **PUMP-07 crosses the alert threshold 45–60s into replay, live on screen** (planted by A-03 relative to `t0`; verified against the *trained model and tuned τ* by **A-15**, re-run after every retrain).
- [ ] Alert → 1 click → Diagnosis with agent trace, citations, Facts/Assumptions/Limits.
- [ ] Citation chip hover scrubs + shades the exact sensor window; `[M#]` pops the work order.
- [ ] A0 approval → evidence drawer → approve → **work order appears in the mock ERP** (`GET /erp/v1/workorders`).
- [ ] Both KPI numbers visible on screen with their target lines drawn.
- [ ] `pytest` run live in a terminal if asked: all green including both KPI gates.

### 8.2 Standout features — status board (fill in at H11.0, no ambiguity)

| Feature | Built / Cut | If cut, what ships instead |
|---|---|---|
| ⭐ Downtime Ledger UI + τ slider | ☑ Built ☐ Cut | Plain table + `eval/REPORT.md`; the 20% number is unaffected |
| ⭐ Trust & Evals screen | ☑ Built ☐ Cut | `eval/REPORT.md` opened in the editor |
| ⭐ Fault Rehearsal Console | ☐ Built ☑ Cut | Nothing — flag off, screen not routed, never mentioned |
| ⭐ Audit ledger viewer | ☑ Built ☐ Cut | `GET /audit/verify` shown in the browser |
| 3D digital twin | ☑ Built ☐ Cut | 2D SVG schematic, identical hotspots — presenter says nothing about 3D |

**Rule: a cut feature is not mentioned, not apologised for, and not visible in the nav.**

### 8.3 Fallback plan if AI calls fail live

1. **Nothing to do.** Alerts, risk scores, RUL, the ledger and the audit chain never touched the LLM. The
   deterministic template diagnosis renders with an amber "Offline diagnosis mode" banner and a
   `deterministic-template` stamp.
2. If the gateway is *slow* rather than down: the circuit breaker opens after 4 failures (60s cooldown) —
   wait it out or hit the **LLM-offline toggle on the Trust screen** deliberately.
3. **Rehearsed:** run #2 at H11.5 was executed with the network physically disabled. If it happens live,
   say so: *"That's the offline path — it's the same demo, deterministically narrated."*
4. Cached diagnoses (15-min TTL) replay in ~5ms, so any diagnosis already shown once is instant on re-open.
5. **Last resort:** `data/DEMO_NOTES.md` documents the expected output of each planted scenario — narrate
   from it against a static screen rather than debugging on stage.

### 8.4 Judge-facing demo script

> **Measured F-05 run:** PUMP-07 crossed τ=0.420000 at 52.3s of the 600× replay; the live alert was
> observed at 56.1s in both stopwatch takes. The floor showed
> risk 62/100, 49.6% failure probability in 24h and 6.2 days RUL. The offline `GET /runs/{trace_id}`
> contained one persisted coordinator stage at 3.0ms; absent stages were left unfilled. The ledger
> returned Reactive 19,200 min, Fixed-schedule 21,900 min, Predictive 10,230 min, a 46.72% reduction
> and £385,058 avoided. `scripts/demo_rehearsal.py --interactive` re-fetches these values and resets
> the replay clock before each timed take.

**Frame (15s):** *"Plants lose money to unexpected failures, and fault diagnosis needs an expert who isn't
there at 3am. Every number you're about to see is computed by a deterministic model. The AI only explains
numbers that already exist, and it has to cite its evidence."*

**Moment #1 — "It sees it coming, and it shows its work." (≤30s, Live Floor → Diagnosis)**
1. Floor is calm and green; 15 tiles, live sparklines.
2. PUMP-07's sparkline climbs; the risk ring sweeps amber → red; an alert slides in:
   risk 62/100, 49.6% failure probability and 6.2 days RUL.
3. One click → Diagnosis. The persisted trace shows coordinator 4.0ms in offline fallback; absent
   stages remain unfilled.
4. Hover `[S2]` → **the chart below scrubs and shades the exact 6-hour window the model reacted to.**
   Hover `[M3]` → “Drive-end bearing replacement”, dated 2025-09-27.
5. **Line:** *"Not a black box. Every sentence is anchored to data you can see."*

**Moment #2 — "The 20% is computed, not claimed." (≤20s, Downtime Ledger)**
1. Three bars race up: Reactive 19,200 min · Fixed-schedule 21,900 min · Predictive 10,230 min.
2. Count-up headline resolves to **46.72% downtime reduction / £385,058 avoided**, with the 20% target line drawn.
3. Toggle "what the model missed" — the failures we did **not** catch, with their cost included.
4. **Line:** *"Same event stream, three policies, and we charge ourselves a full planned window for every
   false positive. This is the pytest gate, not a slide."*

**Closer (20s) — pick two, by audience:**
- **Safety/governance:** approve an A0 recommendation → evidence drawer → work order lands in the ERP →
  `GET /audit/verify` shows the intact hash chain. *"No LLM holds a write tool."*
- **Resilience:** set `ERP_FAULT_MODE=flaky` or flip LLM-offline **live** → the dashboard degrades visibly
  and keeps working. *"Resilience you can watch beats resilience described."*
- **Rigour:** run `pytest` live — ~60 tests, both KPI gates green.
- **Honesty (if asked "isn't the synthetic data rigged?"):** show the held-out split — 3 machines never seen
  in training, split by time **and** machine, plus the `sensor_drift` false-positive trap on PRESS-03 that
  the system correctly *declines* to call a mechanical fault.

### 8.5 Pre-demo hard checks (do these at H11.75)

- [ ] Laptop on mains, sleep/screensaver disabled, notifications off.
- [ ] Browser zoom 100%, window 1280×800, one tab per screen pre-opened and logged in.
- [ ] Replay clock **reset to t0** so PUMP-07 fires on cue — not mid-fault from the rehearsal.
- [ ] `python scripts/seed.py` re-run clean (no stale `is_simulated` rows from rehearsal testing).
- [ ] Terminal font size up; `logs/app.jsonl` tail ready in a spare tab in case a judge asks for a trace.
- [ ] Feature flags in `config.yaml` match the §8.2 status board exactly.

---

**End of plan.** Every task above is Docker-free, container-free, and installable without administrator
rights. Ready for direct conversion to `tasks.md` — task IDs `A-01…A-12`, `B-01…B-14`, `C-01…C-09`,
`D-01…D-06`, `E-01…E-06`, `F-01…F-03` are stable and each carries owner, duration, blocking status, MVP/stretch
tag, and its spec.md §11 provenance.
