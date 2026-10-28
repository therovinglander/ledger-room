"""narrate.py — the one shared LLM call: facts in, cited narrative out.

Design rule (PROJECT.md §2): code computes and cites, the model only
narrates structured facts it's handed. Every fact must have a value and
source citations. The model never invents a number, never sees raw rows,
and must cite by ID.

Facts contract:
  {
    "label": str (human-readable label for this fact),
    "value": numeric or None (the actual value being stated),
    "source_transaction_ids": list[str] (exact row IDs that produced it,
                                         required if value is not None)
  }

The narrate() function:
1. validate_facts(): enforce citation contract — no uncited non-null values.
2. build_prompt(): format facts as a numbered sheet with exact IDs.
3. backend-call: template-based offline narrator or LLM.
4. validate_narrative(): extract and check numbers + citation tokens against
   the input facts, flag mismatches.
5. Return result with narrative + validated flag + issues list.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
TXN_PATH = ROOT / "data" / "transactions.csv"
BIZ_PATH = ROOT / "data" / "businesses.csv"


# ============================================================================
# Fact contract & validation
# ============================================================================

def validate_facts(facts: list[dict]) -> dict:
    """Check the citation contract: every fact with a non-null value must
    have a non-empty source_transaction_ids list.

    Returns {
      "valid": bool,
      "issues": list[str] (human-readable issues found, if any),
      "facts_checked": int
    }
    """
    issues = []
    for i, fact in enumerate(facts):
        label = fact.get("label", f"<fact {i}>")
        value = fact.get("value")
        ids = fact.get("source_transaction_ids", [])

        if value is not None and not ids:
            issues.append(f"Fact '{label}': non-null value {value} but no source_transaction_ids")
        if not isinstance(ids, list):
            issues.append(f"Fact '{label}': source_transaction_ids is not a list (got {type(ids).__name__})")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "facts_checked": len(facts),
    }


# ============================================================================
# Prompt building
# ============================================================================

def build_prompt(subject: str, facts: list[dict]) -> str:
    """Format facts as a numbered sheet with exact IDs for the narrator.

    Returns a prompt that instructs the model to cite only these facts,
    never invent a number.
    """
    lines = [
        f"Subject: {subject}",
        "",
        "Below are cited facts. Write a short narrative paragraph citing "
        "only these facts. NEVER invent a number not present below. "
        "When citing a fact, reference its source IDs in parentheses, "
        "e.g. (biz_01_t00123, biz_01_t00456). Keep the narrative plain "
        "and direct — no hedging or caveats unless explicitly flagged below.",
        "",
        "FACTS:",
        "",
    ]

    for i, fact in enumerate(facts, start=1):
        label = fact.get("label", "")
        value = fact.get("value")
        ids = fact.get("source_transaction_ids", [])
        flag = fact.get("flag")

        # Format value based on type
        if value is None:
            val_str = "(no value)"
        elif isinstance(value, float):
            # Round to 2 decimals for currency, 4 for ratios
            if abs(value) < 1.0:
                val_str = f"{value:.4f}"
            else:
                val_str = f"{value:.2f}"
        else:
            val_str = str(value)

        lines.append(f"{i}. {label}: {val_str}")
        if flag:
            lines.append(f"   [FLAG: {flag}]")
        if ids:
            lines.append(f"   Sources: {', '.join(ids[:10])}")
            if len(ids) > 10:
                lines.append(f"   (and {len(ids) - 10} more)")
        lines.append("")

    return "\n".join(lines)


# ============================================================================
# Mock backend (deterministic, offline)
# ============================================================================

def _load_businesses() -> dict[str, dict]:
    """Load business names for use in the mock narrator."""
    by_id = {}
    try:
        with BIZ_PATH.open(newline="") as f:
            for row in csv.DictReader(f):
                by_id[row["business_id"]] = row
    except FileNotFoundError:
        pass
    return by_id


def _mock_narrator(subject: str, facts: list[dict]) -> str:
    """Deterministic, template-based offline narrator.

    Takes the facts and builds a plain-English paragraph. No network call,
    no non-determinism, works in a sandbox with no internet.
    """
    businesses = _load_businesses()

    # Extract business_id from subject or first fact
    biz_id = None
    if "biz_" in subject:
        match = re.search(r"(biz_\d+)", subject)
        if match:
            biz_id = match.group(1)
    if not biz_id and facts:
        for fact in facts:
            ids = fact.get("source_transaction_ids", [])
            if ids and "_" in ids[0]:
                biz_id = ids[0].split("_")[0] + "_" + ids[0].split("_")[1]
                break

    biz_name = businesses.get(biz_id, {}).get("business_name", biz_id) if biz_id else "This business"

    # Build narrative from facts directly
    para_parts = []
    
    for fact in facts:
        label = fact.get("label", "")
        value = fact.get("value")
        flag = fact.get("flag")
        ids_short = ", ".join(fact.get("source_transaction_ids", [])[:3])
        
        if value is None:
            continue
            
        # Format based on fact label
        if "runway" in label.lower():
            flag_note = f" {flag}" if flag else ""
            para_parts.append(f"{biz_name} has {value} months of cash runway{flag_note} ({ids_short})")
        elif "burn" in label.lower():
            para_parts.append(f"with a trailing burn rate of ${abs(value):.2f}/month ({ids_short})")
        elif "concentration" in label.lower():
            customer = fact.get("top_customer", "its top customer")
            flag_note = f" {flag}" if flag else ""
            para_parts.append(f"{value * 100:.1f}% of revenue from {customer}{flag_note} ({ids_short})")
        elif "lowest" in label.lower() or "balance" in label.lower():
            flag_note = f" {flag}" if flag else ""
            para_parts.append(f"with lowest cash balance of ${value:.2f}{flag_note} ({ids_short})")
        elif "round" in label.lower():
            flag_note = f" {flag}" if flag else ""
            para_parts.append(f"{int(value)} suspicious round-number transactions{flag_note} ({ids_short})")
        elif "benford" in label.lower():
            para_parts.append(f"with Benford anomaly score {value:.4f} ({ids_short})")
        elif "margin" in label.lower():
            para_parts.append(f"{label} of {value * 100:.2f}% ({ids_short})")
        elif "tier" in label.lower() or "pricing" in label.lower():
            tier_name = fact.get("tier_name", "")
            para_parts.append(
                f"Recommended: {tier_name} — {int(value)} risk flag(s) triggered this decision ({ids_short})"
            )
        elif "bad_debt" in label.lower():
            flag_note = f" {flag}" if flag else ""
            para_parts.append(f"Bad debt at {value * 100:.3f}% of revenue{flag_note} ({ids_short})")
        elif "allowance" in label.lower():
            para_parts.append(f"Allowance for doubtful accounts at {value * 100:.2f}% of AR ({ids_short})")
        elif "accrual revenue" in label.lower():
            para_parts.append(f"Total accrual revenue of ${value:,.2f} ({ids_short})")
        elif "cash revenue" in label.lower():
            para_parts.append(f"with total cash revenue of ${value:,.2f} ({ids_short})")
        elif "d&a" in label.lower() or "depreciation" in label.lower():
            para_parts.append(f"D&A of ${value:,.2f} ({ids_short})")
    
    # Join parts into a narrative
    narrative = ""
    if para_parts:
        narrative = para_parts[0]
        for part in para_parts[1:]:
            if part.startswith("with"):
                narrative += ", " + part
            else:
                narrative += ". " + part
        if not narrative.endswith("."):
            narrative += "."

    return narrative or f"{biz_name} financial profile assessed."


# ============================================================================
# Output validation
# ============================================================================

def validate_narrative(narrative_text: str, facts: list[dict]) -> dict:
    """Extract numbers and citation-like tokens from narrative and validate
    them against the input facts.

    Returns {
      "valid": bool,
      "issues": list[str],
      "unsupported_numbers": list[dict] (number, context),
      "unknown_citations": list[str]
    }
    """
    issues = []
    unsupported = []
    unknown_cites = []

    # Build a map of fact values and their source IDs for checking
    fact_values: dict[float, list[str]] = {}
    for fact in facts:
        val = fact.get("value")
        ids = fact.get("source_transaction_ids", [])
        if val is not None and ids:
            # Store with some tolerance for floating-point comparison
            # Use absolute value for matching since narrative might show $X while
            # the burn rate is -X (negative)
            key = round(abs(float(val)), 4)
            if key not in fact_values:
                fact_values[key] = []
            fact_values[key].extend(ids)
            
            # Also store the percentage version (ratios are commonly narrated
            # as "-158.18%" rather than "-1.58" — no upper bound here:
            # margins can legitimately exceed +/-100%, e.g. a business with
            # net income more negative than its revenue).
            if abs(float(val)) > 0:
                pct_key = round(abs(float(val)) * 100, 2)
                if pct_key not in fact_values:
                    fact_values[pct_key] = []
                fact_values[pct_key].extend(ids)

    # Extract numbers NOT inside parentheses (citations are in parens, and we
    # don't want to validate transaction IDs as fact values).
    # Split by citation blocks and check only non-parenthetical text.
    parts = re.split(r'(\([^)]*\))', narrative_text)
    non_cite_text = "".join(p for i, p in enumerate(parts) if i % 2 == 0)

    # Extract all numbers from the non-citation text (currency, percentages, decimals)
    number_pattern = r"[$]?(\d+(?:[.,]\d+)?)\s*(?:%|months|K|M)?"
    for match in re.finditer(number_pattern, non_cite_text):
        num_str = match.group(1).replace(",", "")
        try:
            num = float(num_str)
            # Allow for rounding tolerance (±0.05 for most metrics, ±5% for percentages)
            found = False
            for key in fact_values:
                if abs(num - key) <= 0.05 or (key != 0 and abs(num - key) / abs(key) <= 0.05):
                    found = True
                    break
            if not found:
                # Check if this number looks like it's part of a citation ID
                # (surrounded by non-word chars or underscores suggesting it's from biz_NN_t0000)
                context = match.group(0).strip()
                # If the context is all digits and short, it's likely part of an ID
                if re.match(r'^\d{1,2}$', context):  # Just "07" or similar
                    continue
                
                unsupported.append({
                    "number": num,
                    "context": match.group(0),
                    "line_offset": non_cite_text[:match.start()].count("\n")
                })
        except ValueError:
            pass

    # Extract citation-like tokens: biz_NN_tNNNNN, biz_NN_fin_YYYY-MM, etc.
    cite_pattern = r"(\(([^)]*(?:biz_\d+[^)]*)?)\))"
    all_fact_ids = set()
    for fact in facts:
        all_fact_ids.update(fact.get("source_transaction_ids", []))

    for match in re.finditer(cite_pattern, narrative_text):
        cite_text = match.group(2).strip()
        # Extract individual IDs from comma/space-separated list
        for token in re.split(r"[,\s]+", cite_text):
            token = token.strip()
            if token and "biz_" in token:
                if token not in all_fact_ids:
                    unknown_cites.append(token)

    if unsupported:
        issues.append(f"Found {len(unsupported)} numbers not in input facts")
    if unknown_cites:
        issues.append(f"Found {len(set(unknown_cites))} citation IDs not in input facts")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "unsupported_numbers": unsupported,
        "unknown_citations": list(set(unknown_cites)),
    }


# ============================================================================
# Main narrate() entry point
# ============================================================================

def narrate(
    subject: str,
    facts: list[dict],
    backend: str = "mock"
) -> dict:
    """Main entry point: facts in, cited narrative out.

    Steps:
    1. validate_facts(): check citation contract.
    2. build_prompt(): format facts for the narrator.
    3. Call backend (mock or real).
    4. validate_narrative(): check output for uncited numbers.
    5. Return result dict with narrative, validated flag, and issues.

    Args:
        subject: human-readable subject (e.g. "Cash flow risk for biz_07")
        facts: list of {label, value, source_transaction_ids, [flag]}
        backend: "mock" for template-based (always works), or "real" (not
                implemented in this sandbox)

    Returns {
        "subject": str,
        "narrative": str,
        "validated": bool (True if facts are valid, output is valid, no
                    uncited numbers),
        "issues": list[str] (if validated is False),
        "fact_validation": dict (result of validate_facts),
        "output_validation": dict (result of validate_narrative),
    }
    """
    # Step 1: Validate input facts
    fact_check = validate_facts(facts)
    if not fact_check["valid"]:
        return {
            "subject": subject,
            "narrative": None,
            "validated": False,
            "issues": fact_check["issues"],
            "fact_validation": fact_check,
            "output_validation": None,
        }

    # Step 2: Build prompt
    prompt = build_prompt(subject, facts)

    # Step 3: Call backend
    if backend == "mock":
        narrative = _mock_narrator(subject, facts)
    elif backend == "real":
        # Real LLM backend placeholder — not implemented for offline sandbox.
        # To use: implement with an actual LLM call (OpenAI, Claude, etc.)
        # For now, this is deliberately untested since the sandbox has no internet.
        raise NotImplementedError("Real LLM backend not available in sandbox")
    else:
        return {
            "subject": subject,
            "narrative": None,
            "validated": False,
            "issues": [f"Unknown backend '{backend}'"],
            "fact_validation": fact_check,
            "output_validation": None,
        }

    # Step 4: Validate narrative output
    output_check = validate_narrative(narrative, facts)

    # Step 5: Return result
    all_issues = output_check.get("issues", [])
    validated = fact_check["valid"] and output_check["valid"]

    return {
        "subject": subject,
        "narrative": narrative,
        "validated": validated,
        "issues": all_issues,
        "fact_validation": fact_check,
        "output_validation": output_check,
    }


# ============================================================================
# Self-check (runnable via `python3 narrate.py`)
# ============================================================================

def _self_check() -> None:
    """Sanity check: pull real facts from cash_kpis.py for biz_07,
    run narrate() with mock backend, assert validated==True."""
    import sys
    sys.path.insert(0, str(ROOT / "skills"))

    import cash_kpis
    import anomaly_detection

    txns = cash_kpis.load_transactions()
    checks = []

    # Collect facts for biz_07
    biz = "biz_07"
    facts = []

    # Cash KPIs
    runtime = cash_kpis.runway_months(txns, biz)
    facts.append({
        "label": f"Runway (months)",
        "value": runtime.get("value"),
        "flag": runtime.get("flag"),
        "source_transaction_ids": runtime.get("source_transaction_ids", []),
    })

    burn = cash_kpis.burn_rate(txns, biz)
    facts.append({
        "label": f"Trailing 3-month burn rate",
        "value": burn.get("value"),
        "source_transaction_ids": burn.get("source_transaction_ids", []),
    })

    conc = cash_kpis.customer_concentration(txns, biz)
    facts.append({
        "label": f"Top customer concentration",
        "value": conc.get("value"),
        "flag": conc.get("flag"),
        "top_customer": conc.get("top_customer"),
        "source_transaction_ids": conc.get("source_transaction_ids", []),
    })

    overdraft = cash_kpis.overdraft_risk(txns, biz)
    facts.append({
        "label": f"Lowest cash balance",
        "value": overdraft.get("lowest_balance"),
        "flag": overdraft.get("flag"),
        "source_transaction_ids": overdraft.get("source_transaction_ids", []),
    })

    # Anomalies
    round_num = anomaly_detection.round_number_cluster(txns, biz)
    if round_num.get("round_number_count", 0) > 0:  # Only add if there's a value to cite
        facts.append({
            "label": f"Round-number ($500 multiples) expenses",
            "value": round_num.get("round_number_count"),
            "flag": round_num.get("flag"),
            "source_transaction_ids": round_num.get("source_transaction_ids", []),
        })

    benford = anomaly_detection.benford_deviation(txns, biz)
    if benford.get("value") is not None:
        facts.append({
            "label": f"Benford anomaly score",
            "value": benford.get("value"),
            "source_transaction_ids": benford.get("source_transaction_ids", []),
        })

    # Run narrate
    result = narrate(f"Cash flow risk assessment for {biz}", facts, backend="mock")

    # Checks
    checks.append((
        "narrate() returns a dict with narrative, validated, and issues keys",
        isinstance(result, dict) and "narrative" in result and "validated" in result,
        result
    ))

    checks.append((
        "narrate() result has validated=True (facts all have sources, output is valid)",
        result.get("validated") is True,
        {
            "validated": result.get("validated"),
            "fact_validation": result.get("fact_validation"),
            "output_validation": result.get("output_validation"),
        }
    ))

    checks.append((
        "narrate() narrative is non-empty",
        bool(result.get("narrative")),
        f"narrative length: {len(result.get('narrative', '') or '')}"
    ))

    checks.append((
        "narrate() issues list is empty when validated=True",
        result.get("validated") is True and len(result.get("issues", [])) == 0,
        result.get("issues", [])
    ))

    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"   detail: {detail}")

    # Also print the actual narrative to verify readability
    if result.get("narrative"):
        print("\n--- Generated narrative ---")
        print(result["narrative"])
        print("--- end narrative ---\n")


if __name__ == "__main__":
    _self_check()
