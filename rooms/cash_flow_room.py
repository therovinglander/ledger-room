"""cash_flow_room.py — Relay lens: SMB cash-runway/risk brief.

Reads transactions.csv (+ businesses.csv for display names), calls
cash_kpis + anomaly_detection, assembles a fact list, and calls
skills/narrate.py. Audience is non-technical (docs/PERSONAS.md §2) — plain
tone, 3-4 KPIs that matter for underwriting, each citeable.

Usage:
    python3 rooms/cash_flow_room.py                 # all 25 businesses
    python3 rooms/cash_flow_room.py biz_07           # one business
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills"))

import anomaly_detection  # noqa: E402
import cash_kpis  # noqa: E402
from narrate import Fact, narrate  # noqa: E402

BUSINESSES_PATH = ROOT / "data" / "businesses.csv"
OUT_DIR = ROOT / "out"


def load_business_names() -> dict[str, str]:
    with BUSINESSES_PATH.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return {r["business_id"]: r["business_name"] for r in rows}


def _pct_fact(label: str, result: dict, note: str | None = None) -> Fact:
    value = result.get("value")
    return Fact(label=label, value=round(value * 100, 1) if value is not None else None,
                source_transaction_ids=result.get("source_transaction_ids", []),
                note=note or result.get("flag") or result.get("reason"))


def build_facts(transactions: list[dict], business_id: str) -> list[Fact]:
    """Calls cash_kpis + anomaly_detection and normalizes their outputs into
    the Fact shape narrate.py expects. This function is the one place the
    "code computes and cites" contract gets enforced for this room."""
    runway = cash_kpis.runway_months(transactions, business_id)
    burn = cash_kpis.burn_rate(transactions, business_id)
    conc = cash_kpis.customer_concentration(transactions, business_id)
    od = cash_kpis.overdraft_risk(transactions, business_id)
    rnc = anomaly_detection.round_number_cluster(transactions, business_id)
    bd = anomaly_detection.benford_deviation(transactions, business_id, category="misc_expense")

    facts = [
        Fact(label="Months of cash runway", value=runway.get("value"),
             source_transaction_ids=runway.get("source_transaction_ids", []),
             note=runway.get("flag") or runway.get("reason")),
        Fact(label="Trailing 3-month average monthly cash flow", value=burn.get("value"),
             source_transaction_ids=burn.get("source_transaction_ids", []),
             note=burn.get("reason")),
        _pct_fact("Revenue share from top customer", conc),
        Fact(label="Lowest cumulative cash balance", value=od.get("lowest_balance"),
             source_transaction_ids=od.get("source_transaction_ids", []),
             note=od.get("flag")),
        Fact(label="Round-number outflow transaction count", value=rnc.get("round_number_count"),
             # When the count is 0 there are no matching rows to cite, but
             # the fact still needs a citation per narrate.py's "non-null
             # value needs a source" rule — cite the full outflow scope that
             # was checked instead of the (empty) match set.
             source_transaction_ids=rnc.get("source_transaction_ids") or
             [r["transaction_id"] for r in cash_kpis._for_business(transactions, business_id) if r["amount"] < 0],
             note=rnc.get("flag")),
        Fact(label="Benford deviation score (misc. expenses)", value=bd.get("value"),
             source_transaction_ids=bd.get("source_transaction_ids", []),
             note=bd.get("reason")),
    ]
    return facts


def run_for_business(transactions: list[dict], business_id: str, display_name: str,
                      backend: str = "mock") -> dict:
    facts = build_facts(transactions, business_id)
    result = narrate(facts, audience="SMB credit/risk underwriter",
                      room_label=f"Cash Flow Room — {display_name} ({business_id})",
                      backend=backend)
    return {
        "business_id": business_id,
        "business_name": display_name,
        "facts": [vars(f) for f in facts],
        **result,
    }


def main(business_ids: list[str] | None = None, backend: str = "mock") -> None:
    transactions = cash_kpis.load_transactions()
    names = load_business_names()
    if business_ids is None:
        business_ids = sorted(names)

    OUT_DIR.mkdir(exist_ok=True)
    reports = []
    for biz_id in business_ids:
        display_name = names.get(biz_id, biz_id)
        reports.append(run_for_business(transactions, biz_id, display_name, backend=backend))

    out_path = OUT_DIR / "cash_flow_room_report.json"
    out_path.write_text(json.dumps(reports, indent=2))

    txt_lines = []
    for r in reports:
        txt_lines.append(f"=== {r['business_name']} ({r['business_id']}) ===")
        txt_lines.append(r["narrative"])
        txt_lines.append(f"[validation ok: {r['ok']}]")
        txt_lines.append("")
    txt_path = OUT_DIR / "cash_flow_room_report.txt"
    txt_path.write_text("\n".join(txt_lines))

    all_ok = all(r["ok"] for r in reports)
    print(f"Wrote {len(reports)} brief(s) to {out_path} and {txt_path}")
    print(f"All reports passed citation validation: {all_ok}")
    if not all_ok:
        failing = [r["business_id"] for r in reports if not r["ok"]]
        print(f"FAILING: {failing}")


if __name__ == "__main__":
    args = sys.argv[1:]
    ids = args if args else None
    main(ids)
