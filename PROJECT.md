# Ledger Room — Project Plan, Build Context, PRD

Subproject of `trading-room/`. Standalone from the full Trading Room build (see
sibling docs) — this is a scoped, 2-3 day artifact, not the multi-agent trading
system. Written so a fresh session can pick this up with no other context.

## 1. Why this exists

Dual-purpose career artifact, built to close specific gaps surfaced in two live
interview processes:

| Target | Role | Gap this closes |
|---|---|---|
| **Daloopa** | Senior PM, applied same day this started | No direct experience with financial-analyst/buy-side workflows as end users. Daloopa's product is structured, traceable extraction from financial filings. |
| **Relay** | Senior PM, warm intro, Stage 1 interview booked (background/conversational, non-technical — low urgency for this artifact until Stage 2 Product Deep Dive / Stage 3 case study) | Fintech product exposure; Relay's bonus points explicitly call out fintech experience and risk/fraud/credit-underwriting framework familiarity. Relay's own pitch is "turn cash flow from stress into clear signal" for SMBs with ~27 days of cash on hand. |

Reuses a pattern already proven on other projects (MCP Identity Resolution
Server, Compute Research Dashboard): ship a small, honest, scoped tool to prove
a capability before claiming it on a resume — never a polished product, never
overclaimed.

## 2. Core design philosophy (non-negotiable, carries from the Trading Room spec)

**Code computes and cites. The model reads structured facts only — never raw
rows — and narrates with citations. No number in any output is a model guess.**

This is the single most important line to be able to say out loud in either
interview. Everything below is built to make that literally true, not just a
slogan.

## 3. Architecture: two rooms, one shared skill layer

Deliberately the *lightweight* version of the Trading Room pattern — shared
deterministic skills, two narrow "lens" entry points, **no agent personas, no
debate/voting protocol**. The full persona/debate machinery was considered and
explicitly rejected: it would blow a 2-3 day budget into weeks for no added
credibility (see decision log, item D4).

```
trading-room/
  ledger-room/                      # THIS project
    README.md
    data/                           # see §4, status: built
    src/
      generate_data.py              # bank transactions + capex events
      generate_fixed_assets.py      # depreciation schedule from capex
      generate_financials.py        # accrual monthly financials
    skills/                         # NOT YET BUILT — next chunk
      cash_kpis.py                  # runway, burn rate, concentration, overdraft risk
      accrual_kpis.py               # D&A, bad debt, net income
      reconciliation.py             # cash vs accrual gap, flagged with source IDs both sides
      anomaly_detection.py          # Benford-style flags, round-number clusters
      citation.py                  # re-derives source transaction/financials IDs on demand
      narrate.py                   # the one shared LLM call: facts in, cited narrative out
    rooms/                          # NOT YET BUILT
      cash_flow_room.py             # Relay lens -> SMB cash-runway/risk brief
      filing_extraction_room.py     # Daloopa lens -> traceable KPI extraction report
```

| Room | Reads | Calls | Produces | Audience |
|---|---|---|---|---|
| `cash_flow_room` | `transactions.csv` | `cash_kpis`, `anomaly_detection`, `narrate` | SMB cash-runway/risk brief | Relay |
| `filing_extraction_room` | `monthly_financials.csv` (+ real EDGAR data, see §5) | `accrual_kpis`, `reconciliation`, `narrate` | Structured, cited KPI extraction + confidence flags | Daloopa |

Both rooms import the same `citation.py` and `narrate.py` — that shared import
*is* the proof of the design philosophy in §2, not just a description of it.

## 4. Data layer — STATUS: built and verified

