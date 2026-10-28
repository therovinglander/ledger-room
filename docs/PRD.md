# Ledger Room — PRD

Status: built, 3 rooms live. This PRD exists to make the product decisions
explicit and reviewable on their own — `PROJECT.md` remains the canonical
build log and decision history; this doc is the "what are we building and
for whom" summary pulled out of it.

## 1. Problem statement

A small, honest, working artifact — not a resume claim — that demonstrates
two specific capabilities:

1. Turning raw financial data into **structured, traceable, cited** KPIs
   (extraction from financial filings/transactions).
2. Turning that same traceable fact layer into **decisions** — a
   risk/underwriting-style signal, and a pricing-tier recommendation — not
   just a KPI readout.

All three are closed by the same underlying engine: deterministic skills
that compute numbers and carry their own source-row citations, with the
model restricted to narrating already-computed facts — never guessing a
number, never touching a raw row.

## 2. Non-goals

- Not a general ledger / accounting product.
- Not the full multi-agent Trading Room (no personas, no debate/voting —
  see PROJECT.md decision D4).
- Not using any real financial data (decision D5) — fully synthetic,
  clearly labeled.
- Not a forecasting or predictive-ML product — every KPI here is a
  deterministic, auditable calculation, not a model estimate.

## 3. Users / personas

See `PERSONAS.md` for full detail. Summary:

| Persona | Room | Core question they're asking |
|---|---|---|
| Buy-side financial analyst | `filing_extraction_room` | "Can I trust this extracted number, and can I see exactly where it came from?" |
| SMB credit/risk underwriter | `cash_flow_room` | "Is this business running out of cash, and is anything about its transactions suspicious?" |
| Relationship manager / pricing desk | `pricing_room` | "What should we charge this account, and why?" |

## 4. Functional requirements

### 4.1 Shared skill layer (`skills/`)
| Skill | Produces | Status |
|---|---|---|
| `cash_kpis.py` | runway, burn rate, customer concentration, overdraft risk | ✅ built, self-checked |
| `anomaly_detection.py` | round-number expense clustering, Benford-deviation ranking | ✅ built, self-checked |
| `accrual_kpis.py` | gross/operating/net margin, bad-debt profile, allowance adequacy, D&A trend, net-income integrity recheck | ✅ built, self-checked |
| `reconciliation.py` | cash-vs-accrual gap per business/period, cited both sides | ✅ built, self-checked |
| `citation.py` | re-derive source row IDs for a given KPI/business/period on demand | ✅ built, self-checked |
| `pricing_tier.py` | deterministic A/B/C tier recommendation from 5 risk flags | ✅ built, self-checked |
| `narrate.py` | the one shared LLM call — facts in, cited narrative out | ✅ built, self-checked |

Every skill function returns a dict with a `value`/metric field plus
`source_transaction_ids` (or `financials_id`s) — this is the contract every
other layer (rooms, narration) depends on. No function is allowed to return
a bare number with no citation.

### 4.2 Rooms (`rooms/`) — thin entry points, all built
- `cash_flow_room.py`: reads `transactions.csv`, calls `cash_kpis` +
  `anomaly_detection` + `narrate`, outputs an SMB cash-runway/risk brief.
- `filing_extraction_room.py`: reads `monthly_financials.csv`, calls
  `accrual_kpis` + `reconciliation` + `narrate`, outputs a structured KPI
  extraction report with confidence flags.
- `pricing_room.py`: calls `cash_kpis` + `accrual_kpis` + `anomaly_detection`
  + `pricing_tier` + `narrate`, outputs a tier recommendation with cited
  rationale.

### 4.3 Data layer (`data/`) — built
- `transactions.csv`, `capex_events.csv`, `depreciation_schedule.csv`,
  `monthly_financials.csv` — see PROJECT.md §4 for row counts and lineage.
- `businesses.csv` — cosmetic business_id → name/industry/risk-note
  roster, so reports can reference named businesses instead of bare ids.
  Does not touch any financial figure.

## 5. Non-functional requirements

- **Determinism.** Every KPI is reproducible from the same CSV inputs — no
  randomness at query time (randomness is seeded once, at data-generation
  time only).
- **Citeability.** Every number traces to one or more row IDs. This is
  checked, not assumed — see each skill's `_self_check()`.
- **No real/confidential data.** Enforced per PROJECT.md §9; checked before
  any external sharing.
- **Small surface area.** Three rooms, one skill layer, no agent framework —
  deliberate scope control (decision D4).

## 6. Success criteria

1. Can narrate, out loud, to any audience: "code computes and cites, the
   model narrates facts it's handed and nothing else" — and point at the
   actual code that makes it true.
2. All three rooms produce a report a non-technical reviewer or a technical
   one can read end-to-end with every number traceable.
3. Repo is safe to show externally: no real financial data, synthetic data
   clearly labeled.

## 7. Open decisions carried from PROJECT.md

- Real EDGAR filings integration for the filing-extraction room — approved
  direction, separate workstream (see `skills/edgar.py`).
- Plaid sandbox integration for the cash-flow room — optional stretch, not
  built.
