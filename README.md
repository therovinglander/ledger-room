# Ledger Room

A small, honest proof-of-concept for one claim: **an LLM can narrate
financial data without ever being allowed to guess a number.**

Code computes every KPI deterministically from raw transaction/financial
data. The model is handed structured *facts* — never raw rows — and narrates
them with inline citations back to the exact source row IDs. After the model
writes, a second piece of code re-extracts every number and citation from
its output and checks them against the input facts. Anything uncited or
unsupported is flagged, not hidden.

> **⚠ Synthetic data only.** Every business, transaction, and financial
> statement in `data/` is generated (see `src/generate_*.py`), not real. No
> real financial data — Andrew's own or anyone else's — appears anywhere in
> this repo.

## Live demo

📱 [ledger-room-demo.vercel.app](#) — phone-friendly, zero-JS (pure CSS tabs,
clickable even where JavaScript doesn't run, e.g. iOS QuickLook).

## What it does: three rooms, one shared fact layer

| Room | Audience | Output |
|---|---|---|
| **Cash Flow Room** | SMB underwriter | Runway, burn rate, customer concentration, overdraft risk — plain-English, cited |
| **Filing Extraction Room** | Financial analyst | Margins, bad-debt ratio, allowance adequacy, D&A trend, cash-vs-accrual reconciliation — cited |
| **Pricing Room** | Relationship manager / pricing desk | A deterministic risk-tier recommendation (Tier A/B/C) with required terms, citing only the risk flags that actually fired |

All three consume the same deterministic skill layer (`skills/`) and the same
narration contract (`skills/narrate.py`) — no new trust model per room.

```
ledger-room/
├── data/              synthetic transactions, financials, capex/depreciation
├── skills/            deterministic KPI + anomaly + citation + narration logic
├── rooms/             thin entry points: compute -> assemble facts -> narrate
├── demo/              self-contained HTML demo (JS and no-JS versions)
└── docs/              PRD, demo script, design notes
```

## Design contract (non-negotiable)

> Code computes and cites. The model reads structured facts only — never raw
> rows — and narrates with citations. No number in any output is a model
> guess.

Enforced in two places:
- `narrate.py: validate_facts()` — rejects any fact with a value but no
  citation *before* it reaches the model.
- `narrate.py: validate_narrative()` — re-extracts every number and citation
  from the model's output *after* it writes, and flags anything that doesn't
  trace back to an input fact.

This isn't a slogan — it caught two real bugs during development (a dropped
KPI due to a fact-key mismatch, and a validator blind spot on margins beyond
±100%). See `docs/DEMO_SCRIPT.md` for the full story.

## Running it

```bash
# self-checks (deterministic, no LLM needed)
python3 skills/cash_kpis.py
python3 skills/accrual_kpis.py
python3 skills/anomaly_detection.py
python3 skills/reconciliation.py
python3 skills/citation.py
python3 skills/pricing_tier.py
python3 skills/narrate.py

# run a room end-to-end
python3 rooms/cash_flow_room.py --business biz_07
python3 rooms/filing_extraction_room.py --business biz_03
python3 rooms/pricing_room.py --business biz_11
```

**Status:** 21/21 skill self-checks passing. Swept all 25 synthetic
businesses × all 3 rooms (75 runs) — zero uncited numbers, zero unresolved
citations.

## What this is not

- Not a production system, not investment/credit advice.
- The pricing-tier rule (`skills/pricing_tier.py`) is a deliberately simple,
  documented 5-flag threshold — an illustration of *governed decisioning*,
  not a real underwriting model.
- The narration backend in this repo is a deterministic mock (sandbox build
  had no LLM access). The citation/validation contract is backend-agnostic —
  swapping in a real model doesn't change what gets rejected.

## Why this exists

Built as a scoped 2-3 day artifact to demonstrate one architectural pattern —
traceable, governed AI narration over computed facts — that applies anywhere
a model touches financial numbers: KPI extraction, risk briefs, or
relationship pricing decisions.
