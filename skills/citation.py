"""citation.py — re-derive source row IDs for any KPI on demand.

Design intent (PROJECT.md §7 item 3): given a KPI name + business + period,
RE-RUN the deterministic calculation and return its citations, rather than
reading a frozen string stored somewhere. This is the difference between
"trust this citation because it's written down" and "trust this citation
because re-running the calculation right now produces the same rows" — the
latter is what catches a stale or hand-edited citation.

This module is a thin dispatcher over the other skills — it owns no
calculation logic of its own, only the KPI-name -> function mapping and the
re-derive-and-compare contract.
"""
from __future__ import annotations

from typing import Callable

import accrual_kpis
import anomaly_detection
import cash_kpis
import reconciliation

# KPI name -> (loader-kind, callable). loader-kind selects which raw table(s)
# the callable needs, so cite() can load the right inputs itself rather than
# trusting a caller-supplied dataset (that would reopen the same "trust a
# frozen value" hole this module exists to close).
_CASH_KPIS: dict[str, Callable] = {
    "runway_months": cash_kpis.runway_months,
    "burn_rate": cash_kpis.burn_rate,
    "customer_concentration": cash_kpis.customer_concentration,
    "overdraft_risk": cash_kpis.overdraft_risk,
}
_ANOMALY_KPIS: dict[str, Callable] = {
    "round_number_cluster": anomaly_detection.round_number_cluster,
    "benford_deviation": anomaly_detection.benford_deviation,
}
_ACCRUAL_KPIS: dict[str, Callable] = {
    "margins": accrual_kpis.margins,
    "bad_debt_profile": accrual_kpis.bad_debt_profile,
    "allowance_adequacy": accrual_kpis.allowance_adequacy,
    "da_trend": accrual_kpis.da_trend,
}
_RECONCILIATION_KPIS: dict[str, Callable] = {
    "revenue_reconciliation": reconciliation.revenue_reconciliation,
    "reconciliation_summary": reconciliation.reconciliation_summary,
}

KNOWN_KPIS = {**_CASH_KPIS, **_ANOMALY_KPIS, **_ACCRUAL_KPIS, **_RECONCILIATION_KPIS}


def cite(kpi_name: str, business_id: str, **kwargs) -> dict:
    """Re-run `kpi_name` for `business_id` right now and return its result,
    including source_transaction_ids. Raises ValueError for an unknown KPI
    name rather than silently returning nothing — a citation tool that
    fails open is worse than one that fails loud.

    `kwargs` are passed through to the underlying function (e.g.
    period="2025-03", trailing_months=6, category="misc_expense").
    """
    if kpi_name not in KNOWN_KPIS:
        raise ValueError(f"Unknown KPI '{kpi_name}'. Known KPIs: {sorted(KNOWN_KPIS)}")

    fn = KNOWN_KPIS[kpi_name]

    if kpi_name in _CASH_KPIS or kpi_name in _ANOMALY_KPIS:
        txns = cash_kpis.load_transactions()
        result = fn(txns, business_id, **kwargs)
    elif kpi_name in _ACCRUAL_KPIS:
        fin = accrual_kpis.load_financials()
        result = fn(fin, business_id, **kwargs)
    else:  # reconciliation
        txns = cash_kpis.load_transactions()
        fin = accrual_kpis.load_financials()
        result = fn(txns, fin, business_id, **kwargs)

    result["_kpi_name"] = kpi_name
    result["_re_derived"] = True  # marker: this came from a live recompute, not a cache
    return result


def verify_citation(kpi_name: str, business_id: str, claimed_source_ids: list[str], **kwargs) -> dict:
    """Re-derives a KPI and checks a previously-claimed citation list
    against the freshly computed one. This is the actual anti-staleness
    check: call this on any citation before trusting it in a report."""
    fresh = cite(kpi_name, business_id, **kwargs)
    fresh_ids = set(fresh.get("source_transaction_ids", []))
    claimed = set(claimed_source_ids)
    return {
        "kpi_name": kpi_name,
        "business_id": business_id,
        "matches": fresh_ids == claimed,
        "missing_from_claim": sorted(fresh_ids - claimed),
        "extra_in_claim": sorted(claimed - fresh_ids),
    }


def _self_check() -> None:
    checks = []

    r1 = cite("runway_months", "biz_07")
    checks.append(("cite('runway_months', biz_07) returns citations and a re-derived marker",
                    bool(r1.get("source_transaction_ids")) and r1.get("_re_derived") is True, r1))

    r2 = cite("margins", "biz_03", period="2025-06")
    checks.append(("cite('margins', biz_03, period=2025-06) returns a scoped result",
                    r2.get("period") == "2025-06", r2))

    ok_check = verify_citation("runway_months", "biz_07", r1["source_transaction_ids"])
    checks.append(("verify_citation confirms a freshly-cited list matches itself",
                    ok_check["matches"], ok_check))

    unique_ids = sorted(set(r1["source_transaction_ids"]))
    tampered = [i for i in r1["source_transaction_ids"] if i != unique_ids[0]]  # drop ALL occurrences of one id
    bad_check = verify_citation("runway_months", "biz_07", tampered)
    checks.append(("verify_citation catches a deliberately incomplete claimed citation",
                    not bad_check["matches"] and unique_ids[0] in bad_check["missing_from_claim"], bad_check))

    try:
        cite("not_a_real_kpi", "biz_01")
        checks.append(("unknown KPI name raises ValueError (fails loud, not silent)", False, None))
    except ValueError:
        checks.append(("unknown KPI name raises ValueError (fails loud, not silent)", True, None))

    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"   detail: {detail}")


if __name__ == "__main__":
    _self_check()
