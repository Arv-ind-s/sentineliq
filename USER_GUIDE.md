# SentinelPdM user guide

SentinelPdM is a decision-support demo. It helps a plant team inspect current machine condition, understand the evidence behind a predicted issue, and route an approved maintenance action. It does not replace a qualified person, a safety procedure, or the plant's official maintenance and change-control process.

## Sign in and roles

The local seed creates four demo users. Their usernames and roles are identical, and the throwaway local fixture password is `fixture`:

| User | Landing workspace | What to demonstrate |
|---|---|---|
| `operator` | Live Floor | Monitor the plant, triage open alerts, and escalate risk. |
| `technician` | Maintenance | Inspect diagnosis evidence, maintenance windows, and technician approvals. |
| `planner` | Schedule & Approvals | Make accountable schedule decisions and release governed ERP work. |
| `admin` | Trust & Evals | Oversee model trust, integrations, and the audit trail. |

For a presentation, start at the **Choose your control-room role** screen. Selecting a card pre-fills the local demo account, states that persona's mission, and opens its dedicated landing workspace. The left rail then repeats the active role, its mission, and allowed tasks; navigation only shows workspaces relevant to that persona. This gives the audience an immediate before/after cue when you switch roles.

When signed in as `admin`, the top bar also includes a **Demo as** persona selector. It replaces the active session role and redirects to that persona's landing workspace, so protected actions are genuinely enforced as the selected role. The admin demo capability is retained only to switch back and every change is recorded in the audit ledger.

Use the lowest role that can complete the task. The fixture credential is for the local demo only; replace the JWT secret and user management before any shared deployment.

## Operator walkthrough

1. Sign in as `operator` and open Live Floor.
2. Watch the 15 machine tiles and moving sparklines. A tile's colour is not the only status signal: read its label, risk, alert state, and freshness/quality indicator.
3. When an alert appears, assess its severity, machine state, freshness, and quality indicator before escalating it to maintenance.
4. Escalate to a technician when a safety boundary, data-quality issue, conflicting evidence, or low-confidence result is shown. Operators do not open diagnosis evidence, approve work, or create ERP work orders.

## Technician walkthrough

1. Open Machine Detail for the affected machine and inspect the selected sensor channels, quality flags, and event timeline.
2. In Diagnosis, start with Facts. Confirm that the risk, threshold comparison, fault mode, and RUL values match the displayed evidence bundle.
3. Read Assumptions and Limits. A missing window, stale signal, `sensor_drift`, conflicting maintenance history, or Gemini API outage should be visible rather than silently converted into certainty.
4. Use the citation scrubber to open the exact sensor interval or maintenance row. Check whether the evidence is recent, relevant, and from the expected machine.
5. Treat recommended actions as reviewable options. If the recommendation is unsafe, unsupported, or not grounded in the cited evidence, do not advance it; use the escalation path and record the concern.

## Planner walkthrough

1. Sign in as `planner` and open Schedule & Approvals.
2. Review the candidate job, machine, fault mode, RUL range, expected downtime, cost, evidence citations, and autonomy route.
3. An A0 route requires explicit approval. Confirm the evidence and the immutable server payload against plant policy, crew availability, parts, and safety constraints. The browser cannot replace that payload.
4. Approve once only after review. The ERP action agent hashes the approval ID and stored payload; a failed ERP call becomes a visible pending sync rather than an invented success.
5. Verify the resulting work order in the mock ERP and inspect the Audit Ledger. Reject or escalate when confidence is low, evidence conflicts, data quality is poor, or the action is outside the planner's authority.

An A1 item belongs to the technician. Acknowledgement records the review and closes the item without creating an ERP work order; reject it with a reason when the recommendation should not proceed.

## ERP Work Orders workspace

Open **ERP Work Orders** as a technician, planner, or administrator to inspect the operational maintenance record without handling the ERP service credential directly.

