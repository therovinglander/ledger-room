# Ledger Room — Demo Script (Zafin event, phone-in-hand)

**Context:** Hallway/event demo, 2-3 minutes, phone screen, one-on-one or small
group. Audience: banking product/pricing/relationship people — they think in
"governed decisions," "auditability," "relationship pricing," not in
"KPIs" or "LLM accuracy." Pitch accordingly. Daloopa/Relay framing is gone;
this is now a general governed-decisioning demo.

File to open: `ledger_room_demo_nojs.html` (clickable with no JS execution —
safe on any phone/QuickLook).

---

## 0. One-line hook (before you even open the phone)

> "I built a small demo of something your Deal Manager / governed-AI pitch
> needs everywhere: an LLM that explains a number, but can never make one up.
> Thirty seconds?"

Wait for a yes. Don't explain further until the phone is out.

---

## 1. Open on Cash Flow Room (10s)

Phone open, `biz_07` tab already selected by default. Don't narrate the UI —
narrate the finding:

> "This is a small business's cash position, auto-generated from raw bank
> transactions. Zero cash runway, $62k overdraft at the low point."

Point at the narrative paragraph, not the KPI cards.

## 2. Tap a citation chip (15s) — THE MOMENT

Tap/point at one of the `biz_07_t00001` style chips inline in the narrative.

> "Every single number in that sentence — not just the total, each one — is
> tied to a literal transaction ID. The model that wrote this sentence never
> saw a raw bank row. It was handed a fact and a citation, and a second piece
> of code re-checks its output afterward and rejects anything it can't trace
> back. If the model hallucinates a number, the system catches it before a
> human ever sees it."

This is the core differentiator. Say it slowly. Everything else is garnish.

## 3. Switch business pill → biz_22 (10s)

Tap the `biz_22` pill.

> "Different business, same engine — this one's got a round-number
> transaction cluster, a classic manual-override red flag, also cited."

## 4. Switch tab → Filing Extraction Room (15s)

Tap the "Filing Extraction Room" tab, business defaults to `biz_03`.

> "Same spine, different lens — this one's margins and bad-debt rate,
> accrual accounting instead of cash. Same contract: every percentage here
> traces to a row in the financial statement data, not a guess."

Point at the "✓ Net Income Integrity: verified" line.

> "And this line isn't cosmetic — it's a real reconciliation check that ran
> against all 300 rows before anything got narrated."

## 5. Switch tab → Pricing Room (20s) — close strong

Tap the "Pricing Room" tab, business defaults to `biz_11`.

> "Same facts, but now it's a decision, not just a number: a pricing tier
> recommendation. This business gets Tier C — elevated pricing, deposit
> covenant required — because two risk flags fired: cash-negative runway and
> an overdraft. The tier logic itself is a simple, documented threshold rule
> in code, zero model involvement in the decision. The model's only job is
> explaining it afterward, and it's held to the same citation contract as
> everything else."

Switch to `biz_03` pill.

> "Same Tier C outcome, completely different reason — this one's not a cash
> problem, it's customer concentration plus bad debt. The citations prove
> which flags actually drove it, every time."

Switch to `biz_13` pill.

> "And when nothing's wrong, it says so — Tier A, standard pricing, zero
> flags, zero citation. It doesn't manufacture a justification when there's
> nothing to justify."

## 6. The pivot line (10s)

> "This is a toy dataset, built in a weekend. The pattern underneath it —
> compute in code, cite everything, validate the narration before it ships —
> is what 'governed AI' means for pricing and relationship decisions, not
> just KPI extraction. Is this a real gap for you, or is Zafin's Deal Manager
> already doing this?"

Let them answer. This is the actual ask — their reaction to the pricing-room
*pattern* is the signal you're collecting, not admiration of the demo. It's
a real working room now, not a sketch — so take their answer as "is this
worth productionizing," not "would this be useful if it existed."

---

## Fallback lines (if asked)

- **"Is this real bank data?"** → "No — fully synthetic, 25 generated
  businesses, labeled as such in the README. No real financial data touches
  this."
- **"What LLM is narrating this?"** → "Right now a deterministic offline
  template for the demo — the citation/validation layer is backend-agnostic,
  so swapping in a real model doesn't change the guarantee: the check that
  rejects an uncited number runs on the output regardless of what produced
  it."
- **"How do you know it actually catches hallucinations?"** → "Found two real
  bugs this way during build — a dropped KPI and a validator blind spot on
  margins past -100%. Both caught because the check flagged them, not because
  I eyeballed the output."

## Don'ts

- Don't open with "I built this for a Daloopa/Relay interview" — dead
  context, wastes their attention budget.
- Don't walk through every KPI card — the narrative + citation chip is the
  whole point; the cards are supporting detail, not the demo.
- Don't over-explain the mock-backend caveat unless asked — it's an honest
  answer to a direct question, not a disclaimer to lead with.
