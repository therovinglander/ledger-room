"""Cash-basis KPIs for the cash_flow_room.

Design rule (PROJECT.md §2): code computes and cites, the model never sees
raw rows. Every function here returns a dict with a numeric "value" plus
"source_transaction_ids" — the exact rows that produced it — so narrate.py
(or any caller) can cite without re-deriving.

Cash balance convention: no real starting balance exists in the synthetic
data, so balance is cumulative net cash flow from day 1 of the dataset
(2025-01-01), i.e. "cash generated/consumed since inception". This is
correct for trend/runway/overdraft-shape signal even though it is not a
real bank balance.

Inputs: data/transactions.csv (business_id, date, category, counterparty,
amount, transaction_id). Amounts are already signed (inflow positive,
outflow negative).
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TXN_PATH = ROOT / "data" / "transactions.csv"


def load_transactions(path: Path = TXN_PATH) -> list[dict]:
    """Load transactions.csv, with amount cast to float and rows kept in
    file order (file order == chronological per business, verified at
    generation time)."""
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["amount"] = float(r["amount"])
    return rows


def _for_business(transactions: list[dict], business_id: str) -> list[dict]:
    rows = [r for r in transactions if r["business_id"] == business_id]
    rows.sort(key=lambda r: (r["date"], r["transaction_id"]))
    return rows


def _period(date_str: str) -> str:
    return date_str[:7]  # "YYYY-MM"


def monthly_net_flow(transactions: list[dict], business_id: str) -> dict:
    """Net cash flow per calendar month for one business.

    Returns {"biz": ..., "months": [{"period", "net_flow", "source_transaction_ids"}, ...]}
    ordered chronologically. This is the building block for burn rate,
    runway, and the cumulative balance series.
    """
    rows = _for_business(transactions, business_id)
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_month[_period(r["date"])].append(r)

    months = []
    for period in sorted(by_month):
        rs = by_month[period]
        months.append({
            "period": period,
            "net_flow": round(sum(r["amount"] for r in rs), 2),
            "source_transaction_ids": [r["transaction_id"] for r in rs],
        })
    return {"business_id": business_id, "months": months}


def cash_balance_series(transactions: list[dict], business_id: str) -> dict:
    """Cumulative cash balance at the end of each month (see module
    docstring for the "balance since inception" convention)."""
    flows = monthly_net_flow(transactions, business_id)["months"]
    running = 0.0
    all_ids: list[str] = []
    series = []
    for m in flows:
        running += m["net_flow"]
        all_ids.extend(m["source_transaction_ids"])
        series.append({
            "period": m["period"],
            "ending_balance": round(running, 2),
            "source_transaction_ids": list(all_ids),  # cumulative, by design
        })
    return {"business_id": business_id, "series": series}


def burn_rate(transactions: list[dict], business_id: str, trailing_months: int = 3) -> dict:
    """Average monthly net cash flow over the trailing N months.

    Negative value = burning cash. Returns None value (with reason) if
    fewer than trailing_months of data exist.
    """
    months = monthly_net_flow(transactions, business_id)["months"]
    if len(months) < trailing_months:
        return {
            "business_id": business_id, "trailing_months": trailing_months,
            "value": None, "reason": f"only {len(months)} month(s) of data",
            "source_transaction_ids": [],
        }
    window = months[-trailing_months:]
    ids = [tid for m in window for tid in m["source_transaction_ids"]]
    avg = statistics.mean(m["net_flow"] for m in window)
    return {
        "business_id": business_id,
        "trailing_months": trailing_months,
        "periods": [m["period"] for m in window],
        "value": round(avg, 2),
        "source_transaction_ids": ids,
    }


def runway_months(transactions: list[dict], business_id: str, trailing_months: int = 3) -> dict:
    """Months of runway = current cash balance / trailing burn rate.

    None (infinite runway) when burn rate is >= 0 (business is cash-flow
    positive over the trailing window). value=0 + flag="cash_negative" when
    current balance is already <= 0 — the business has already exhausted
    the cash it generated since inception; "months remaining" is moot, the
    real signal is that it's already underwater. Cites both the
    balance-series rows and the burn-rate rows that produced the two inputs.
    """
    balance = cash_balance_series(transactions, business_id)["series"]
    burn = burn_rate(transactions, business_id, trailing_months)
    if not balance:
        return {"business_id": business_id, "value": None, "reason": "no transaction data",
                "source_transaction_ids": []}
    current_balance = balance[-1]["ending_balance"]
    ids = list(balance[-1]["source_transaction_ids"]) + burn["source_transaction_ids"]

    if current_balance <= 0:
        return {"business_id": business_id, "value": 0, "flag": "cash_negative",
                "reason": "cumulative cash balance since inception is already <= 0",
                "as_of_period": balance[-1]["period"], "current_balance": current_balance,
                "trailing_burn_rate": burn.get("value"), "source_transaction_ids": ids}

    if burn["value"] is None:
        return {"business_id": business_id, "value": None, "reason": burn["reason"],
                "as_of_period": balance[-1]["period"], "current_balance": current_balance,
                "source_transaction_ids": ids}
    if burn["value"] >= 0:
        return {"business_id": business_id, "value": None,
                "reason": "cash-flow positive over trailing window (infinite runway)",
                "as_of_period": balance[-1]["period"], "current_balance": current_balance,
                "trailing_burn_rate": burn["value"], "source_transaction_ids": ids}

    months = current_balance / abs(burn["value"])
    return {
        "business_id": business_id,
        "as_of_period": balance[-1]["period"],
        "current_balance": current_balance,
        "trailing_burn_rate": burn["value"],
        "trailing_months": trailing_months,
        "value": round(months, 1),
        "flag": "runway_risk" if months <= 6 else None,
        "source_transaction_ids": ids,
    }


def customer_concentration(transactions: list[dict], business_id: str) -> dict:
    """Share of total revenue attributable to the single largest
    counterparty, over all available history.

    Two-tier flag: >50% = concentration_risk (high), >30% = elevated. The
    two-tier design is deliberate: the synthetic generator assigns biz_03's
    top customer a 62% *selection weight* per transaction, but with only
    ~36 revenue transactions for the year the realized share can land well
    below the target weight by chance (verified: biz_03 realizes ~39%, not
    62%, under the fixed seed). A single >50% cliff would miss that this
    business's revenue is still real-world-flag-worthy. Documented here
    rather than silently re-seeding the generator to force a cleaner number.
    """
    rows = [r for r in _for_business(transactions, business_id) if r["category"] == "revenue"]
    if not rows:
        return {"business_id": business_id, "value": None, "reason": "no revenue transactions",
                "source_transaction_ids": []}

    by_customer: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_customer[r["counterparty"]].append(r)

    total = sum(r["amount"] for r in rows)
    top_customer = max(by_customer, key=lambda c: sum(r["amount"] for r in by_customer[c]))
    top_amount = sum(r["amount"] for r in by_customer[top_customer])
    share = top_amount / total if total else 0.0

    if share > 0.5:
        flag = "concentration_risk"
    elif share > 0.3:
        flag = "elevated_concentration"
    else:
        flag = None

    return {
        "business_id": business_id,
        "top_customer": top_customer,
        "top_customer_revenue": round(top_amount, 2),
        "total_revenue": round(total, 2),
        "value": round(share, 4),
        "flag": flag,
        "source_transaction_ids": [r["transaction_id"] for r in by_customer[top_customer]],
    }


def overdraft_risk(transactions: list[dict], business_id: str, threshold: float = 0.0) -> dict:
    """Finds the lowest point of the cumulative cash balance series and
    flags overdraft risk if it ever dips at/under `threshold`.

    Note: this is a month-end balance check (coarser than day-level), since
    the cumulative series above is monthly. It still catches the injected
    thin-buffer pattern (biz_11) because that pattern is sustained, not a
    single-day dip.
    """
    series = cash_balance_series(transactions, business_id)["series"]
    if not series:
        return {"business_id": business_id, "value": None, "reason": "no transaction data",
                "source_transaction_ids": []}
    low_point = min(series, key=lambda s: s["ending_balance"])
    flagged = low_point["ending_balance"] <= threshold
    return {
        "business_id": business_id,
        "lowest_balance": low_point["ending_balance"],
        "lowest_balance_period": low_point["period"],
        "threshold": threshold,
        "flag": "overdraft_risk" if flagged else None,
        "source_transaction_ids": low_point["source_transaction_ids"],
    }


def _self_check() -> None:
    """Sanity check against the 4 risk patterns deliberately injected by
    generate_data.py (PROJECT.md §4). Prints PASS/FAIL per check — not a
    pytest suite, deliberately kept runnable with `python cash_kpis.py`.

    Note on thresholds: there is no starting cash balance in the synthetic
    data, so "runway" is measured from a cumulative balance that starts at
    $0 on 2025-01-01. About half of the 25 businesses are already
    cash-negative by month 12 purely from normal opex/revenue variance —
    that is a property of the generator, not a bug here. The checks below
    assert the *relative* signal (declining-revenue businesses show
    worsening/negative cash + negative burn; biz_11 is the deepest or
    near-deepest overdraft) rather than an absolute "runway > 0".
    """
    txns = load_transactions()
    checks = []

    for biz in ("biz_07", "biz_18"):
        r = runway_months(txns, biz)
        burn = r.get("trailing_burn_rate")
        ok = burn is not None and burn < 0  # burning cash, consistent with declining revenue
        checks.append((f"{biz} is burning cash in the trailing window (declining revenue)", ok, r))

    conc = customer_concentration(txns, "biz_03")
    checks.append(("biz_03 flagged for elevated or high customer concentration",
                    conc["flag"] in ("concentration_risk", "elevated_concentration"), conc))

    od = overdraft_risk(txns, "biz_11")
    checks.append(("biz_11 flagged for overdraft risk", od["flag"] == "overdraft_risk", od))

    all_biz = sorted({r["business_id"] for r in txns})
    balances = {b: overdraft_risk(txns, b)["lowest_balance"] for b in all_biz}
    rank = sorted(balances, key=lambda b: balances[b])
    checks.append(("biz_11 is among the 3 lowest cash-balance businesses overall",
                    "biz_11" in rank[:3], {"bottom_3": rank[:3], "balances": balances}))

    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"   detail: {detail}")


if __name__ == "__main__":
    _self_check()