- The connection banner states whether rows come from the live ERP or the local historical mirror. A cached result is explicitly marked degraded.
- Use Asset, Status, and Search to filter the complete seeded history by work-order ID, fault code, notes, or part.
- Parts & lead times covers every seeded fault-code family and shows stock state, lead time, and INR cost.
- Planners and administrators can retry an already-approved A0 recommendation by approval ID. The service reloads the stored payload; clients cannot submit replacement work-order fields. Retrying the same approval returns the existing ERP work order rather than creating a duplicate.
- Pending ERP sync lists transiently failed writes and retry details. A zero queue means there is no accepted write waiting to reach ERP.

The workspace is not an approval bypass. Create or review the recommendation in **Schedule & Approvals** first; the API and ERP Action Agent enforce that boundary even if a browser request is modified.

## How to read a diagnosis

Read the diagnosis in this order:

1. **Current state and alert:** what was observed now, which threshold was crossed, and whether the reading is fresh and usable.
2. **Numbers:** failure risk, tuned threshold, fault-mode probabilities, and RUL interval. These are generated by the local sklearn artifacts and are the authoritative numerical outputs.
3. **Facts:** statements directly supported by the sensor and maintenance evidence. Each factual claim should have `[S#]` or `[M#]` citations.
4. **Assumptions and limits:** missing data, conflicting records, sensor quality, model limitations, and any offline/degraded status.
5. **Recommended action and route:** what a person may review next, whether it requires approval, and whether it has been escalated.

The coordinator can write a concise narrative, but it may not create a new measurement, change the fused risk, alter the RUL interval, or turn a cited conflict into a fact. The Guardrail Agent validates the response and can force a deterministic template or escalation.

## What confidence means

Confidence is a bounded model/evidence signal, not a probability that the machine will definitely fail and not a guarantee of safety. It reflects the available classifier/fault-mode evidence and the quality/consistency of the current evidence bundle. A high value means the supplied evidence is internally stronger for the displayed prediction; it does not authorize an unsupervised intervention. A low value, wide RUL interval, stale telemetry, disagreement between sources, or a guardrail warning should lower trust and prompt review.

Confidence is not the same as the evaluation report's balanced accuracy, precision, recall, or downtime score. Those are aggregate test metrics. Use the per-diagnosis evidence and route when making an operational decision.

## When to escalate

Escalate to a technician, planner, admin, or safety owner when:

- sensor quality is invalid, stale, frozen, duplicated, heavily skewed, or marked `sensor_drift`;
- sensor and maintenance evidence disagree, especially for the planted CNC-11 conflict;
- the diagnosis has a low confidence, wide RUL interval, unsupported claim, unsafe action, or guardrail rejection;
- the ERP is unavailable, the action remains pending sync, or an approval is not clearly recorded;
- the LLM is offline or degraded and the deterministic template does not provide enough context;
- a proposed action affects safety, production release, lockout/tagout, or another controlled procedure.

The correct degraded response is to preserve the evidence and escalate, not to guess. For the CONV-14 data-integrity scenario, the system is expected to withhold a prediction when the signal is not trustworthy.

## Offline mode and degraded services

The amber Offline diagnosis mode banner and `deterministic-template` stamp mean that the narrative LLM path was not used or was not trusted. Numerical model predictions, retrieval, audit, and the rest of the core flow remain local. The health page should show the LLM as degraded/reachable false while distinguishing it from the database, model, stream, and ERP dependencies. Cached diagnoses may reopen quickly, but the cache does not make old evidence current.

## What this system cannot do

- It cannot guarantee that a machine will or will not fail, and it cannot replace a qualified technician or a safety review.
- It cannot discover a new physical measurement or infer a value that is absent from the evidence bundle.
- It cannot treat LLM prose as the source of a numerical prediction; the LLM only narrates already-computed evidence.
- It cannot approve or write an ERP work order without the required authenticated role, explicit approval, and idempotency controls.
- It cannot safely resolve contradictory evidence by choosing the convenient source; it exposes the conflict or escalates.
- It cannot make the seeded benchmark metrics representative of every plant, machine family, operating regime, or unseen sensor design.
- It cannot operate the plant, change PLC logic, bypass lockout/tagout, issue safety clearance, or perform a physical repair.
- It cannot make Gemini TLS exceptions safe by itself. `tls_verify: false` is a scoped, explicitly escalated demo setting with residual interception risk.
