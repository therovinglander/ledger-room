"""Generate a business metadata roster: business_id -> name/industry/notes.

Purely cosmetic/labeling layer — does not touch any financial figure. Exists
so the PRD, personas, and narrated reports can refer to "Prairie Fox Design
Studio (biz_07)" instead of a bare id, which is what a real analyst tool
would show. Deterministic (seeded) so re-running regenerates identically.

Output: data/businesses.csv
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

random.seed(99)

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "businesses.csv"
N_BUSINESSES = 25

INDUSTRIES = [
    "Professional Services", "Retail", "Food & Beverage", "Design Studio",
    "Construction Trades", "Marketing Agency", "Logistics", "Specialty Clinic",
    "Software Services", "Landscaping", "Auto Repair", "Fitness Studio",
    "Legal Services", "Accounting Practice", "Event Planning", "Consulting",
]

NAME_PREFIXES = [
    "Prairie Fox", "Northgate", "Blue Harbor", "Summit", "Ironwood", "Cedar Grove",
    "Lakeside", "Redstone", "Westfield", "Brightside", "Hollow Creek", "Granite Peak",
    "Maple & Co.", "Harborview", "Pine Ridge", "Silverline", "Copper Kettle", "Stonebridge",
    "Fieldstone", "Riverbend", "Oakmont", "Elm Street", "Highmark", "Bayview", "Trailhead",
]

RISK_NOTES = {
    "biz_07": "Injected pattern: declining revenue trend (runway/burn-rate test case).",
    "biz_18": "Injected pattern: declining revenue trend (runway/burn-rate test case).",
    "biz_03": "Injected pattern: single-customer concentration (~62% selection weight).",
    "biz_22": "Injected pattern: round-number expense cluster from an unverified payee (anomaly test case).",
    "biz_11": "Injected pattern: thin cash buffer timed around payroll dates (overdraft/liquidity test case).",
}


def main() -> None:
    rows = []
    for i in range(1, N_BUSINESSES + 1):
        biz_id = f"biz_{i:02d}"
        rows.append({
            "business_id": biz_id,
            "business_name": f"{NAME_PREFIXES[i - 1]} {random.choice(['LLC', 'Inc.', 'Co.', 'Group', 'Partners'])}",
            "industry": random.choice(INDUSTRIES),
            "risk_pattern_note": RISK_NOTES.get(biz_id, ""),
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["business_id", "business_name", "industry", "risk_pattern_note"])
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} business roster rows -> {OUT_PATH}")


if __name__ == "__main__":
    main()
