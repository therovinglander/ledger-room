"""edgar.py — real-data pull for filing_extraction_room ONLY (PROJECT.md §5).

Pulls SEC EDGAR XBRL "company facts" for a small set of real small/mid-cap
public companies and maps the relevant GAAP concepts onto the field names
`accrual_kpis.py` already expects, so `filing_extraction_room` can run
against real filings instead of (or alongside) the synthetic
`monthly_financials.csv`.

Scope, per the handoff and PROJECT.md §5/D5: this connector touches
`filing_extraction_room` only. `cash_flow_room` stays on synthetic data —
there is no real public substitute for private SMB bank data.

Network: `requests` to `data.sec.gov`, with a descriptive User-Agent
carrying a contact email (SEC's own requirement — an anonymous or
browser-spoofed UA gets rate-limited/blocked). No API key needed.

If the sandbox running this has no route to data.sec.gov, `fetch_company_facts`
raises `EdgarUnavailable` with the underlying error so a caller (or this
module's `__main__` block) can report it as a clean, documented stub rather
than crash or silently fabricate data.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out"
CACHE_DIR = ROOT / "data" / "edgar_cache"

USER_AGENT = "ledger-room-portfolio-project contact: andrew-ledger-room@example.com"

# A handful of real small/mid-cap public companies (CIK = SEC's Central
# Index Key, zero-padded to 10 digits). Chosen only as examples of the
# connector working against real filings — not a claim about these
# specific companies' financial health.
SAMPLE_COMPANIES = {
    "CIK0000320193": "Apple Inc.",            # large-cap, but a stable, always-available XBRL fixture
    "CIK0000029644": "Donaldson Company",     # mid-cap industrial (SEC: DONALDSON Co INC)
    "CIK0000023217": "Conagra Brands",        # mid-cap consumer (SEC: CONAGRA BRANDS INC.)
    # Earlier versions had CIK0001090872 (actually Agilent) and CIK0000875320
    # (actually Vertex) here. pull_sample now checks SEC's entityName.
}

# Maps each accrual_kpis.py field to the GAAP XBRL concepts that can feed it,
# in priority order. Many filers moved revenue off `Revenues` to the ASC 606
# tag after 2018, so revenue walks a list; whichever concept has the most
# recent annual period wins (ties go to the earlier, preferred concept).
GAAP_CONCEPTS = {
    "revenue_accrual": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                        "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "depreciation_amortization": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                                  "DepreciationAmortizationAndAccretionNet"],
    "allowance_doubtful_accounts": ["AllowanceForDoubtfulAccountsReceivableCurrent",
                                    "AllowanceForDoubtfulAccountsReceivable"],
    "ar_balance": ["AccountsReceivableNetCurrent"],
    "net_income": ["NetIncomeLoss"],
}
# Kept for anything importing the old name: concept -> field.
GAAP_CONCEPT_MAP = {c: f for f, cs in GAAP_CONCEPTS.items() for c in cs}

ANNUAL_FORMS = {"10-K", "10-K/A"}
# A full fiscal year is 52 or 53 weeks; this window excludes quarters and
# odd transition periods that 10-Ks also carry.
ANNUAL_DAYS = (350, 380)
# A field whose period ends this long before the company's newest period is
# flagged stale instead of being presented as "latest".
STALE_DAYS = 540


class EdgarUnavailable(RuntimeError):
    """Raised when data.sec.gov cannot be reached. Carries the underlying
    exception so the caller can log/report the real reason rather than a
    generic failure."""


def fetch_company_facts(cik: str, timeout: float = 15.0) -> dict[str, Any]:
    """GET data.sec.gov/api/xbrl/companyfacts/<CIK>.json. Requires a
    descriptive User-Agent per SEC's fair-access policy."""
    try:
        import requests
    except ImportError as exc:
        raise EdgarUnavailable("the 'requests' package is not installed") from exc

    url = f"https://data.sec.gov/api/xbrl/companyfacts/{cik}.json"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise EdgarUnavailable(f"could not reach data.sec.gov: {exc}") from exc
    return resp.json()


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def extract_annual_facts(company_facts: dict[str, Any]) -> dict[str, list[dict]]:
    """Pulls the mapped GAAP concepts out of the raw companyfacts payload.

    Keeps only USD rows from 10-K / 10-K/A filings, and keeps every field
    needed to tell periods apart: the measured period (`start`/`end`; instant
    facts such as AR have only `end`), the filing date, the accession number
    (the citation) and EDGAR's `frame`. A 10-K repeats prior-year comparatives
    under the same accession, so `fy`/`form` alone cannot identify the
    current-year figure; `select_annual` uses the period instead."""
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    out: dict[str, list[dict]] = {}
    for field_name, concepts in GAAP_CONCEPTS.items():
        rows = []
        for concept in concepts:
            node = us_gaap.get(concept)
            if not node:
                continue
            for r in node.get("units", {}).get("USD", []):
                if r.get("form") not in ANNUAL_FORMS or not r.get("end") or not r.get("accn"):
                    continue
                rows.append({
                    "field": field_name,
                    "concept": concept,
                    "value": r.get("val"),
                    "period_start": r.get("start"),        # None for instant (balance) facts
                    "period_end": r.get("end"),
                    "filed": r.get("filed"),
                    "fiscal_year_of_filing": r.get("fy"),  # the FILING's FY, not the figure's
                    "fiscal_period": r.get("fp"),
                    "form": r.get("form"),
                    "frame": r.get("frame"),
                    "accession_number": r.get("accn"),
                    "source_transaction_ids": [r.get("accn")],
                })
        if rows:
            out[field_name] = rows
    return out


