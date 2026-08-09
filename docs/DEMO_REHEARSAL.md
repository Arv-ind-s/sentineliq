# Judge-facing demo rehearsal

`scripts/demo_rehearsal.py` is the executable runbook for F-05. It reads the
current API and `data/DEMO_NOTES.md`, so a retrained model or a regenerated
ledger cannot silently leave stale numbers in the spoken script.

## Run

With the API, ERP, and UI running, log in as `admin` in the browser and run:

```powershell
python scripts/demo_rehearsal.py --verify
python scripts/demo_rehearsal.py --interactive
```

The interactive command performs two takes of each moment. In the current run,
the live PUMP-07 alert arrived at 56.1s in both Moment #1 takes. Before every take
it calls the admin-only `POST /stream/reset`, which stops the current replay,
clears stale SSE history, and creates a new clock at the configured `t0` when
the next stream is opened. Reload Live Floor after each reset so its SSE
subscription is fresh. It also resets once after the final take, leaving the
application ready for the next operator action. The command then waits for the PUMP-07 event and times
only the presenter’s spoken moment; the 52.3-second cue is not charged to the
30-second Moment #1 budget.

The presenter must speak the lines aloud and press Enter only after each line
has been delivered. The stopwatch can verify duration, but it cannot verify
whether a person read a line; that remains a human rehearsal check.

## Measured values in the current seeded run

- PUMP-07 crossed τ=0.420000 at 52.3s of the 600× replay; the live alert was
  observed at 56.1s in both Moment #1 takes.
- The floor showed risk 62/100, 49.6% failure probability in 24h, and 6.2 days RUL.
- The offline diagnosis trace persisted coordinator 3.0ms; absent stages were
  intentionally left unfilled.
- Diagnosis citation `[S2]` spans `2026-01-01T06:00:00Z` →
  `2026-01-01T12:00:00Z`; `[M3]` is “Drive-end bearing replacement”, dated
  `2025-09-27`.
- Ledger: Reactive 19,200 min; Fixed-schedule 21,900 min; Predictive 10,230
  min; 46.72% reduction; ₹4,91,33,636 avoided; 21 missed failures shown by the
  “what the model missed” toggle.

All displayed monetary values are INR. The historical seeded GBP amounts are
converted at the API boundary using the frozen rate £1 = ₹127.6005 (Bank of
England XUDLBK97, 27 July 2026); see `docs/CURRENCY.md`.

## Presenter lines

Frame: “Plants lose money to unexpected failures, and fault diagnosis needs an
expert who isn't there at 3am. Every number you're about to see is computed by
a deterministic model. The AI only explains numbers that already exist, and it
has to cite its evidence.”

Moment #1 payoff: “Not a black box. Every sentence is anchored to data you can
see.”

Moment #2 payoff: “Same event stream, three policies, and we charge ourselves a
full planned window for every false positive. This is the pytest gate, not a
slide.”

Chosen closers for this run:

- Safety/governance: “No LLM holds a write tool.”
- Resilience: “Resilience you can watch beats resilience described.”

If a number changes, re-run `--verify` and use the new output. Never speak the
illustrative figures that were removed from the specification.
