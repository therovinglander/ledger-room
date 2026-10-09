# SMB Cash Runway & Risk Signal

Small, honest proof-of-concept: turns raw SMB transaction data into structured,
traceable cash-flow KPIs (runway, burn rate, concentration risk, anomaly flags),
then has an LLM narrate the *structured facts only* — never raw transactions —
into a plain-English risk brief with citations back to transaction IDs.

Code computes every number (deterministic, unit-testable). The model reads
facts, not rows, and cites what it read. No number in the output is a model
guess.

## Why this exists

Scoped 1-2 day build to validate two things before claiming them on a resume:
structured financial extraction with traceability (Daloopa), and SMB cash-flow
visibility + risk signal (Relay). Synthetic data only — no real bank data used
or needed.

## Status

In progress. See `src/` for the deterministic KPI engine; LLM narrative layer
not yet built.

---
Lives in the trading-room repo (github.com/therovinglander/trading-room, private). Bots in the build pod see it at `/workspace/ventures/trading-room/ledger-room`. Synthetic data only: no real bank data, broker credentials or live-execution code.