def select_annual(rows: list[dict], concept_priority: list[str] | None = None) -> dict | None:
    """Picks the figure for the most recent fiscal year from extracted rows.

    - Duration facts (revenue, net income, D&A) must cover a full year
      (ANNUAL_DAYS); instant facts (AR, allowance) are balances at `end`.
    - The most recent period end wins, which drops the prior-year
      comparatives every 10-K repeats.
    - Restatement policy: for the same period, the most recently FILED row
      wins (a 10-K/A, or the next year's 10-K restating it, supersedes the
      original), so the citation points at the filing whose number is shown.
    - Several concepts can feed one field; ties go to the earlier one in
      `concept_priority`."""
    def is_annual(r: dict) -> bool:
        if r.get("period_start"):
            return ANNUAL_DAYS[0] <= _days(r["period_start"], r["period_end"]) <= ANNUAL_DAYS[1]
        return True  # instant fact

    candidates = [r for r in rows if r.get("value") is not None and is_annual(r)]
    if not candidates:
        return None
    rank = {c: i for i, c in enumerate(concept_priority or [])}
    return max(candidates, key=lambda r: (r["period_end"], -rank.get(r["concept"], 99), r.get("filed") or ""))


def _same_company(expected: str, sec_name: str) -> bool:
    """Loose match on the first word of the name ("Apple Inc." vs "Apple Inc.",
    "Conagra Brands" vs "CONAGRA BRANDS INC.")."""
    first = lambda n: (n.split() or [""])[0].strip(".,").lower()
    return bool(sec_name) and first(expected) == first(sec_name)


def pull_sample(companies: dict[str, str] | None = None) -> dict[str, Any]:
    """Pulls and caches extracted facts for each sample company. Writes a
    per-company JSON cache under data/edgar_cache/ so filing_extraction_room
    (or a future room) can read real filings without re-hitting the network
    every run. Returns a summary dict; never raises past the first
    unreachable company — it records the failure and keeps going, since one
    company being unavailable shouldn't block the others."""
    companies = companies or SAMPLE_COMPANIES
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for cik, name in companies.items():
        try:
            raw = fetch_company_facts(cik)
            entity = raw.get("entityName") or ""
            if not _same_company(name, entity):
                results[cik] = {"company_name": name, "status": "name_mismatch", "sec_entity_name": entity,
                                "reason": f"{cik} is '{entity}' at SEC, not '{name}'; fix SAMPLE_COMPANIES"}
                continue
            facts = extract_annual_facts(raw)
            facts["_sec_entity_name"] = entity
            cache_path = CACHE_DIR / f"{cik}.json"
            cache_path.write_text(json.dumps(facts, indent=2))
            results[cik] = {"company_name": name, "status": "ok", "sec_entity_name": entity,
                             "fields_extracted": sorted(facts), "cache_path": str(cache_path)}
        except EdgarUnavailable as exc:
            results[cik] = {"company_name": name, "status": "blocked", "reason": str(exc)}
    return results


