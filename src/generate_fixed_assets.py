"""Build the accrual-side depreciation schedule from capex events.

Depreciation is derived here, not injected into the bank feed - it is a
straight-line allocation of each capex purchase (data/capex_events.csv) over
its useful life. Every monthly depreciation row cites the capex_id (and
transitively, via capex_events.csv, the original source_transaction_id) it
came from, so the chain purchase -> asset -> monthly expense is fully
traceable.

Output: data/depreciation_schedule.csv
  depreciation_id, business_id, period (YYYY-MM), capex_id, amount
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPEX_PATH = ROOT / "data" / "capex_events.csv"
OUT_PATH = ROOT / "data" / "depreciation_schedule.csv"
HORIZON_MONTHS = 12  # matches the 2025 transaction horizon; schedule runs through year-end
START = date(2025, 1, 1)


def month_add(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, 1)


def main() -> None:
    with CAPEX_PATH.open() as f:
        capex_rows = list(csv.DictReader(f))

    out_rows = []
    seq = defaultdict(int)
    for row in capex_rows:
        biz_id = row["business_id"]
        amount = float(row["amount"])
        life_years = int(row["useful_life_years"])
        monthly_dep = round(amount / (life_years * 12), 2)
        purchase_date = date.fromisoformat(row["purchase_date"])
        # depreciation starts the month AFTER purchase, runs to horizon end or useful life end, whichever first
        start_period = month_add(purchase_date, 1)
        for m in range(HORIZON_MONTHS):
            period_date = month_add(START, m)
            if period_date < start_period:
                continue
            months_since_start = (period_date.year - start_period.year) * 12 + (period_date.month - start_period.month)
            if months_since_start >= life_years * 12:
                break
            seq[biz_id] += 1
            out_rows.append({
                "depreciation_id": f"{biz_id}_dep{seq[biz_id]:04d}",
                "business_id": biz_id,
                "period": period_date.strftime("%Y-%m"),
                "capex_id": row["capex_id"],
                "amount": monthly_dep,
            })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["depreciation_id", "business_id", "period", "capex_id", "amount"])
        w.writeheader()
        w.writerows(out_rows)

    print(f"Wrote {len(out_rows)} depreciation rows -> {OUT_PATH}")


if __name__ == "__main__":
    main()
