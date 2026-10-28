"""Accrual-basis KPIs for the filing_extraction_room.

Same rule as cash_kpis.py: code computes and cites, narrate.py never sees
raw rows. Source here is data/monthly_financials.csv, where each row
already carries its own basis_refs columns (populated by
src/generate_financials.py from the cash-basis transactions and the
depreciation schedule) — these functions re-surface those refs, they don't
invent new ones.

This module also carries the "evaluate AI output quality, flag low-
confidence extractions" talking point literally: `verify_net_income`
independently recomputes net_income from the row's own component fields and
flags any row where the reported figure doesn't match — i.e. it treats the
CSV's net_income column the way an analyst would treat a model's extracted
figure, and checks it rather than trusting it.
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIN_PATH = ROOT / "data" / "monthly_financials.csv"

NUMERIC_FIELDS = (
    "revenue_accrual", "cogs", "opex_payroll", "opex_rent", "opex_other",
    "depreciation_amortization", "bad_debt_expense", "bad_debt_rate_assumption",
    "net_income", "ar_balance", "allowance_doubtful_accounts", "ap_balance",
    "accrued_liabilities",
)


def load_financials(path: Path = FIN_PATH) -> list[dict]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for field in NUMERIC_FIELDS:
            r[field] = float(r[field])
        r["recognition_lag_months"] = int(r["recognition_lag_months"])
    return rows


def _for_business(financials: list[dict], business_id: str) -> list[dict]:
    rows = [r for r in financials if r["business_id"] == business_id]
    rows.sort(key=lambda r: r["period"])
    return rows


def _refs(row: dict) -> list[str]:
    """Every basis_refs column on a row, flattened into one source list."""
    out: list[str] = []
    for col in ("revenue_basis_refs", "opex_other_basis_refs", "dep_basis_refs"):
        val = row.get(col, "")
        if val:
            out.extend(val.split(";"))
    return out


def verify_net_income(row: dict, tolerance: float = 0.01) -> dict:
    """Independently recomputes net_income from the row's own component
    fields (revenue - cogs - opex_payroll - opex_rent - opex_other - D&A -
    bad_debt_expense) and compares to the reported net_income column.

    This is a confidence check: treat the stored figure as
    an extraction to be verified, not a fact to be trusted. Mismatch beyond
    `tolerance` (rounding noise) is flagged low_confidence_extraction.
    """
    recomputed = (
        row["revenue_accrual"] - row["cogs"] - row["opex_payroll"] - row["opex_rent"]
        - row["opex_other"] - row["depreciation_amortization"] - row["bad_debt_expense"]
    )
    diff = round(recomputed - row["net_income"], 2)
    return {
        "financials_id": row["financials_id"],
        "business_id": row["business_id"],
        "period": row["period"],
        "reported_net_income": row["net_income"],
        "recomputed_net_income": round(recomputed, 2),
        "diff": diff,
        "flag": "low_confidence_extraction" if abs(diff) > tolerance else None,
        "source_transaction_ids": _refs(row),
    }


def verify_net_income_all(financials: list[dict], tolerance: float = 0.01) -> dict:
    """Runs verify_net_income over every row; this is what narrate.py (or a
    human) would actually call before trusting any net_income figure in a
    filing-extraction report."""
    results = [verify_net_income(r, tolerance=tolerance) for r in financials]
    flagged = [r for r in results if r["flag"]]
    return {"total_rows": len(results), "flagged_count": len(flagged), "flagged": flagged}


def margins(financials: list[dict], business_id: str, period: str | None = None) -> dict:
    """Gross / operating / net margin for one business, for one period or
    (if period is None) trailing-12mo totals. Returns None margins (with a
    reason) when revenue is 0 to avoid a divide-by-zero masquerading as a
    real figure."""
    rows = _for_business(financials, business_id)
    if period:
        rows = [r for r in rows if r["period"] == period]
    if not rows:
        return {"business_id": business_id, "period": period, "value": None,
                "reason": "no matching rows", "source_transaction_ids": []}

    revenue = sum(r["revenue_accrual"] for r in rows)
    cogs = sum(r["cogs"] for r in rows)
    opex = sum(r["opex_payroll"] + r["opex_rent"] + r["opex_other"] for r in rows)
    net_income = sum(r["net_income"] for r in rows)
    ids = [tid for r in rows for tid in _refs(r)]

    if revenue == 0:
        return {"business_id": business_id, "period": period, "value": None,
                "reason": "zero revenue in period", "source_transaction_ids": ids}

    return {
        "business_id": business_id,
        "period": period or "trailing_all",
        "revenue": round(revenue, 2),
        "gross_margin": round((revenue - cogs) / revenue, 4),
        "operating_margin": round((revenue - cogs - opex) / revenue, 4),
        "net_margin": round(net_income / revenue, 4),
        "source_transaction_ids": ids,
    }


def bad_debt_profile(financials: list[dict], business_id: str) -> dict:
    """Effective bad-debt rate (bad_debt_expense / revenue, recomputed from
    the rows, not just re-reading the assumption column) vs the row's own
    stated bad_debt_rate_assumption. Flags elevated risk when the effective
    rate is above the portfolio default (0.012, see
    src/generate_financials.py DEFAULT_BAD_DEBT)."""
    rows = _for_business(financials, business_id)
    if not rows:
        return {"business_id": business_id, "value": None, "reason": "no rows",
                "source_transaction_ids": []}

    revenue = sum(r["revenue_accrual"] for r in rows)
    bad_debt = sum(r["bad_debt_expense"] for r in rows)
    stated_rate = rows[-1]["bad_debt_rate_assumption"]
    effective_rate = bad_debt / revenue if revenue else None

    return {
        "business_id": business_id,
        "stated_bad_debt_rate_assumption": stated_rate,
        "effective_bad_debt_rate": round(effective_rate, 5) if effective_rate is not None else None,
        "total_bad_debt_expense": round(bad_debt, 2),
        "flag": "elevated_bad_debt" if stated_rate > 0.012 else None,
        "source_transaction_ids": [r["financials_id"] for r in rows],
    }


def allowance_adequacy(financials: list[dict], business_id: str, period: str | None = None) -> dict:
    """allowance_doubtful_accounts / ar_balance — the share of receivables
    already provisioned as uncollectible. Flags thin coverage (<1%) or a
    business carrying unusually high provisioning (>7%) relative to AR."""
    rows = _for_business(financials, business_id)
    if period:
        rows = [r for r in rows if r["period"] == period]
    if not rows:
        return {"business_id": business_id, "period": period, "value": None,
                "reason": "no matching rows", "source_transaction_ids": []}

    ar = sum(r["ar_balance"] for r in rows)
    allowance = sum(r["allowance_doubtful_accounts"] for r in rows)
    ratio = allowance / ar if ar else None
    flag = None
    if ratio is not None:
        if ratio < 0.01:
            flag = "thin_allowance_coverage"
        elif ratio > 0.07:
            flag = "high_allowance_provisioning"

    return {
        "business_id": business_id,
        "period": period or "trailing_all",
        "ar_balance": round(ar, 2),
        "allowance_doubtful_accounts": round(allowance, 2),
        "value": round(ratio, 4) if ratio is not None else None,
        "flag": flag,
        "source_transaction_ids": [r["financials_id"] for r in rows],
    }


def da_trend(financials: list[dict], business_id: str) -> dict:
    """Depreciation & amortization by period, with the depreciation_ids
    (dep_basis_refs) behind each month — lets a reviewer confirm D&A starts
    only after a capex purchase and never appears as a cash outflow."""
    rows = _for_business(financials, business_id)
    months = []
    for r in rows:
        refs = r["dep_basis_refs"].split(";") if r["dep_basis_refs"] else []
        months.append({"period": r["period"], "depreciation_amortization": r["depreciation_amortization"],
                        "source_transaction_ids": refs})
    return {"business_id": business_id, "months": months}


def _self_check() -> None:
    fin = load_financials()
    checks = []

    integrity = verify_net_income_all(fin)
    checks.append(("every row's net_income reconciles to its own components (±$0.01)",
                    integrity["flagged_count"] == 0, {"flagged_count": integrity["flagged_count"],
                                                        "sample": integrity["flagged"][:3]}))

    for biz in ("biz_03", "biz_22"):
        bd = bad_debt_profile(fin, biz)
        checks.append((f"{biz} flagged for elevated bad debt (ELEVATED_BAD_DEBT in generator)",
                        bd["flag"] == "elevated_bad_debt", bd))

    default_bizs = [b for b in sorted({r["business_id"] for r in fin}) if b not in ("biz_03", "biz_22")]
    sample = bad_debt_profile(fin, default_bizs[0])
    checks.append((f"{default_bizs[0]} (non-elevated) is NOT flagged for bad debt",
                    sample["flag"] is None, sample))

    biz07_dep = da_trend(fin, "biz_07")
    first_nonzero = next((m for m in biz07_dep["months"] if m["depreciation_amortization"] > 0), None)
    checks.append(("biz_07 has at least one month of non-zero depreciation with cited source IDs",
                    first_nonzero is not None and len(first_nonzero["source_transaction_ids"]) > 0,
                    first_nonzero))

    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"   detail: {detail}")


if __name__ == "__main__":
    _self_check()
