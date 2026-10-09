"""Generate synthetic SMB transaction + capex data for the cash-signal demo.

No real bank data used. 25 synthetic businesses, 12 months of transactions.

Risk patterns deliberately injected (consumed by the cash_kpis / anomaly_detection skills):
  - biz_07, biz_18: declining revenue trend (runway risk)
  - biz_03: single-customer concentration >60% of revenue
  - biz_22: injected round-number transaction cluster (Benford-style anomaly)
  - biz_11: thin/negative cash buffer with payroll near due dates (overdraft risk)

Two outputs:
  data/transactions.csv   - every cash-basis bank transaction, stable transaction_id per row
  data/capex_events.csv   - equipment/asset purchases, consumed by generate_fixed_assets.py
    to build the accrual-side depreciation schedule (depreciation itself is NEVER a bank
    transaction - it's derived from these capex events in the accrual layer, not injected
    into the cash feed).
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

ROOT = Path(__file__).resolve().parent.parent
TXN_PATH = ROOT / "data" / "transactions.csv"
CAPEX_PATH = ROOT / "data" / "capex_events.csv"
N_BUSINESSES = 25
MONTHS = 12
START = date(2025, 1, 1)

SPARSE_EXPENSE_CATEGORIES = [
    "vendor_payment", "misc_expense", "taxes", "loan_payment",
    "bank_fees", "insurance", "software_subscription",
]


def daterange_days(start: date, months: int) -> list[date]:
    days = []
    d = start
    end_year = start.year + (start.month - 1 + months) // 12
    end_month = (start.month - 1 + months) % 12 + 1
    end = date(end_year, end_month, 1)
    while d < end:
        days.append(d)
        d += timedelta(days=1)
    return days


def gen_business(biz_id: str, days: list[date]) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    capex: list[dict] = []
    txn_seq = 0
    capex_seq = 0

    base_revenue = random.uniform(8000, 40000)
    revenue_trend = 1.0
    declining = biz_id in ("biz_07", "biz_18")
    concentrated = biz_id == "biz_03"
    anomalous = biz_id == "biz_22"
    thin_buffer = biz_id == "biz_11"

    customers = [f"cust_{biz_id}_{i}" for i in range(1, 9)]
    customer_weights = (
        [0.62, 0.06, 0.06, 0.06, 0.05, 0.05, 0.05, 0.05] if concentrated
        else [1 / len(customers)] * len(customers)
    )

    def next_txn_id() -> str:
        nonlocal txn_seq
        txn_seq += 1
        return f"{biz_id}_t{txn_seq:05d}"

    for d in days:
        month_idx = (d.year - START.year) * 12 + (d.month - START.month)
        if declining:
            revenue_trend = max(0.35, 1.0 - 0.055 * month_idx)

        if d.day in (5, 15, 25):
            amount = round(base_revenue * revenue_trend * random.uniform(0.25, 0.45), 2)
            customer = random.choices(customers, weights=customer_weights, k=1)[0]
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": "revenue", "counterparty": customer, "amount": amount})

        if d.day in (1, 15):
            payroll_amt = round(base_revenue * 0.35 * random.uniform(0.9, 1.05), 2)
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": "payroll", "counterparty": "payroll_provider", "amount": -payroll_amt})

        if d.day == 1:
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": "rent", "counterparty": "landlord", "amount": -round(base_revenue * 0.08, 2)})

        # monthly interest: small income on cash buffer, small expense on any loan balance
        if d.day == 28:
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": "interest_income", "counterparty": "bank", "amount": round(random.uniform(5, 60), 2)})
            if thin_buffer or random.random() < 0.3:
                rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                             "category": "interest_expense", "counterparty": "lender",
                             "amount": -round(random.uniform(40, 300), 2)})

        # occasional owner draw
        if d.day == 30 and random.random() < 0.4:
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": "owner_draw", "counterparty": "owner", "amount": -round(base_revenue * 0.05, 2)})

        # occasional customer refund
        if random.random() < 0.015:
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": "refund", "counterparty": random.choice(customers),
                         "amount": -round(random.uniform(100, 900), 2)})

        if random.random() < 0.08:
            cat = random.choice(SPARSE_EXPENSE_CATEGORIES)
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": cat, "counterparty": f"{cat}_vendor", "amount": -round(random.uniform(150, 2500), 2)})

        if anomalous and d.day in (10, 20) and random.random() < 0.7:
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": "misc_expense", "counterparty": "unverified_payee",
                         "amount": -float(random.choice([5000, 10000, 2500, 7500]))})

        if thin_buffer and d.day in (2, 16):
            rows.append({"transaction_id": next_txn_id(), "business_id": biz_id, "date": d.isoformat(),
                         "category": "loan_payment", "counterparty": "short_term_lender",
                         "amount": -round(base_revenue * 0.15, 2)})

        # capex: 1-2 equipment purchases per year, cash outflow now, depreciated later in the accrual layer
        if d.month in (3, 9) and d.day == 10 and random.random() < 0.5:
            capex_seq += 1
            amt = round(random.uniform(4000, 35000), 2)
            txn_id = next_txn_id()
            rows.append({"transaction_id": txn_id, "business_id": biz_id, "date": d.isoformat(),
                         "category": "equipment_purchase", "counterparty": "equipment_vendor", "amount": -amt})
            capex.append({"capex_id": f"{biz_id}_cx{capex_seq:03d}", "business_id": biz_id,
                          "purchase_date": d.isoformat(), "amount": amt, "source_transaction_id": txn_id,
                          "useful_life_years": random.choice([3, 5, 7, 10])})

    return rows, capex


def main() -> None:
    days = daterange_days(START, MONTHS)
    all_rows: list[dict] = []
    all_capex: list[dict] = []
    for i in range(1, N_BUSINESSES + 1):
        biz_id = f"biz_{i:02d}"
        rows, capex = gen_business(biz_id, days)
        all_rows.extend(rows)
        all_capex.extend(capex)

    TXN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TXN_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["transaction_id", "business_id", "date", "category", "counterparty", "amount"])
        w.writeheader()
        w.writerows(all_rows)

    with CAPEX_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["capex_id", "business_id", "purchase_date", "amount", "source_transaction_id", "useful_life_years"])
        w.writeheader()
        w.writerows(all_capex)

    print(f"Wrote {len(all_rows)} transactions -> {TXN_PATH}")
    print(f"Wrote {len(all_capex)} capex events -> {CAPEX_PATH}")


if __name__ == "__main__":
    main()
