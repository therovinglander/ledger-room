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
    "CIK0001090872": "Donaldson Company",     # mid-cap industrial
    "CIK0000875320": "Conagra Brands",        # mid-cap consumer
}

# Maps GAAP XBRL concept -> the accrual_kpis.py field it feeds.
GAAP_CONCEPT_MAP = {
    "Revenues": "revenue_accrual",
    "DepreciationDepletionAndAmortization": "depreciation_amortization",
    "AllowanceForDoubtfulAccountsReceivable": "allowance_doubtful_accounts",
    "AccountsReceivableNetCurrent": "ar_balance",
    "NetIncomeLoss": "net_income",
}


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


def extract_annual_facts(company_facts: dict[str, Any]) -> dict[str, list[dict]]:
    """Pulls the mapped GAAP concepts out of the raw companyfacts payload,
    keeping only annual (10-K, form FY) USD-denominated facts with their
    accession number and frame as the citation (EDGAR's own evidence that
    a number came from a specific filing, not a model guess)."""
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    out: dict[str, list[dict]] = {}
    for concept, field_name in GAAP_CONCEPT_MAP.items():
        node = us_gaap.get(concept)
        if not node:
            continue
        usd_rows = node.get("units", {}).get("USD", [])
        annual_rows = [
            {
                "field": field_name,
                "concept": concept,
                "value": r.get("val"),
                "fiscal_year": r.get("fy"),
                "fiscal_period": r.get("fp"),
                "form": r.get("form"),
                "accession_number": r.get("accn"),
                "source_transaction_ids": [r.get("accn")] if r.get("accn") else [],
            }
            for r in usd_rows
            if r.get("form") == "10-K"
        ]
        if annual_rows:
            out[field_name] = annual_rows
    return out


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
            facts = extract_annual_facts(raw)
            cache_path = CACHE_DIR / f"{cik}.json"
            cache_path.write_text(json.dumps(facts, indent=2))
            results[cik] = {"company_name": name, "status": "ok",
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
    main()
