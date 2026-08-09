# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This is an implemented FastAPI, SQLite, and React/Vite application. Use the commands in `README.md` for
local setup, tests, and runtime startup; use `pytest <path-to-test>` for focused backend checks and the
scripts in `ui/package.json` for frontend linting and builds.

## What is being built

An **AI-enabled predictive maintenance and fault diagnosis agent for manufacturing equipment**. The
constraints below should be preserved when designing or changing a component.

### Required agent topology

The solution is specified as a **multi-agent system**, not a single model call:

- a **sensor agent** that processes streaming sensor data (temperature, vibration, pressure — time series),
- a **maintenance-log agent** that analyzes maintenance history from ERP systems,
- a **coordinator agent** that predicts failures and recommends maintenance actions.

Deliverables layered on top: an interactive dashboard (real-time alerts, fault-diagnosis explanations,
maintenance schedules), API integrations with ERP/maintenance platforms, and automated test suites for
data ingestion and prediction accuracy.

### Data

Synthetic or anonymized data is explicitly sanctioned. Expected inputs: sensor time series, ERP maintenance
history logs, and operational/machine-usage metadata, at a volume that simulates multiple machine cycles.
Preprocessing must cover **sensor-noise cleaning and timestamp alignment** across sources, plus synthetic
generation of **fault events** for training and testing.

### Target KPIs

- **≥85% fault prediction accuracy**
- **20% downtime reduction**

These are the numbers the prototype is judged on — keep them measurable in code (evaluation harness) rather
than asserted in documentation.

### Evaluation criteria that shape design

The brief's checklist grades more than a working demo. The items with direct code consequences:

- **Separate prediction from narration.** Ground outputs in trusted sources, cite evidence, separate facts
  from assumptions, and surface reasoning, confidence, and limits.
- **Human-in-the-loop boundaries.** Define approval checkpoints and intervention points for high-risk or
  uncertain maintenance decisions; autonomy must have explicit limits.
- **Agentic mechanics must be explicit**: agent roles, collaboration model, handoffs, tool usage, retry,
  reflection, and escalation logic.
- **Guardrails**: privacy, bias, explainability, prompt injection, unsafe recommendations, unauthorized
  tool execution.
- **Configurability**: models, prompts, tools, APIs, vector stores, and deployment environments must be
  swappable to avoid lock-in.
- **Evaluation beyond happy path**: groundedness, hallucination control, latency, cost, reliability,
  traceability — with edge cases and failure scenarios in the test data.

## Core implementation pattern

- FastAPI + SQLAlchemy backend, deterministic synthetic data generator seeded to a fixed value, Vite/React UI.
- **The load-bearing pattern**: scores are computed deterministically by an engine; the LLM only *explains*
  a number that already exists — it never produces one. LLM output is post-checked for numbers absent from
  the supplied inputs, and a deterministic template is used on rejection or when no API key is configured,
  so the demo runs fully offline.
- Every response is stamped with model, prompt version, timestamp, and whether it was generated or fell
  back to a template.

The analogous split here is: the predictive model produces failure probabilities and RUL estimates; the
LLM layer diagnoses and explains them.
