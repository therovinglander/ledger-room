# Ledger Room — Personas

Three personas, one per room. Each persona drives what that room's
`narrate.py` output should look and read like — tone, depth, and which KPIs
lead.

## 1. Buy-side financial analyst — `filing_extraction_room`

**Who:** A financial analyst or associate at a buy-side fund or research
shop, reviewing extracted figures from a company's filings before they go
into a model or memo.

**What they need from this tool:**
- Every number traced to its exact source line — no "trust me."
- A flag the moment something doesn't reconcile (e.g. net income that
  doesn't sum from its own components).
- Margins, bad-debt, and D&A trends presented as already-computed facts,
  not raw transaction dumps they have to re-derive themselves.

**What makes them trust (or distrust) the output:**
- Trust: a `source_transaction_ids` / `financials_id` list next to every
  figure, re-derivable on demand via `citation.py`.
- Distrust: any number with no citation, or a citation that turns out to be
  wrong when spot-checked (this is exactly what `verify_net_income` in
  `accrual_kpis.py` is built to catch before the user does).

**Primary questions this persona asks, mapped to skills:**
| Question | Skill / function |
|---|---|
| "What's the gross/operating/net margin, and from what?" | `accrual_kpis.margins` |
| "Does this business's bad debt look elevated, and why?" | `accrual_kpis.bad_debt_profile` |
| "Is this net income number actually consistent with its line items?" | `accrual_kpis.verify_net_income` |
| "Where does the depreciation in this month's P&L actually come from?" | `accrual_kpis.da_trend` |
| "Why don't cash receipts and recognized revenue match this month?" | `reconciliation.py` |

## 2. SMB credit/risk underwriter — `cash_flow_room`

**Who:** Someone screening a small business's cash position for
underwriting or risk-monitoring purposes — turning raw cash flow into a
clear stress signal for SMBs that may have only weeks of runway.

**What they need from this tool:**
- A fast, plain-English read on: is this business about to run out of cash,
  is its revenue concentrated in one customer, is anything in its
  transaction history irregular enough to warrant a closer look.
- No raw bank statement scrolling — a short brief with the 3-4 KPIs that
  actually matter for underwriting, each with the rows behind it available
  if they want to dig in.

**What makes them trust (or distrust) the output:**
- Trust: concrete numbers (months of runway, % revenue from top customer,
  count of flagged anomalous transactions) with the underlying transaction
  IDs available, not a vague "this business looks risky" assertion.
- Distrust: a narrative that asserts risk without a KPI backing it, or a KPI
  that doesn't account for a business having $0 starting cash (see the
  `cash_negative` flag design note in `cash_kpis.py` — a documented,
  deliberate choice, not a silent gap).

**Primary questions this persona asks, mapped to skills:**
| Question | Skill / function |
|---|---|
| "How many months of runway does this business have left?" | `cash_kpis.runway_months` |
| "Is revenue dangerously concentrated in one customer?" | `cash_kpis.customer_concentration` |
| "Has this business's cash ever gone negative / near-overdraft?" | `cash_kpis.overdraft_risk` |
| "Are there suspicious round-number or unverified-payee transactions?" | `anomaly_detection.round_number_cluster` |
| "Is anything about this business's transaction pattern statistically odd?" | `anomaly_detection.benford_deviation` |

## 3. Relationship manager / pricing desk — `pricing_room`

**Who:** Someone deciding what to charge an account or what terms to
attach — relationship pricing, covenant requirements, credit-line
decisions. Cares less about the raw KPI and more about the decision it
implies.

**What they need from this tool:**
- A single recommended tier (standard / monitor / restricted) with the
  specific risk flags that drove it — not a KPI dump they have to
  interpret themselves.
- Confidence that the recommendation logic is auditable: a deterministic,
  documented threshold rule, not an opaque model judgment call.

**What makes them trust (or distrust) the output:**
- Trust: a tier recommendation that cites only the flags that actually
  fired — no padding, no vague "elevated risk" without a named reason.
- Distrust: a tier with no citation trail, or a "clean" account (Tier A)
  that still gets a fabricated justification instead of an honest "nothing
  to cite."

**Primary questions this persona asks, mapped to skills:**
| Question | Skill / function |
|---|---|
| "What tier should this account be priced at, and why?" | `pricing_tier.recommend_pricing_tier` |
| "Which specific risk signals drove this recommendation?" | same — `triggered_flags` field |
| "Is this decision auditable after the fact?" | same — citations are the union of only fired flags |

## 4. Shared design constraint across all three personas

No persona should ever see a number the model invented. All three rooms
read the **same underlying principle**: code computes, code cites, the
model narrates only what it's handed. This is the one claim that has to
hold up line-by-line under scrutiny — these personas exist so the artifact
is built *toward* that defense, not just toward a demo that looks good once.
