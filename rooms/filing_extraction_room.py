"""filing_extraction_room.py — Daloopa lens: structured, cited KPI
extraction report with confidence flags.

Reads monthly_financials.csv (+ businesses.csv), calls accrual_kpis +
reconciliation, assembles a fact list, and calls skills/narrate.py.
Audience is technical/financial (docs/PERSONAS.md §1) — depth and
citations matter more than plain English here.

Real-data note (PROJECT.md §5, handoff item 4): this room is the ONLY one
EDGAR data is wired into. See skills/edgar.py — if the sandbox's network
allowlist does not include data.sec.gov, that pull is a documented stub
and this room continues to run end-to-end on the synthetic
monthly_financials.csv (cash_flow_room's data is left untouched either way).

Usage:
    python3 rooms/filing_extraction_room.py                # all 25 businesses
    python3 rooms/filing_extraction_room.py biz_03          # one business
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills"))

import accrual_kpis  # noqa: E402
import reconciliation  # noqa: E402
from narrate import Fact, narrate  # noqa: E402

BUSINESSES_PATH = ROOT / "data" / "businesses.csv"
OUT_DIR = ROOT / "out"


def load_business_names() -> dict[str, str]:
    with BUSINESSES_PATH.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return {r["business_id"]: r["business_name"] for r in rows}


def build_facts(transactions: list[dict], financials: list[dict], business_id: str) -> list[Fact]:
    """Calls accrual_kpis + reconciliation and normalizes their outputs
    into Fact objects. One place where this room's "code computes and
    cites" contract is enforced."""
    m = accrual_kpis.margins(financials, business_id)
    bd_profile = accrual_kpis.bad_debt_profile(financials, business_id)
    allowance = accrual_kpis.allowance_adequacy(financials, business_id)
    recon = reconciliation.reconciliation_summary(transactions, financials, business_id)

    rows = accrual_kpis._for_business(financials, business_id)
    integrity_flags = [
        accrual_kpis.verify_net_income(r) for r in rows
    ]
    flagged_periods = [f["period"] for f in integrity_flags if f["flag"]]
    # Cite every period's financials_id regardless of count, not just the
    # flagged ones — the fact is "N mismatches found when checking all of
    # these periods," so the full set checked is the citation, even when
    # the count is 0 (a 0 with no citation would itself violate the
    # "non-null value needs a citation" rule narrate.py enforces).
    integrity_ids = [r["financials_id"] for r in rows]

    facts = [
        Fact(label="Gross margin (%)", value=round(m["gross_margin"] * 100, 1) if m.get("gross_margin") is not None else None,
             source_transaction_ids=m.get("source_transaction_ids", []), note=m.get("reason")),
        Fact(label="Operating margin (%)", value=round(m["operating_margin"] * 100, 1) if m.get("operating_margin") is not None else None,
             source_transaction_ids=m.get("source_transaction_ids", []), note=m.get("reason")),
        Fact(label="Net margin (%)", value=round(m["net_margin"] * 100, 1) if m.get("net_margin") is not None else None,
             source_transaction_ids=m.get("source_transaction_ids", []), note=m.get("reason")),
        Fact(label="Effective bad debt rate (%)",
             value=round(bd_profile["effective_bad_debt_rate"] * 100, 2) if bd_profile.get("effective_bad_debt_rate") is not None else None,
             source_transaction_ids=bd_profile.get("source_transaction_ids", []), note=bd_profile.get("flag")),
        Fact(label="Allowance coverage of AR (%)",
             value=round(allowance["value"] * 100, 2) if allowance.get("value") is not None else None,
             source_transaction_ids=allowance.get("source_transaction_ids", []), note=allowance.get("flag")),
        Fact(label="Cash-vs-accrual revenue gap (full window)", value=recon.get("gap"),
             source_transaction_ids=recon.get("source_transaction_ids", []), note=recon.get("explanation")),
        Fact(label="Periods with a net-income integrity mismatch", value=len(flagged_periods),
             source_transaction_ids=integrity_ids if integrity_ids else [],
             note=("low_confidence_extraction: " + ", ".join(flagged_periods)) if flagged_periods else None),
    ]
    return facts


def run_for_business(transactions: list[dict], financials: list[dict], business_id: str,
                      display_name: str, backend: str = "mock") -> dict:
    facts = build_facts(transactions, financials, business_id)
    result = narrate(facts, audience="buy-side financial analyst",
                      room_label=f"Filing Extraction Room — {display_name} ({business_id})",
                      backend=backend)
    return {
        "business_id": business_id,
        "business_name": display_name,
        "facts": [vars(f) for f in facts],
        **result,
    }


