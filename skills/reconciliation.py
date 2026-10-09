"""Cash-vs-accrual reconciliation for the filing_extraction_room (Daloopa lens).

The data layer has a deliberate, inspectable gap between cash and accrual
revenue (src/generate_financials.py: each business gets a fixed 0-2 month
recognition lag — revenue recognized in period P was actually collected in
cash in period P-lag). This skill makes that gap explicit, per period, with
citations on BOTH sides: the cash transactions that were collected in a
period, and the accrual-basis row (with its own revenue_basis_refs) that
recognizes revenue for that same period.

This is the concrete, demoable version of the Daloopa talking point "here
is exactly where these two views of the business disagree, and why."
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from cash_kpis import load_transactions, TXN_PATH  # noqa: F401
from accrual_kpis import load_financials, FIN_PATH, _for_business as _fin_for_business  # noqa: F401


def _cash_revenue_by_period(transactions: list[dict], business_id: str) -> dict[str, dict]:
    rows = [r for r in transactions if r["business_id"] == business_id and r["category"] == "revenue"]
    by_period: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_period[r["date"][:7]].append(r)
    return {
        period: {
            "cash_revenue": round(sum(r["amount"] for r in rs), 2),
            "source_transaction_ids": [r["transaction_id"] for r in rs],
        }
        for period, rs in by_period.items()
    }


def revenue_reconciliation(transactions: list[dict], financials: list[dict], business_id: str) -> dict:
    """Period-by-period cash-vs-accrual revenue gap for one business.

    gap = cash_revenue_collected_this_period - accrual_revenue_recognized_this_period.
    A nonzero gap in a mid-year period is EXPECTED (that's the recognition
    lag working as designed) — it's not itself an anomaly. The point of this
    skill is to make the gap visible and cited, not to flag it as wrong.
    """
    cash_by_period = _cash_revenue_by_period(transactions, business_id)
    fin_rows = _fin_for_business(financials, business_id)

    periods = []
    for row in fin_rows:
        period = row["period"]
        cash = cash_by_period.get(period, {"cash_revenue": 0.0, "source_transaction_ids": []})
        accrual_refs = row["revenue_basis_refs"].split(";") if row["revenue_basis_refs"] else []
        gap = round(cash["cash_revenue"] - row["revenue_accrual"], 2)
        periods.append({
            "period": period,
            "cash_revenue": cash["cash_revenue"],
            "cash_revenue_source_transaction_ids": cash["source_transaction_ids"],
            "accrual_revenue": row["revenue_accrual"],
            "accrual_revenue_source_transaction_ids": accrual_refs,
            "recognition_lag_months": row["recognition_lag_months"],
            "gap": gap,
        })

    return {"business_id": business_id, "periods": periods}


def reconciliation_summary(transactions: list[dict], financials: list[dict], business_id: str) -> dict:
    """Rolls the per-period reconciliation up into one number: total cash
    revenue collected over the full window vs total accrual revenue
    recognized. These should differ only by the "edge effect" of the
    recognition lag (the first `lag` months of accrual revenue have no
    prior-year cash to draw from, and the last `lag` months of cash never
    get recognized as accrual revenue within this 12-month window) — this
    function reports both totals and leaves the lag-driven edge gap
    visible and explained, rather than treating it as reconciled to zero.
    """
    detail = revenue_reconciliation(transactions, financials, business_id)["periods"]
    total_cash = round(sum(p["cash_revenue"] for p in detail), 2)
    total_accrual = round(sum(p["accrual_revenue"] for p in detail), 2)
    lag = detail[0]["recognition_lag_months"] if detail else 0
    all_ids = [tid for p in detail for tid in p["cash_revenue_source_transaction_ids"]]

    return {
        "business_id": business_id,
        "total_cash_revenue": total_cash,
        "total_accrual_revenue": total_accrual,
        "gap": round(total_cash - total_accrual, 2),
        "recognition_lag_months": lag,
        "explanation": (
            f"Expected edge gap from a {lag}-month recognition lag at the "
            "start/end of the 12-month window (no prior-year cash to "
            "recognize into January, and late-year cash not yet recognized "
            "within this window)." if lag else
            "No recognition lag for this business; cash and accrual revenue "
            "should match period-for-period."
        ),
        "source_transaction_ids": all_ids,
    }


def _self_check() -> None:
    txns = load_transactions()
    fin = load_financials()
    checks = []

    biz = "biz_01"
    detail = revenue_reconciliation(txns, fin, biz)
    nonzero_gap_months = [p for p in detail["periods"] if p["gap"] != 0]
    checks.append((f"{biz} shows at least one period with a cash-vs-accrual revenue gap",
                    len(nonzero_gap_months) > 0, {"nonzero_gap_count": len(nonzero_gap_months)}))

    summ = reconciliation_summary(txns, fin, biz)
    checks.append((f"{biz} reconciliation summary carries a non-empty source citation list",
                    len(summ["source_transaction_ids"]) > 0, {"n_ids": len(summ["source_transaction_ids"])}))

    # A business with 0-month lag should reconcile period-for-period exactly.
    zero_lag_biz = next(
        (b for b in sorted({r["business_id"] for r in fin})
         if _fin_for_business(fin, b)[0]["recognition_lag_months"] == 0),
        None,
    )
    if zero_lag_biz:
        zl_detail = revenue_reconciliation(txns, fin, zero_lag_biz)["periods"]
        all_zero = all(p["gap"] == 0 for p in zl_detail)
        checks.append((f"{zero_lag_biz} (0-month lag) reconciles to $0 gap every period",
                        all_zero, {"nonzero_periods": [p for p in zl_detail if p["gap"] != 0]}))

    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"   detail: {detail}")


if __name__ == "__main__":
    _self_check()
