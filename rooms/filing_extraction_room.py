"""filing_extraction_room.py — Structured KPI extraction report (Daloopa lens).

Entry point that reads monthly_financials.csv, calls accrual KPI skills,
assembles facts, and produces a structured report with confidence flags for
a financial analyst reviewing extracted figures.

Audience: Daloopa persona (§1 in PERSONAS.md) — buy-side financial analyst
checking extracted figures from a company's filings before they go into a
model or memo. Every number must be traceable to its source, and anything
that doesn't reconcile must be flagged.

Usage:
    python3 rooms/filing_extraction_room.py [--business biz_07]

Output: A structured report with margins, bad-debt profile, D&A trends,
reconciliation, and confidence flags. Citations are front and center.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills"))

import accrual_kpis
import reconciliation
import cash_kpis
import narrate


def load_businesses(path: Path = ROOT / "data" / "businesses.csv") -> dict[str, dict]:
    """Load business names and metadata."""
    by_id = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            by_id[row["business_id"]] = row
    return by_id


def run_filing_extraction_room(business_id: str) -> dict:
    """Extract and cite accrual-basis KPIs for a given business.

    Returns:
        {
            "business_id": str,
            "business_name": str,
            "facts": list[dict],
            "narrative": str,
            "validated": bool,
            "issues": list[str],
            "confidence_flags": dict
        }
    """
    # Load data
    fin = accrual_kpis.load_financials()
    txns = cash_kpis.load_transactions()
    businesses = load_businesses()

    business = businesses.get(business_id, {})
    business_name = business.get("business_name", business_id)
    industry = business.get("industry", "Unknown")

    # Collect facts from accrual KPI skills
    facts = []
    confidence_flags = {}

    # 1. Net income verification (confidence check)
    integrity_check = accrual_kpis.verify_net_income_all(fin)
    confidence_flags["net_income_integrity"] = {
        "total_rows": integrity_check["total_rows"],
        "flagged_count": integrity_check["flagged_count"],
        "flag": "low_confidence" if integrity_check["flagged_count"] > 0 else "verified",
    }

    # 2. Margins (gross, operating, net)
    margins = accrual_kpis.margins(fin, business_id)
    if margins.get("net_margin") is not None:
        facts.append({
            "label": f"Net margin (TTM)",
            "value": margins.get("net_margin"),
            "source_transaction_ids": margins.get("source_transaction_ids", []),
        })
        facts.append({
            "label": f"Operating margin (TTM)",
            "value": margins.get("operating_margin"),
            "source_transaction_ids": margins.get("source_transaction_ids", []),
        })
        facts.append({
            "label": f"Gross margin (TTM)",
            "value": margins.get("gross_margin"),
            "source_transaction_ids": margins.get("source_transaction_ids", []),
        })

    # 3. Bad debt profile
    bd = accrual_kpis.bad_debt_profile(fin, business_id)
    if bd.get("effective_bad_debt_rate") is not None:
        facts.append({
            "label": f"Bad debt rate (effective)",
            "value": bd.get("effective_bad_debt_rate"),
            "flag": bd.get("flag"),
            "source_transaction_ids": bd.get("source_transaction_ids", []),
        })

    # 4. Allowance for doubtful accounts adequacy
    allowance = accrual_kpis.allowance_adequacy(fin, business_id)
    if allowance.get("value") is not None:
        facts.append({
            "label": f"Allowance / AR ratio (TTM)",
            "value": allowance.get("value"),
            "flag": allowance.get("flag"),
            "source_transaction_ids": allowance.get("source_transaction_ids", []),
        })

    # 5. D&A trend (show months with D&A, cite their sources)
    da = accrual_kpis.da_trend(fin, business_id)
    # Only include months with D&A > 0
    da_with_value = [m for m in da.get("months", []) if m["depreciation_amortization"] > 0]
    if da_with_value:
        total_da = sum(m["depreciation_amortization"] for m in da_with_value)
        # Combine all source IDs from D&A months
        all_da_ids = []
        for m in da_with_value:
            all_da_ids.extend(m.get("source_transaction_ids", []))
        facts.append({
            "label": f"D&A total (TTM, {len(da_with_value)} months)",
            "value": round(total_da, 2),
            "source_transaction_ids": all_da_ids,
        })

    # 6. Cash vs accrual reconciliation
    recon = reconciliation.reconciliation_summary(txns, fin, business_id)
    if recon.get("total_accrual_revenue") is not None:
        facts.append({
            "label": f"Total accrual revenue (12mo)",
            "value": recon.get("total_accrual_revenue"),
            "source_transaction_ids": recon.get("source_transaction_ids", []),
        })
        facts.append({
            "label": f"Total cash revenue collected (12mo)",
            "value": recon.get("total_cash_revenue"),
            "source_transaction_ids": recon.get("source_transaction_ids", []),
        })

    # Call narrate to produce the brief
    subject = f"Filing extraction report — {business_name} ({industry})"
    result = narrate.narrate(subject, facts, backend="mock")

    return {
        "business_id": business_id,
        "business_name": business_name,
        "industry": industry,
        "facts": facts,
        "narrative": result.get("narrative"),
        "validated": result.get("validated"),
        "issues": result.get("issues", []),
        "confidence_flags": confidence_flags,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate structured KPI extraction report for filing review.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 rooms/filing_extraction_room.py --business biz_07\n"
            "  python3 rooms/filing_extraction_room.py --business biz_03\n"
        ),
    )
    parser.add_argument(
        "--business",
        default="biz_07",
        help="Business ID to analyze (default: biz_07)",
    )
    args = parser.parse_args()

    # Run analysis
    result = run_filing_extraction_room(args.business)

    # Print report
    print("\n" + "=" * 70)
    print("FILING EXTRACTION REPORT")
    print("=" * 70)
    print(f"\nBusiness: {result['business_name']} ({result['industry']})")
    print(f"ID: {result['business_id']}")

    # Confidence flags
    print("\n" + "-" * 70)
    print("DATA INTEGRITY & CONFIDENCE")
    print("-" * 70)
    for check_name, check_result in result["confidence_flags"].items():
        flag = check_result.get("flag", "unknown")
        icon = "✓" if flag == "verified" else "⚠"
        details = ""
        if "flagged_count" in check_result:
            details = f" ({check_result['flagged_count']}/{check_result['total_rows']} rows flagged)"
        print(f"{icon} {check_name.replace('_', ' ').title()}: {flag}{details}")

    # Extracted KPIs
    print("\n" + "-" * 70)
    print("EXTRACTED KPIs (WITH SOURCES)")
    print("-" * 70)
    for i, fact in enumerate(result["facts"], 1):
        label = fact.get("label", "unknown")
        value = fact.get("value")
        ids = fact.get("source_transaction_ids", [])
        flag = fact.get("flag")

        # Format value
        if value is None:
            val_str = "(no value)"
        elif isinstance(value, float):
            if abs(value) < 1.0:
                val_str = f"{value:.4f}"
            else:
                val_str = f"{value:.2f}"
        else:
            val_str = str(value)

        print(f"\n{i}. {label}")
        print(f"   Value: {val_str}")
        if flag:
            print(f"   Flag: {flag}")
        if ids:
            # Show first few IDs and count
            shown_ids = ids[:5]
            id_str = ", ".join(shown_ids)
            if len(ids) > 5:
                id_str += f", ... (+{len(ids) - 5} more)"
            print(f"   Sources: {id_str}")

    # Narrative
    print("\n" + "-" * 70)
    print("ANALYST NARRATIVE")
    print("-" * 70)
    if result["narrative"]:
        print(f"\n{result['narrative']}\n")

    if not result["validated"]:
        print("\n[WARNING] Narrative validation issues:")
        for issue in result["issues"]:
            print(f"  - {issue}")

    print("\n" + "=" * 70 + "\n")

    return 0 if result["validated"] else 1


if __name__ == "__main__":
    sys.exit(main())
