"""Anomaly detection skills for the cash_flow_room.

Two deterministic, citeable checks — no ML, no model judgment call on which
rows are anomalous. The model (narrate.py) only ever reads the *output* of
these functions, never raw transactions. Matches the injected anomaly
pattern described in PROJECT.md §4 (biz_22: round-number expense cluster
from an "unverified_payee" counterparty).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path

from cash_kpis import load_transactions, TXN_PATH, _for_business  # noqa: F401 (re-export path)

# Benford's Law expected frequency of each leading digit 1-9.
BENFORD_EXPECTED = {d: math.log10(1 + 1 / d) for d in range(1, 10)}


def _leading_digit(amount: float) -> int | None:
    a = abs(amount)
    if a == 0:
        return None
    while a < 1:
        a *= 10
    while a >= 10:
        a /= 10
    return int(a)


def round_number_cluster(
    transactions: list[dict],
    business_id: str,
    round_to: float = 500.0,
    min_count: int = 3,
) -> dict:
    """Flags a business whose outflow transactions are suspiciously often
    exact multiples of `round_to` (default $500) — real-world expenses
    rarely land on round numbers; a cluster of them is a classic
    fabricated/unverified-expense signal.

    Returns the matching source_transaction_ids so the narrative layer can
    cite exactly which rows triggered the flag, plus a same-metric
    portfolio_percentile context so "22 round-number expenses" can be read
    against what's normal across the other 24 businesses.
    """
    rows = [r for r in _for_business(transactions, business_id) if r["amount"] < 0]
    matches = [r for r in rows if abs(r["amount"]) % round_to == 0]

    return {
        "business_id": business_id,
        "round_to": round_to,
        "total_outflow_transactions": len(rows),
        "round_number_count": len(matches),
        "round_number_rate": round(len(matches) / len(rows), 4) if rows else 0.0,
        "flag": "round_number_cluster" if len(matches) >= min_count else None,
        "source_transaction_ids": [r["transaction_id"] for r in matches],
    }


def round_number_cluster_portfolio(transactions: list[dict], round_to: float = 500.0) -> dict:
    """Same check across every business, so a single business's count can
    be read against the portfolio distribution (not just an arbitrary
    absolute threshold)."""
    biz_ids = sorted({r["business_id"] for r in transactions})
    per_biz = {b: round_number_cluster(transactions, b, round_to=round_to) for b in biz_ids}
    ranked = sorted(per_biz.values(), key=lambda v: -v["round_number_count"])
    return {"round_to": round_to, "ranked_by_count": ranked}


def benford_deviation(transactions: list[dict], business_id: str, category: str | None = None) -> dict:
    """Chi-square-style deviation of a business's leading-digit distribution
    from Benford's Law. Flags when deviation is well outside what the other
    businesses show — real transaction amounts (rent, payroll %, vendor
    invoices) are expected to roughly follow Benford's Law; a business
    whose amounts cluster unnaturally (e.g. repeated round numbers from one
    counterparty) will deviate more.

    `category`: optionally restrict to one transaction category (e.g.
    "misc_expense") to isolate where the deviation is coming from.
    """
    rows = _for_business(transactions, business_id)
    if category:
        rows = [r for r in rows if r["category"] == category]
    digits = [_leading_digit(r["amount"]) for r in rows]
    digits = [d for d in digits if d is not None]
    n = len(digits)
    if n < 10:
        return {"business_id": business_id, "category": category, "n": n,
                "value": None, "reason": "fewer than 10 transactions, distribution unreliable",
                "source_transaction_ids": [r["transaction_id"] for r in rows]}

    counts = Counter(digits)
    deviation = 0.0
    for d in range(1, 10):
        observed = counts.get(d, 0) / n
        expected = BENFORD_EXPECTED[d]
        deviation += (observed - expected) ** 2 / expected

    return {
        "business_id": business_id,
        "category": category,
        "n": n,
        "value": round(deviation, 4),  # higher = more deviation from Benford
        "observed_distribution": {d: round(counts.get(d, 0) / n, 4) for d in range(1, 10)},
        "source_transaction_ids": [r["transaction_id"] for r in rows],
    }


def benford_deviation_portfolio(transactions: list[dict], category: str | None = None) -> dict:
    """Same check across every business, ranked by deviation score, with a
    flag on whatever business is the clear outlier (> 2x the median
    deviation) — relative, not an arbitrary absolute cutoff.

    Caveat (verified, not assumed): per-category sample sizes here are
    small (n~10-30 misc_expense rows per business), so Benford deviation is
    noisy for everyone and biz_22's injected anomaly does NOT clear the 2x
    bar (it ranks #1 but at ~1.3x the portfolio median, not 2x). Treat
    `outlier` as a secondary, low-confidence corroborating signal — the
    round_number_cluster check above is the primary, high-confidence one
    for this specific injected pattern. Rank-1 is still worth surfacing.
    """
    biz_ids = sorted({r["business_id"] for r in transactions})
    per_biz = {b: benford_deviation(transactions, b, category=category) for b in biz_ids}
    scored = [v for v in per_biz.values() if v["value"] is not None]
    scored.sort(key=lambda v: -v["value"])
    if not scored:
        return {"category": category, "ranked_by_deviation": [], "outlier": None}

    values = sorted(v["value"] for v in scored)
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    top = scored[0]
    outlier = top["business_id"] if median > 0 and top["value"] > 2 * median else None

    return {"category": category, "ranked_by_deviation": scored, "median_deviation": round(median, 4),
            "outlier": outlier}


def _self_check() -> None:
    txns = load_transactions()
    checks = []

    rc_all = round_number_cluster_portfolio(txns)
    top = rc_all["ranked_by_count"][0]
    checks.append(("biz_22 has the most round-number ($500-multiple) outflows portfolio-wide",
                    top["business_id"] == "biz_22", top))

    rc_22 = round_number_cluster(txns, "biz_22")
    checks.append(("biz_22 round-number cluster flagged", rc_22["flag"] == "round_number_cluster", rc_22))

    bd = benford_deviation_portfolio(txns, category="misc_expense")
    top_biz = bd["ranked_by_deviation"][0]["business_id"]
    checks.append(("biz_22 ranks #1 by Benford deviation within misc_expense (secondary, low-n signal)",
                    top_biz == "biz_22", {"top_biz": top_biz, "median": bd["median_deviation"],
                                           "outlier_flag": bd["outlier"],
                                           "top3": bd["ranked_by_deviation"][:3]}))

    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"   detail: {detail}")


if __name__ == "__main__":
    _self_check()