No real SMB bank data used or available for the cash side (private SMB bank
data isn't published anywhere; see §5 for the one place real data *is* used).

| File | Rows | What it is | Traces back to |
|---|---|---|---|
| `data/transactions.csv` | 3,299 | Cash-basis bank transactions, 25 synthetic businesses × 12mo (2025). Categories: revenue, payroll, rent, interest_income/expense, bank_fees, insurance, software_subscription, refund, owner_draw, equipment_purchase, vendor/misc/tax/loan | Ground truth — every row has a stable `transaction_id` |
| `data/capex_events.csv` | 31 | Equipment/asset purchases | `source_transaction_id` → the bank row that paid for it |
| `data/depreciation_schedule.csv` | 183 | Straight-line monthly depreciation, derived from capex (depreciation is NEVER injected as a fake bank row — it's a non-cash accrual allocation, see decision log D1) | `capex_id` → the asset it's amortizing |
| `data/monthly_financials.csv` | 300 (25 biz × 12mo) | Accrual P&L + balance-sheet snapshot: revenue (recognition-lagged vs cash by 0-2mo per business), COGS, opex, D&A, bad debt expense, net income, AR, allowance for doubtful accounts, AP, accrued liabilities | `revenue_basis_refs`, `opex_other_basis_refs`, `dep_basis_refs` columns cite exact source rows |

**4 deliberately injected risk patterns** (so the KPI/anomaly layer has real
signal to catch, not just noise):

| Business | Pattern | Tests which KPI |
|---|---|---|
| biz_07, biz_18 | Declining revenue trend | Cash runway / burn rate — directly echoes Relay's "27 days of cash on hand" framing |
| biz_03 | Single customer = 62% of revenue | Concentration risk — maps to Relay's "credit underwriting frameworks" bonus point |
| biz_22 | Round-number transaction cluster | Benford's-law-style anomaly flag — maps to Relay's "risk/fraud tools" bonus point |
| biz_11 | Thin cash buffer around payroll dates | Overdraft/liquidity risk |

Verified: biz_07's `net_income` visibly deteriorates month over month in
`monthly_financials.csv`, and depreciation correctly starts the month *after*
its first capex purchase (confirmed by spot-check, not assumed).

## 5. Real-data decision (confirmed, pending execution)

| Room | Real data available? | Decision |
|---|---|---|
| `filing_extraction_room` (Daloopa) | **Yes** — SEC EDGAR XBRL "company facts" API (`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`), free, needs only a `User-Agent` header with contact email. Returns real filings pre-tagged with GAAP concepts (`us-gaap:Revenues`, `us-gaap:DepreciationDepletionAndAmortization`, `us-gaap:AllowanceForDoubtfulAccountsReceivable`) that map directly onto the KPIs already built. | **Approved, not yet built.** Pull 3-5 real small/mid-cap public companies. Strict upgrade over synthetic for this room — real filings in Daloopa's exact problem domain. Est. 2-4 hrs. |
| `cash_flow_room` (Relay) | **No** — private SMB bank data isn't published anywhere, by its nature. | Stay synthetic, clearly labeled as such. Optional stretch: free Plaid sandbox for a realistic bank-integration pattern (not required; Relay Stage 1 is non-technical, low urgency). |
| — | Andrew's own real CCPC bank data | **Explicitly declined for this project.** Flagged as a privacy/confidential-data risk for any artifact that might be shown to interviewers or land in a repo — not worth the exposure when Plaid sandbox gets the same credibility point safely. |

## 6. Decision log

- **D1 (depreciation/bad debt placement).** Depreciation and bad debt do not
  belong in the bank feed — they're non-cash/accrual constructs. Building them
  as a second accrual-basis layer (§4) instead of injecting fake bank rows was
  the correct fix, and it happened to unlock a stronger design: a real
  cash-vs-accrual reconciliation skill, which is itself a strong Daloopa talking
  point ("evaluate AI output quality, flag low-confidence extractions").
- **D2 (sparse categories).** Fixed directly by expanding the bank-side category
  list (bank_fees, interest income/expense, insurance, software_subscription,
  refund, owner_draw, equipment_purchase) rather than overloading categories
  with accrual concepts that don't belong there.
- **D3 (two audiences, one codebase).** Two "rooms" sharing one skill layer,
  reusing the Trading Room's skill/agent-as-lens pattern, so each audience gets
  a tailored artifact without maintaining two unrelated codebases.
- **D4 (scope: lightweight vs full Room pattern).** Explicitly rejected the full
  persona/debate-protocol version of the Room pattern for this project — no
  added credibility for a 2-3 day artifact, and it's what blew the original
  Trading Room's own estimate out to months. Shared skills + two lens entry
  points only.
- **D5 (real data, asymmetric by room).** Real EDGAR filings for the Daloopa
  room (approved); synthetic, clearly labeled, for the Relay room (no real
  public substitute exists); Andrew's own real bank data explicitly ruled out
  for privacy reasons.

## 7. Remaining build steps (in order)

1. `skills/cash_kpis.py`, `skills/accrual_kpis.py`, `skills/anomaly_detection.py`
   — deterministic, unit-tested, each citing its source IDs.
2. `skills/reconciliation.py` — cash vs accrual gap per business/period, with
   source refs on both sides.
3. `skills/citation.py` — given a KPI name + business + period, re-derive the
   exact source row IDs on demand (not a frozen string).
4. EDGAR real-data pull for `filing_extraction_room` (§5) — new connector,
   replaces/augments synthetic `monthly_financials.csv` for this room only.
5. `skills/narrate.py` — the one shared LLM call, schema-validated, facts-only
   input, citations required in output.
6. `rooms/cash_flow_room.py` and `rooms/filing_extraction_room.py` — thin
   entry points wiring the above into one report each.
7. 3-line README finalize; confirm repo is safe to show (no real financial data,
   synthetic clearly labeled).

## 8. Timeline / urgency context

- Daloopa: application submitted same day this project started — artifact is a
  fast-follow reference in a follow-up note, not a blocker to the application.
- Relay: Stage 1 (background/conversational) booked for next Thursday — **does
  not require this artifact**. Prep for that call is a separate workstream
  (chronological career narrative). This artifact targets Relay Stage 2
  (Product Deep Dive) and Stage 3 (case study panel).
- Total effort so far: data layer (~1 day equivalent). Remaining: skills + rooms
  + EDGAR swap, estimated 1-1.5 days.

## 9. Repo hygiene reminder for next session

No real financial data (Andrew's own) in this repo. Synthetic data must stay
clearly labeled as synthetic in the README before this is ever shown externally.