def build_edgar_facts(cik: str, company_name: str) -> list[Fact] | None:
    """Builds a Fact list for one real public company from the EDGAR cache
    written by skills/edgar.py (data/edgar_cache/<CIK>.json). Returns None
    if that company hasn't been pulled/cached yet.

    Scope note: EDGAR's annual GAAP facts (revenue, net income, D&A, AR)
    don't carry the full breakdown accrual_kpis.py's synthetic rows do
    (no COGS/opex split, no bad-debt rate), so this is an honest, narrower
    extraction report — latest-fiscal-year figures cited to their own SEC
    accession number — not a forced remap onto every synthetic KPI."""
    cache_path = ROOT / "data" / "edgar_cache" / f"{cik}.json"
    if not cache_path.exists():
        return None
    cached = json.loads(cache_path.read_text())

    facts = []
    labels = {
        "revenue_accrual": "Revenue (latest 10-K, USD)",
        "net_income": "Net income (latest 10-K, USD)",
        "depreciation_amortization": "D&A (latest 10-K, USD)",
        "ar_balance": "Accounts receivable, net (latest 10-K, USD)",
    }
    for field_name, label in labels.items():
        rows = cached.get(field_name, [])
        if not rows:
            facts.append(Fact(label=label, value=None, note="not reported / not pulled"))
            continue
        latest = max(rows, key=lambda r: (r.get("fiscal_year") or 0))
        facts.append(Fact(
            label=label,
            value=latest["value"],
            source_transaction_ids=latest["source_transaction_ids"],
            note=f"FY{latest['fiscal_year']} {latest['form']} filing, accession {latest['accession_number']}",
        ))
    return facts


def run_edgar_companies(backend: str = "mock") -> list[dict]:
    """Runs the EDGAR-sourced report for every company already cached by
    skills/edgar.py. Caller is responsible for running `edgar.pull_sample()`
    first (main() below does this when --edgar is passed)."""
    import edgar  # local import: optional network dependency, only needed here

    reports = []
    for cik, company_name in edgar.SAMPLE_COMPANIES.items():
        facts = build_edgar_facts(cik, company_name)
        if facts is None:
            reports.append({"cik": cik, "company_name": company_name, "status": "not_cached"})
            continue
        result = narrate(facts, audience="buy-side financial analyst",
                          room_label=f"Filing Extraction Room (EDGAR) — {company_name} ({cik})",
                          backend=backend)
        reports.append({"cik": cik, "company_name": company_name, "status": "ok",
                         "facts": [vars(f) for f in facts], **result})
    return reports


def main(business_ids: list[str] | None = None, backend: str = "mock", use_edgar: bool = False) -> None:
    from cash_kpis import load_transactions  # noqa: E402 (local import, avoids a hard dependency for pure-accrual use)

    OUT_DIR.mkdir(exist_ok=True)

    if use_edgar:
        import edgar  # local import: only needed for --edgar
        pull_status = edgar.pull_sample()
        (OUT_DIR / "edgar_pull_status.json").write_text(json.dumps(pull_status, indent=2))
        edgar_reports = run_edgar_companies(backend=backend)
        out_path = OUT_DIR / "filing_extraction_room_edgar_report.json"
        out_path.write_text(json.dumps(edgar_reports, indent=2))
        txt_lines = []
        for r in edgar_reports:
            txt_lines.append(f"=== {r['company_name']} ({r['cik']}) ===")
            if r["status"] == "ok":
                txt_lines.append(r["narrative"])
                txt_lines.append(f"[validation ok: {r['ok']}]")
            else:
                txt_lines.append(f"[{r['status']}]")
            txt_lines.append("")
        txt_path = OUT_DIR / "filing_extraction_room_edgar_report.txt"
        txt_path.write_text("\n".join(txt_lines))
        ok_count = sum(1 for r in edgar_reports if r["status"] == "ok")
        print(f"EDGAR: wrote {ok_count}/{len(edgar_reports)} real-company report(s) to {out_path} and {txt_path}")

    transactions = load_transactions()
    financials = accrual_kpis.load_financials()
    names = load_business_names()
    if business_ids is None:
        business_ids = sorted(names)

    reports = []
    for biz_id in business_ids:
        display_name = names.get(biz_id, biz_id)
        reports.append(run_for_business(transactions, financials, biz_id, display_name, backend=backend))

    out_path = OUT_DIR / "filing_extraction_room_report.json"
    out_path.write_text(json.dumps(reports, indent=2))

    txt_lines = []
    for r in reports:
        txt_lines.append(f"=== {r['business_name']} ({r['business_id']}) ===")
        txt_lines.append(r["narrative"])
        txt_lines.append(f"[validation ok: {r['ok']}]")
        txt_lines.append("")
    txt_path = OUT_DIR / "filing_extraction_room_report.txt"
    txt_path.write_text("\n".join(txt_lines))

    all_ok = all(r["ok"] for r in reports)
    print(f"Wrote {len(reports)} report(s) to {out_path} and {txt_path}")
    print(f"All reports passed citation validation: {all_ok}")
    if not all_ok:
        failing = [r["business_id"] for r in reports if not r["ok"]]
        print(f"FAILING: {failing}")


if __name__ == "__main__":
    args = sys.argv[1:]
    edgar_flag = "--edgar" in args
    args = [a for a in args if a != "--edgar"]
    ids = args if args else None
    main(ids, use_edgar=edgar_flag)
