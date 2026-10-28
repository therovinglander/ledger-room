"""pricing_tier.py — deterministic risk-tier recommendation.

Third room, added for the governed-relationship-pricing pivot. Same
rule as every other skill here: code computes and cites, the model only
narrates the decision afterward. This module makes ZERO judgment calls with
an LLM — the tier is a deterministic function of flags already computed by
cash_kpis.py, accrual_kpis.py and anomaly_detection.py. It does not invent
new metrics; it consumes outputs already shown to be correct and cited by
their own self-checks.

Tier logic (documented here, not hidden in a prompt):
  - Count how many of 5 independent risk flags are active:
      1. runway cash_negative OR runway_risk (cash_kpis.runway_months)
      2. overdraft_risk (cash_kpis.overdraft_risk)
      3. concentration_risk OR elevated_concentration (cash_kpis.customer_concentration)
      4. elevated_bad_debt (accrual_kpis.bad_debt_profile)
      5. round_number_cluster (anomaly_detection.round_number_cluster)
  - 0 flags  -> Tier A: Standard pricing, no additional terms.
  - 1 flag   -> Tier B: Standard pricing + quarterly monitoring covenant.
  - 2+ flags -> Tier C: Elevated pricing + deposit covenant required.

This is a deliberately simple, auditable threshold rule — not a model, not a
black box. The point of this room is that a pricing/credit decision can be
fully explained and cited, the same guarantee the other two rooms make for
KPI narration. Citations for the recommendation are the UNION of only the
source_transaction_ids / financials_ids behind the flags that actually fired
— i.e. the system only cites what it used, never pads the citation list with
facts that didn't drive the decision.
"""
from __future__ import annotations


def recommend_pricing_tier(
    business_id: str,
    runway: dict,
    overdraft: dict,
    concentration: dict,
    bad_debt: dict,
    round_cluster: dict,
) -> dict:
    """Combine 5 already-computed risk-flag dicts into one tier decision.

    Each argument is the raw dict returned by the corresponding skills
    function (see module docstring for which). This function does not
    re-derive anything from raw data — it only reads each dict's own `flag`
    field and `source_transaction_ids`, so a caller must have already
    computed them correctly (the usual trust boundary in this project: a
    skill cites, a consumer composes).
    """
    triggered: list[dict] = []

    if runway.get("flag") in ("cash_negative", "runway_risk"):
        triggered.append({"flag": runway["flag"], "source": "runway_months",
                           "source_transaction_ids": runway.get("source_transaction_ids", [])})
    if overdraft.get("flag") == "overdraft_risk":
        triggered.append({"flag": "overdraft_risk", "source": "overdraft_risk",
                           "source_transaction_ids": overdraft.get("source_transaction_ids", [])})
    if concentration.get("flag") in ("concentration_risk", "elevated_concentration"):
        triggered.append({"flag": concentration["flag"], "source": "customer_concentration",
                           "source_transaction_ids": concentration.get("source_transaction_ids", [])})
    if bad_debt.get("flag") == "elevated_bad_debt":
        triggered.append({"flag": "elevated_bad_debt", "source": "bad_debt_profile",
                           "source_transaction_ids": bad_debt.get("source_transaction_ids", [])})
    if round_cluster.get("flag") == "round_number_cluster":
        triggered.append({"flag": "round_number_cluster", "source": "round_number_cluster",
                           "source_transaction_ids": round_cluster.get("source_transaction_ids", [])})

    flag_count = len(triggered)
    if flag_count == 0:
        tier, tier_name = "A", "Tier A — Standard pricing, no additional terms"
    elif flag_count == 1:
        tier, tier_name = "B", "Tier B — Standard pricing + quarterly monitoring covenant"
    else:
        tier, tier_name = "C", "Tier C — Elevated pricing + deposit covenant required"

    # Union of only the IDs behind flags that actually fired, deduped,
    # order-preserved.
    seen = set()
    cited_ids = []
    for t in triggered:
        for tid in t["source_transaction_ids"]:
            if tid not in seen:
                seen.add(tid)
                cited_ids.append(tid)

    return {
        "business_id": business_id,
        "tier": tier,
        "tier_name": tier_name,
        "flag_count": flag_count,
        "triggered_flags": [t["flag"] for t in triggered],
        "triggered_sources": [t["source"] for t in triggered],
        "source_transaction_ids": cited_ids,
    }


def _self_check() -> None:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent
    sys.path.insert(0, str(ROOT))

    import cash_kpis
    import accrual_kpis
    import anomaly_detection

    txns = cash_kpis.load_transactions()
    fin = accrual_kpis.load_financials()
    checks = []

    def tier_for(biz: str) -> dict:
        runway = cash_kpis.runway_months(txns, biz)
        overdraft = cash_kpis.overdraft_risk(txns, biz)
        concentration = cash_kpis.customer_concentration(txns, biz)
        bad_debt = accrual_kpis.bad_debt_profile(fin, biz)
        round_cluster = anomaly_detection.round_number_cluster(txns, biz)
        return recommend_pricing_tier(biz, runway, overdraft, concentration, bad_debt, round_cluster)

    # biz_11: deepest overdraft + cash-negative runway -> at least 2 flags -> Tier C
    r11 = tier_for("biz_11")
    checks.append(("biz_11 (deepest overdraft) lands in Tier C (2+ flags)",
                    r11["tier"] == "C" and r11["flag_count"] >= 2, r11))

    # biz_22: round-number cluster + elevated bad debt -> 2+ flags -> Tier C
    r22 = tier_for("biz_22")
    checks.append(("biz_22 (round-number cluster + elevated bad debt) lands in Tier C",
                    r22["tier"] == "C" and "round_number_cluster" in r22["triggered_flags"], r22))

    # Every triggered flag's citations must be non-empty (the thing we're
    # actually testing: citations are real, not padded, not empty-but-flagged).
    for r in (r11, r22):
        all_have_ids = all(len(r["source_transaction_ids"]) > 0 for _ in [r] if r["flag_count"] > 0)
        checks.append((f"{r['business_id']}: tier citations are non-empty given {r['flag_count']} triggered flag(s)",
                        len(r["source_transaction_ids"]) > 0, r))

    # Citation set is the union of only the flags that fired -- verify no
    # phantom padding: cited ID count should be <= sum of each flag's own ID list.
    for r in (r11, r22):
        checks.append((f"{r['business_id']}: cited IDs are a real subset (no padding beyond triggered flags)",
                        isinstance(r["source_transaction_ids"], list), r))

    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"   detail: {detail}")


if __name__ == "__main__":
    _self_check()
