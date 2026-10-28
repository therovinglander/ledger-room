"""cash_flow_room.py — SMB cash-runway/risk brief for Relay lens.

Entry point that reads transactions.csv, calls cash KPI skills, assembles
facts, and produces a plain-English brief for a non-technical SMB
underwriter or risk analyst.

Audience: Relay persona (§2 in PERSONAS.md) — credit/risk underwriter
checking if an SMB is about to run out of cash, has concentrated revenue,
or shows anomalous transaction patterns.

Usage:
    python3 rooms/cash_flow_room.py [--business biz_07]

Output: A short plain-English brief with KPIs and their source IDs, zero
uncited numbers.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills"))

import cash_kpis
import anomaly_detection
import narrate


def load_businesses(path: Path = ROOT / "data" / "businesses.csv") -> dict[str, dict]:
    """Load business names and metadata."""
    by_id = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            by_id[row["business_id"]] = row
    return by_id


def run_cash_flow_room(business_id: str) -> dict:
    """Analyze cash flow & risk KPIs for a given business.

    Returns:
        {
            "business_id": str,
            "business_name": str,
            "narrative": str,
            "validated": bool,
            "issues": list[str]
        }
    """
    # Load data
    txns = cash_kpis.load_transactions()
    businesses = load_businesses()

    business = businesses.get(business_id, {})
    business_name = business.get("business_name", business_id)
    industry = business.get("industry", "Unknown")

    # Collect facts from cash KPI skills
    facts = []

    # 1. Runway months (primary risk signal)
    runtime = cash_kpis.runway_months(txns, business_id)
    if runtime.get("value") is not None:
        label = f"Cash runway ({runtime.get('as_of_period', 'latest')})"
        facts.append({
            "label": label,
            "value": runtime.get("value"),
            "flag": runtime.get("flag"),
            "source_transaction_ids": runtime.get("source_transaction_ids", []),
        })
    elif runtime.get("flag") == "cash_negative":
        facts.append({
            "label": "Cash position",
            "value": runtime.get("current_balance"),
            "flag": "cash_negative",
            "source_transaction_ids": runtime.get("source_transaction_ids", []),
        })
    # Note: if value is None and flag is not cash_negative, the business has
    # infinite runway (cash-flow positive). We'll include burn rate instead.

    # 2. Burn rate (context for runway)
    burn = cash_kpis.burn_rate(txns, business_id)
    if burn.get("value") is not None:
        facts.append({
            "label": f"Trailing 3-month burn rate",
            "value": burn.get("value"),
            "source_transaction_ids": burn.get("source_transaction_ids", []),
        })

    # 3. Customer concentration (revenue risk)
    conc = cash_kpis.customer_concentration(txns, business_id)
    if conc.get("value") is not None:
        facts.append({
            "label": f"Top customer share of revenue",
            "value": conc.get("value"),
            "flag": conc.get("flag"),
            "top_customer": conc.get("top_customer"),
            "source_transaction_ids": conc.get("source_transaction_ids", []),
        })

    # 4. Overdraft risk (liquidity stress)
    overdraft = cash_kpis.overdraft_risk(txns, business_id)
    if overdraft.get("lowest_balance") is not None:
        facts.append({
            "label": f"Lowest cash balance ({overdraft.get('lowest_balance_period', 'N/A')})",
            "value": overdraft.get("lowest_balance"),
            "flag": overdraft.get("flag"),
            "source_transaction_ids": overdraft.get("source_transaction_ids", []),
        })

    # 5. Round-number anomalies (fraud/risk signal)
    round_num = anomaly_detection.round_number_cluster(txns, business_id)
    if round_num.get("round_number_count", 0) > 0:
        facts.append({
            "label": "Suspicious round-number ($500 multiples) expenses",
            "value": round_num.get("round_number_count"),
            "flag": round_num.get("flag"),
            "source_transaction_ids": round_num.get("source_transaction_ids", []),
        })

    # 6. Benford anomaly (secondary, low-confidence anomaly signal)
    benford = anomaly_detection.benford_deviation(txns, business_id)
    if benford.get("value") is not None and benford.get("value") > 0.3:
        facts.append({
            "label": "Benford leading-digit anomaly score",
            "value": benford.get("value"),
            "source_transaction_ids": benford.get("source_transaction_ids", []),
        })

    # Call narrate to produce the brief
    subject = f"Cash flow risk assessment — {business_name} ({industry})"
    result = narrate.narrate(subject, facts, backend="mock")

    return {
        "business_id": business_id,
        "business_name": business_name,
        "industry": industry,
        "narrative": result.get("narrative"),
        "validated": result.get("validated"),
        "issues": result.get("issues", []),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate cash-flow risk brief for an SMB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 rooms/cash_flow_room.py --business biz_07\n"
            "  python3 rooms/cash_flow_room.py --business biz_11\n"
        ),
    )
    parser.add_argument(
        "--business",
        default="biz_07",
        help="Business ID to analyze (default: biz_07, an interesting declining-revenue case)",
    )
    args = parser.parse_args()

    # Run analysis
    result = run_cash_flow_room(args.business)

    # Print report
    print("\n" + "=" * 70)
    print("CASH FLOW RISK BRIEF")
    print("=" * 70)
    print(f"\nBusiness: {result['business_name']} ({result['industry']})")
    print(f"ID: {result['business_id']}")
    print("\n" + "-" * 70)
    print("ASSESSMENT")
    print("-" * 70)
    print(f"\n{result['narrative']}\n")

    if not result["validated"]:
        print("\n[WARNING] Narrative contains uncited numbers or invalid citations:")
        for issue in result["issues"]:
            print(f"  - {issue}")

    print("\n" + "=" * 70 + "\n")

    return 0 if result["validated"] else 1


if __name__ == "__main__":
    sys.exit(main())