def _write_stub(reason: str) -> None:
    """Writes a documented stub to out/ so the handoff's deliverable list
    is satisfied even when the network is unavailable — never fabricate a
    pull result."""
    OUT_DIR.mkdir(exist_ok=True)
    stub = {
        "status": "blocked",
        "reason": reason,
        "note": (
            "EDGAR pull not executed in this sandbox run. This is a clean "
            "stub, not a fabricated result. Re-run `python3 skills/edgar.py` "
            "from an environment with network access to data.sec.gov to "
            "populate data/edgar_cache/."
        ),
    }
    (OUT_DIR / "edgar_pull_status.json").write_text(json.dumps(stub, indent=2))
    print(f"[BLOCKED] EDGAR pull: {reason}")
    print(f"Wrote stub to {OUT_DIR / 'edgar_pull_status.json'}")


def _self_check() -> None:
    """Offline: a synthetic companyfacts payload shaped like EDGAR's."""
    def row(val, start, end, accn, filed, fy, form="10-K"):
        r = {"val": val, "end": end, "accn": accn, "filed": filed, "fy": fy, "fp": "FY", "form": form}
        if start:
            r["start"] = start
        return r
    payload = {"facts": {"us-gaap": {
        # FY2025 10-K carries FY2025 plus two comparative years, all with the same accession.
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            row(300, "2022-10-01", "2023-09-30", "A-25", "2025-11-01", 2025),
            row(350, "2023-10-01", "2024-09-30", "A-25", "2025-11-01", 2025),
            row(400, "2024-10-01", "2025-09-30", "A-25", "2025-11-01", 2025),
            row(100, "2025-07-01", "2025-09-30", "A-25", "2025-11-01", 2025),   # a quarter inside the 10-K
        ]}},
        "Revenues": {"units": {"USD": [row(215, "2015-10-01", "2016-09-30", "A-18", "2018-11-05", 2018)]}},
        "NetIncomeLoss": {"units": {"USD": [
            row(50, "2023-10-01", "2024-09-30", "A-24", "2024-11-01", 2024),
            row(55, "2023-10-01", "2024-09-30", "A-24A", "2025-02-01", 2024, form="10-K/A"),
        ]}},
        "AccountsReceivableNetCurrent": {"units": {"USD": [
            row(70, None, "2024-09-30", "A-25", "2025-11-01", 2025),
            row(80, None, "2025-09-30", "A-25", "2025-11-01", 2025),
        ]}},
    }}}
    ex = extract_annual_facts(payload)
    rev = select_annual(ex["revenue_accrual"], GAAP_CONCEPTS["revenue_accrual"])
    ni = select_annual(ex["net_income"], GAAP_CONCEPTS["net_income"])
    ar = select_annual(ex["ar_balance"], GAAP_CONCEPTS["ar_balance"])
    checks = [
        ("current-year revenue chosen over the comparatives in the same 10-K", rev and rev["value"] == 400),
        ("a quarter inside a 10-K is never chosen as annual", rev and rev["period_start"] == "2024-10-01"),
        ("stale `Revenues` tag loses to the newer ASC 606 concept", rev and rev["concept"].startswith("RevenueFrom")),
        ("period dates are kept on the chosen row", rev and rev["period_end"] == "2025-09-30"),
        ("a 10-K/A restatement supersedes the original for the same period",
         ni and ni["value"] == 55 and ni["accession_number"] == "A-24A"),
        ("instant facts pick the latest balance date", ar and ar["value"] == 80),
        ("no rows -> None, not a guess", select_annual([]) is None),
        ("company name check accepts SEC casing", _same_company("Conagra Brands", "CONAGRA BRANDS INC.")),
        ("company name check rejects a wrong CIK", not _same_company("Donaldson Company", "AGILENT TECHNOLOGIES, INC.")),
    ]
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")


def main() -> None:
    try:
        import requests  # noqa: F401
    except ImportError:
        _write_stub("the 'requests' package is not installed in this environment")
        return

    results = pull_sample()
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "edgar_pull_status.json").write_text(json.dumps(results, indent=2))

    any_ok = any(r["status"] == "ok" for r in results.values())
    for cik, r in results.items():
        tag = "OK" if r["status"] == "ok" else "BLOCKED"
        print(f"[{tag}] {r['company_name']} ({cik}): {r.get('fields_extracted', r.get('reason'))}")

    if not any_ok:
        print("All EDGAR pulls blocked — see out/edgar_pull_status.json")
        sys.exit(1)


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        main()
