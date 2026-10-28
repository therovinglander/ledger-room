"""pricing_room.py — relationship pricing / risk-tier recommendation.

Third room, added for the Zafin pivot: same facts-and-cite architecture as
cash_flow_room.py and filing_extraction_room.py, but the output is a
decision (a pricing tier + required terms) instead of a KPI readout. This is
deliberately the room that speaks "governed relationship pricing" — Zafin's
own language — rather than "extraction" or "underwriting."

skills/pricing_tier.py makes the tier decision in code (a simple, documented,
auditable threshold rule — zero model involvement in the decision itself).
This room only assembles the facts that drove that decision and the KPIs
behind them, then hands them to narrate.py for a cited explanation.

Usage:
    python3 rooms/pricing_room.py [--business biz_11]

Output: a tier recommendation, the risk flags that drove it, and (for
auditability) which of the underlying KPI facts cite which source rows —
plus a narrative paragraph, validated for zero uncited numbers.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills"))

import cash_kpis
import accrual_kpis
import anomaly_detection
import pricing_tier
import narrate


def load_businesses(path: Path = ROOT / "data" / "businesses.csv") -> dict[str, dict]:
    by_id = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            by_id[row["business_id"]] = row
    return by_id


def run_pricing_room(business_id: str) -> dict:
    """Compute a pricing-tier recommendation + cited explanation for one business.

    Returns:
        {
            "business_id", "business_name", "industry",
            "tier", "tier_name", "flag_count", "triggered_flags",
            "narrative", "validated", "issues"
        }
    """
    txns = cash_kpis.load_transactions()
    fin = accrual_kpis.load_financials()
    businesses = load_businesses()

    business = businesses.get(business_id, {})
    business_name = business.get("business_name", business_id)
    industry = business.get("industry", "Unknown")

    # 1. Compute the 5 underlying risk-flag KPIs (already self-check-passing
    #    in their own modules — this room does not recompute, just calls).
    runway = cash_kpis.runway_months(txns, business_id)
    overdraft = cash_kpis.overdraft_risk(txns, business_id)
    concentration = cash_kpis.customer_concentration(txns, business_id)
    bad_debt = accrual_kpis.bad_debt_profile(fin, business_id)
    round_cluster = anomaly_detection.round_number_cluster(txns, business_id)

    # 2. Deterministic tier decision (skills/pricing_tier.py) — no model
    #    involved in the decision, only in narrating it afterward.
    tier_result = pricing_tier.recommend_pricing_tier(
        business_id, runway, overdraft, concentration, bad_debt, round_cluster
    )

    # 3. Assemble facts: the underlying KPIs that drove the decision (only
    #    the ones with a value to cite), plus the tier decision itself.
    facts = []

    if runway.get("value") is not None:
        facts.append({
            "label": "Cash runway",
            "value": runway.get("value"),
            "flag": runway.get("flag"),
            "source_transaction_ids": runway.get("source_transaction_ids", []),
        })
    elif runway.get("flag") == "cash_negative":
        facts.append({
            "label": "Cash position",
            "value": runway.get("current_balance"),
            "flag": "cash_negative",
            "source_transaction_ids": runway.get("source_transaction_ids", []),
        })

    if overdraft.get("lowest_balance") is not None:
        facts.append({
            "label": "Lowest cash balance",
            "value": overdraft.get("lowest_balance"),
            "flag": overdraft.get("flag"),
            "source_transaction_ids": overdraft.get("source_transaction_ids", []),
        })

    if concentration.get("value") is not None:
        facts.append({
            "label": "Top customer concentration",
            "value": concentration.get("value"),
            "flag": concentration.get("flag"),
            "top_customer": concentration.get("top_customer"),
            "source_transaction_ids": concentration.get("source_transaction_ids", []),
        })

    if bad_debt.get("effective_bad_debt_rate") is not None:
        facts.append({
            "label": "Bad debt rate (effective)",
            "value": bad_debt.get("effective_bad_debt_rate"),
            "flag": bad_debt.get("flag"),
            "source_transaction_ids": bad_debt.get("source_transaction_ids", []),
        })

    if round_cluster.get("round_number_count", 0) > 0:
        facts.append({
            "label": "Suspicious round-number expenses",
            "value": round_cluster.get("round_number_count"),
            "flag": round_cluster.get("flag"),
            "source_transaction_ids": round_cluster.get("source_transaction_ids", []),
        })

    # The tier fact itself is only added when there's something to cite
    # (flag_count > 0) -- a Tier A / zero-flag business has nothing to
    # point at, and the fact contract (validate_facts) requires any fact
    # with a non-null value to carry a non-empty citation list. Tier A is
    # still reported directly below, just not narrated with a citation.
    if tier_result["flag_count"] > 0:
        facts.append({
            "label": "Recommended pricing tier",
            "value": tier_result["flag_count"],
            "tier_name": tier_result["tier_name"],
            "source_transaction_ids": tier_result["source_transaction_ids"],
        })

    subject = f"Relationship pricing recommendation — {business_name} ({industry})"
    result = narrate.narrate(subject, facts, backend="mock")

    return {
        "business_id": business_id,
        "business_name": business_name,
        "industry": industry,
        "tier": tier_result["tier"],
        "tier_name": tier_result["tier_name"],
        "flag_count": tier_result["flag_count"],
        "triggered_flags": tier_result["triggered_flags"],
        "cited_ids": tier_result["source_transaction_ids"],
        "narrative": result.get("narrative"),
        "validated": result.get("validated"),
        "issues": result.get("issues", []),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate a relationship pricing tier recommendation for an SMB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 rooms/pricing_room.py --business biz_11\n"
            "  python3 rooms/pricing_room.py --business biz_22\n"
        ),
    )
    parser.add_argument(
        "--business",
        default="biz_11",
        help="Business ID to analyze (default: biz_11, overdraft + cash-negative -> Tier C)",
    )
    args = parser.parse_args()

    result = run_pricing_room(args.business)

    print("\n" + "=" * 70)
    print("RELATIONSHIP PRICING RECOMMENDATION")
    print("=" * 70)
    print(f"\nBusiness: {result['business_name']} ({result['industry']})")
    print(f"ID: {result['business_id']}")
    print("\n" + "-" * 70)
    print("DECISION")
    print("-" * 70)
    print(f"\n{result['tier_name']}")
    print(f"Risk flags triggered ({result['flag_count']}): {', '.join(result['triggered_flags']) or 'none'}")
    if result["cited_ids"]:
        id_str = ", ".join(result["cited_ids"][:8])
        if len(result["cited_ids"]) > 8:
            id_str += f", ... (+{len(result['cited_ids']) - 8} more)"
        print(f"Cited sources: {id_str}")
    else:
        print("Cited sources: none (zero risk flags triggered -- nothing to cite)")

    print("\n" + "-" * 70)
    print("EXPLANATION")
    print("-" * 70)
    if result["narrative"]:
        print(f"\n{result['narrative']}\n")
    else:
        print("\n(No narration generated -- no cited facts to narrate.)\n")

    if not result["validated"]:
        print("\n[WARNING] Narrative contains uncited numbers or invalid citations:")
        for issue in result["issues"]:
            print(f"  - {issue}")

    print("\n" + "=" * 70 + "\n")

    return 0 if result["validated"] else 1


if __name__ == "__main__":
    sys.exit(main())
