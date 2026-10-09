"""Build the accrual-basis monthly financials layer.

Reads the cash-basis bank transactions, the capex events, and the
depreciation schedule, and derives one accrual P&L + balance-sheet snapshot
per business per month. Every derived figure is traceable back to its inputs
via explicit source references stored alongside the row (transaction_ids for
cash-basis lines, depreciation_ids for D&A) - the citation skill in
skills/citation.py re-derives these on demand rather than trusting a frozen
string, but storing a compact ref here makes a quick manual audit possible
without re-running the pipeline.

Key design choice: accrual revenue is NOT identical to cash deposits. Each
business gets a fixed recognition lag (0-2 months) so there is a real,
inspectable cash-vs-accrual gap for the reconciliation skill to flag -
mirrors the real-world reason these two views of a business disagree
(revenue recognized on delivery, cash received later).

Output: data/monthly_financials.csv
"""
from __future__ import annotations

import csv
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

random.seed(7)

ROOT = Path(__file__).resolve().parent.parent
TXN_PATH = ROOT / "data" / "transactions.csv"
DEP_PATH = ROOT / "data" / "depreciation_schedule.csv"
OUT_PATH = ROOT / "data" / "monthly_financials.csv"
START = date(2025, 1, 1)
MONTHS = 12

ELEVATED_BAD_DEBT = {"biz_03": 0.045, "biz_22": 0.04}  # concentration / anomaly risk -> worse collections
DEFAULT_BAD_DEBT = 0.012
OPEX_OTHER_CATS = {"vendor_payment", "misc_expense", "taxes", "loan_payment", "bank_fees",
                   "insurance", "software_subscription", "refund"}


def periods() -> list[str]:
    out = []
    for m in range(MONTHS):
        y = START.year + (START.month - 1 + m) // 12
        mm = (START.month - 1 + m) % 12 + 1
        out.append(f"{y}-{mm:02d}")
    return out


def main() -> None:
    with TXN_PATH.open() as f:
        txns = list(csv.DictReader(f))
    with DEP_PATH.open() as f:
        dep_rows = list(csv.DictReader(f))

    businesses = sorted({t["business_id"] for t in txns})
    period_list = periods()

    # cash-basis monthly aggregates per business, keyed by period, plus the ref ids behind each bucket
    cash_rev = defaultdict(lambda: defaultdict(float))
    cash_rev_refs = defaultdict(lambda: defaultdict(list))
    payroll = defaultdict(lambda: defaultdict(float))
    rent = defaultdict(lambda: defaultdict(float))
    opex_other = defaultdict(lambda: defaultdict(float))
    opex_other_refs = defaultdict(lambda: defaultdict(list))

    for t in txns:
        period = t["date"][:7]
        biz = t["business_id"]
        amt = float(t["amount"])
        cat = t["category"]
        if cat == "revenue":
            cash_rev[biz][period] += amt
            cash_rev_refs[biz][period].append(t["transaction_id"])
        elif cat == "payroll":
            payroll[biz][period] += -amt
        elif cat == "rent":
            rent[biz][period] += -amt
        elif cat in OPEX_OTHER_CATS:
            opex_other[biz][period] += -amt
            opex_other_refs[biz][period].append(t["transaction_id"])

    dep_by_biz_period = defaultdict(lambda: defaultdict(float))
    dep_refs = defaultdict(lambda: defaultdict(list))
    for r in dep_rows:
        dep_by_biz_period[r["business_id"]][r["period"]] += float(r["amount"])
        dep_refs[r["business_id"]][r["period"]].append(r["depreciation_id"])

    recognition_lag = {b: random.choice([0, 0, 1, 1, 2]) for b in businesses}  # most 0-1mo, a few 2mo
    cogs_rate = {b: round(random.uniform(0.35, 0.55), 3) for b in businesses}
    dso_days = {b: random.uniform(20, 45) for b in businesses}

    out_rows = []
    for biz in businesses:
        bad_debt_rate = ELEVATED_BAD_DEBT.get(biz, DEFAULT_BAD_DEBT)
        for idx, period in enumerate(period_list):
            lag = recognition_lag[biz]
            src_idx = idx + lag
            src_period = period_list[src_idx] if src_idx < len(period_list) else None
            revenue_accrual = cash_rev[biz].get(src_period, 0.0) if src_period else 0.0
            refs_revenue = cash_rev_refs[biz].get(src_period, []) if src_period else []

            cogs = round(revenue_accrual * cogs_rate[biz], 2)
            opex_payroll = round(payroll[biz].get(period, 0.0), 2)
            opex_rent = round(rent[biz].get(period, 0.0), 2)
            opex_other_amt = round(opex_other[biz].get(period, 0.0), 2)
            opex_other_refs_list = opex_other_refs[biz].get(period, [])
            dep_amt = round(dep_by_biz_period[biz].get(period, 0.0), 2)
            dep_refs_list = dep_refs[biz].get(period, [])
            bad_debt_expense = round(revenue_accrual * bad_debt_rate, 2)

            net_income = round(revenue_accrual - cogs - opex_payroll - opex_rent - opex_other_amt - dep_amt - bad_debt_expense, 2)

            ar_balance = round(revenue_accrual * (dso_days[biz] / 30.0), 2)
            allowance_doubtful = round(ar_balance * bad_debt_rate * 1.5, 2)
            ap_balance = round(opex_other_amt * 0.5, 2)
            accrued_liabilities = round(opex_payroll * 0.1, 2)

            out_rows.append({
                "financials_id": f"{biz}_fin_{period}",
                "business_id": biz,
                "period": period,
                "revenue_accrual": revenue_accrual,
                "revenue_basis_refs": ";".join(refs_revenue),
                "cogs": cogs,
                "opex_payroll": opex_payroll,
                "opex_rent": opex_rent,
                "opex_other": opex_other_amt,
                "opex_other_basis_refs": ";".join(opex_other_refs_list),
                "depreciation_amortization": dep_amt,
                "dep_basis_refs": ";".join(dep_refs_list),
                "bad_debt_expense": bad_debt_expense,
                "bad_debt_rate_assumption": bad_debt_rate,
                "net_income": net_income,
                "ar_balance": ar_balance,
                "allowance_doubtful_accounts": allowance_doubtful,
                "ap_balance": ap_balance,
                "accrued_liabilities": accrued_liabilities,
                "recognition_lag_months": lag,
            })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(out_rows[0].keys())
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    print(f"Wrote {len(out_rows)} business-month financial rows -> {OUT_PATH}")


if __name__ == "__main__":
    main()
