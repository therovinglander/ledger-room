"""narrate.py — the one shared LLM call: facts in, cited narrative out.

Design rule (PROJECT.md §2, docs/PERSONAS.md §3): code computes and cites,
the model narrates STRUCTURED FACTS ONLY — it never sees a raw transaction
or financials row. A "fact" here is exactly the shape the skills/*.py
functions already return once normalized: ``{"label", "value",
"source_transaction_ids"}``.

Two layers of validation around the one model call, mirroring the
"re-derive, don't trust" pattern in skills/citation.py:

1. **Input validation** (`validate_facts`) — before the model ever sees the
   facts, every fact with a non-null value must carry a non-empty
   `source_transaction_ids` list. A fact that fails this is a bug in the
   calling room, not something narrate.py should paper over.
2. **Output validation** (`validate_narrative`) — after the model responds,
   the narrative is split into claims (the text before each run of
   `[cite:<id>]` tokens). Every number in a claim must equal (within
   rounding) the value of a fact whose own source IDs are cited right after
   it; every citation must be a known ID that belongs to a fact stated in
   that claim; no number may appear uncited; and every numeric fact must be
   stated. Any miss makes the result not ok — nothing is best-effort.

Two backends:

- ``mock`` — deterministic, template-based, fully offline. This is what
  `_self_check()` runs, since this sandbox has no network and the real
  LLM path has never been network-tested here (see handoff notes).
- ``openai`` — real backend using the Chat Completions API. Not exercised
  by `_self_check()`; test separately once there is connectivity.

Citation token format in narrative text: ``[cite:<source_transaction_id>]``
immediately after the sentence/number it supports, e.g.
"Runway is 4.2 months [cite:txn_00123][cite:txn_00124]."
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

CITE_RE = re.compile(r"\[cite:([^\]\s]+)\]")
# A run of one or more adjacent citation tokens closes a "claim": the text
# since the previous run is what those citations support.
CITE_RUN_RE = re.compile(r"(?:\s*\[cite:[^\]\s]+\])+")
# Numbers in narrative text: optional $ and %, optional thousands separators,
# optional decimal part, optional leading minus. Digits glued to letters,
# underscores, dots or hyphens are NOT claims: that skips identifiers and
# labels such as biz_03, FY2025, 10-K, 3-month, 2025-09-27 and accession
# numbers like 0000320193-25-000079.
NUMBER_RE = re.compile(r"(?<![\w.\-])-?\$?\d[\d,]*(?:\.\d+)?%?(?![\w\-]|\.\d)")


@dataclass
class Fact:
    label: str
    value: Any
    source_transaction_ids: list[str] = field(default_factory=list)
    note: str | None = None  # optional free-text context (e.g. a flag name)

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        return cls(
            label=d["label"],
            value=d.get("value"),
            source_transaction_ids=list(d.get("source_transaction_ids") or []),
            note=d.get("note"),
        )


def validate_facts(facts: list[Fact]) -> dict:
    """Every fact with a non-null value must carry a non-empty
    source_transaction_ids list. Returns {"ok": bool, "violations": [...]}
    rather than raising, so a caller can decide whether to proceed with a
    partial fact list or abort — but it must look at the result."""
    violations = []
    for f in facts:
        if f.value is not None and not f.source_transaction_ids:
            violations.append({"label": f.label, "value": f.value,
                                "reason": "non-null value with no source_transaction_ids"})
    return {"ok": not violations, "violations": violations}


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _numbers_in_text(text: str) -> list[str]:
    return NUMBER_RE.findall(CITE_RE.sub(" ", text))


def _token_matches(tok: str, value: Any) -> bool:
    """True if one number token from the text states `value`.

    Accepts the value written exactly (within 0.015), or correctly rounded
    to the token's own precision ("4,073" for 4073.44, but never "4,000"),
    and a 0-1 fraction written as a percent ("39%" for 0.39)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    cleaned = tok.replace("$", "").replace(",", "").replace("%", "")
    try:
        tok_val = float(cleaned)
    except ValueError:
        return False
    decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
    v = float(value)
    candidates = [v]
    if "%" in tok and abs(v) <= 1:
        candidates.append(v * 100)
    for c in candidates:
        if abs(tok_val - c) < 0.015 or abs(tok_val - round(c, decimals)) < 10 ** -(decimals + 3):
            return True
    return False


def _value_appears(value: Any, numbers_in_text: list[str]) -> bool:
    """True if `value` matches some number token (see _token_matches)."""
    if value is None:
        return True  # nothing to check
    return any(_token_matches(tok, value) for tok in numbers_in_text)


def _claims(narrative: str) -> tuple[list[tuple[str, list[str]]], str]:
    """Split a narrative into (claim_text, cited_ids) pairs, one per run of
    citation tokens, plus any trailing text after the last run (which, by
    construction, has no citation)."""
    claims = []
    pos = 0
    for m in CITE_RUN_RE.finditer(narrative):
        claims.append((narrative[pos:m.start()], CITE_RE.findall(m.group(0))))
        pos = m.end()
    return claims, narrative[pos:]


def validate_narrative(narrative: str, facts: list[Fact], require_full_coverage: bool = True) -> dict:
    """Re-derives every claim in a model-produced narrative from the input
    facts. Nothing is trusted from the model; each check is a hard failure:

    1. unknown_citations: a [cite:<id>] that no input fact carries (an
       invented source).
    2. uncited_numbers: a number in the text with no citation run after it.
    3. unsupported_numbers: a cited number that matches no fact whose own
       source IDs are among the citations right after it (an invented value,
       or a real value pinned to the wrong fact's sources).
    4. misattributed_citations: a citation attached to a claim whose numbers
       don't come from a fact that carries that ID (a real source cited for
       the wrong fact).
    Fact labels and notes quoted verbatim are code-authored text, so digits
    inside them are skipped; any other number is checked.

    5. uncovered_facts: a numeric fact the narrative never states with its
       own citation (when require_full_coverage, the default). This is also
       what makes an empty or number-free narrative fail.

    Returns {"ok": bool, <each list above>, "cited_ids": [...], "numbers_found": [...]}.
    """
    known_ids = {tid for f in facts for tid in f.source_transaction_ids}
    numeric_facts = [f for f in facts
                     if isinstance(f.value, (int, float)) and not isinstance(f.value, bool)
                     and f.source_transaction_ids]

    # Fact labels and notes are written by code, not the model (e.g. "fewer
    # than 10 transactions"). Quoted verbatim, their digits are not claims;
    # the same digits anywhere else still are.
    fixed_text = sorted({t for f in facts for t in (f.label, f.note) if t}, key=len, reverse=True)

    def claim_numbers(text: str) -> list[str]:
        for t in fixed_text:
            text = text.replace(t, " ")
        return _numbers_in_text(text)

    claims, trailing = _claims(narrative)
    unknown, uncited, unsupported, misattributed = set(), [], [], []
    covered: set[int] = set()

    for text, cited in claims:
        cited_set = set(cited)
        unknown |= {c for c in cited_set if c not in known_ids}
        supported_ids: set[str] = set()
        for tok in claim_numbers(text):
            backing = [i for i, f in enumerate(numeric_facts)
                       if _token_matches(tok, f.value) and cited_set & set(f.source_transaction_ids)]
            if not backing:
                unsupported.append({"number": tok, "cited": sorted(cited_set), "claim": text.strip()[-160:]})
                continue
            covered.update(backing)
            for i in backing:
                supported_ids |= set(numeric_facts[i].source_transaction_ids)
        stray = sorted((cited_set & known_ids) - supported_ids)
        if stray:
            misattributed.append({"ids": stray, "claim": text.strip()[-160:]})

    for tok in claim_numbers(trailing):
        uncited.append({"number": tok, "claim": trailing.strip()[-160:]})

    uncovered = ([{"label": f.label, "value": f.value}
                  for i, f in enumerate(numeric_facts) if i not in covered]
                 if require_full_coverage else [])

    all_cited = CITE_RE.findall(narrative)
    return {
        "ok": not (unknown or uncited or unsupported or misattributed or uncovered),
        "unknown_citations": sorted(unknown),
        "uncited_numbers": uncited,
        "unsupported_numbers": unsupported,
        "misattributed_citations": misattributed,
        "uncovered_facts": uncovered,
        "cited_ids": sorted(set(all_cited)),
        "numbers_found": _numbers_in_text(narrative),
    }


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------

def _mock_backend(facts: list[Fact], audience: str, room_label: str) -> str:
    """Deterministic, template-based, fully offline. Walks the fact list in
    order and emits one sentence per fact that has a value, citing every
    source ID behind it. No model call, no network — this is what lets
    _self_check() run anywhere."""
    lines = [f"{room_label} — {audience} brief."]
    for f in facts:
        if f.value is None:
            if f.note:
                lines.append(f"{f.label}: not available ({f.note}).")
            continue
        cites = "".join(f"[cite:{tid}]" for tid in f.source_transaction_ids)
        note = f" ({f.note})" if f.note else ""
        lines.append(f"{f.label} is {_format_value(f.value)}{note}. {cites}")
    return " ".join(lines)


def _openai_backend(facts: list[Fact], audience: str, room_label: str) -> str:
    """Real backend. Not covered by _self_check() (no network in this
    sandbox) — test separately once there is connectivity. Requires
    OPENAI_API_KEY in the environment; never hardcode a key."""
    try:
        from openai import OpenAI  # local import: optional dependency
    except ImportError as exc:
        raise RuntimeError(
            "openai backend requires the 'openai' package (pip install openai)"
        ) from exc

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set — openai backend cannot run")

    fact_lines = "\n".join(
        f"- {f.label}: {f.value}"
        + (f" ({f.note})" if f.note else "")
        + f" [ids: {', '.join(f.source_transaction_ids) or 'none'}]"
        for f in facts
    )
    prompt = (
        f"You are writing a {audience} brief for {room_label}.\n"
        "You are given a fixed list of facts below, each with the exact source "
        "IDs that produced it. Rules:\n"
        "1. Use ONLY the numbers given below, written exactly as given (you may "
        "round, but never compute or guess a new one), and state every fact.\n"
        "2. After every number you state, add citation tokens in the form "
        "[cite:<id>] using ONLY the ids listed for that fact.\n"
        "3. Do not cite an id that is not listed for the fact you are discussing, "
        "and do not put any other number (dates, counts, labels) before a citation.\n"
        "4. If a fact's value is null, you may mention it is unavailable but "
        "must not invent a replacement number.\n\n"
        f"Facts:\n{fact_lines}\n\n"
        "Write the brief now."
    )

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=os.environ.get("NARRATE_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


BACKENDS: dict[str, Callable[[list[Fact], str, str], str]] = {
    "mock": _mock_backend,
    "openai": _openai_backend,
}


def narrate(
    facts: list[dict] | list[Fact],
    audience: str,
    room_label: str,
    backend: str = "mock",
) -> dict:
    """Entry point rooms call. Validates facts, calls the chosen backend,
    validates the output, and returns everything (never just the text) so
    a caller can decide what to do with a failed validation rather than
    having it hidden.

    Returns:
        {"narrative": str, "input_validation": {...}, "output_validation": {...},
         "backend": str, "ok": bool}
    """
    fact_objs = [f if isinstance(f, Fact) else Fact.from_dict(f) for f in facts]

    input_check = validate_facts(fact_objs)
    if not input_check["ok"]:
        # Fail loud: a fact with a value and no citation is exactly the bug
        # this whole design exists to prevent. Never silently proceed.
        raise ValueError(
            f"narrate(): {len(input_check['violations'])} fact(s) have a value "
            f"with no source_transaction_ids: {input_check['violations']}"
        )

    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend '{backend}'. Known: {sorted(BACKENDS)}")

    narrative_text = BACKENDS[backend](fact_objs, audience, room_label)
    output_check = validate_narrative(narrative_text, fact_objs)

    return {
        "narrative": narrative_text,
        "input_validation": input_check,
        "output_validation": output_check,
        "backend": backend,
        "ok": input_check["ok"] and output_check["ok"],
    }


def _self_check() -> None:
    checks = []

    good_facts = [
        Fact(label="Runway", value=4.2, source_transaction_ids=["txn_001", "txn_002"]),
        Fact(label="Customer concentration", value=0.39, source_transaction_ids=["txn_010"],
             note="elevated_concentration"),
        Fact(label="Infinite runway case", value=None, note="cash-flow positive over trailing window"),
    ]

    result = narrate(good_facts, audience="SMB underwriter", room_label="Cash Flow Room", backend="mock")
    checks.append(("mock backend returns a non-empty narrative", bool(result["narrative"]), result["narrative"]))
    checks.append(("narrate() result is ok (facts + output both valid)", result["ok"], result))
    checks.append(("output validation finds no unknown citations",
                    result["output_validation"]["unknown_citations"] == [], result["output_validation"]))
    checks.append(("output cites every id from the facts with values",
                    set(result["output_validation"]["cited_ids"]) == {"txn_001", "txn_002", "txn_010"},
                    result["output_validation"]["cited_ids"]))

    # validate_facts must catch a fact with a value and no citations.
    bad_facts = [Fact(label="Bad fact", value=100.0, source_transaction_ids=[])]
    try:
        narrate(bad_facts, audience="x", room_label="y", backend="mock")
        checks.append(("narrate() raises on an uncited non-null fact", False, None))
    except ValueError:
        checks.append(("narrate() raises on an uncited non-null fact", True, None))

    # validate_narrative must catch an invented citation (simulate a model
    # that cited an id outside the fact list).
    forged = "Runway is 4.20 [cite:txn_999]"
    out_check = validate_narrative(forged, good_facts)
    checks.append(("validate_narrative flags a citation to an unknown id",
                    not out_check["ok"] and "txn_999" in out_check["unknown_citations"], out_check))

    # The reviewer-x blocker cases: each MUST fail.
    two_facts = [
        Fact(label="Runway", value=4.2, source_transaction_ids=["txn_001", "txn_002"]),
        Fact(label="Concentration", value=39.0, source_transaction_ids=["txn_010"]),
    ]
    good = "Runway is 4.2 months [cite:txn_001][cite:txn_002]. Top customer is 39% of revenue [cite:txn_010]."
    checks.append(("a correct two-claim narrative passes", validate_narrative(good, two_facts)["ok"],
                   validate_narrative(good, two_facts)))
    rounded = "Runway is 4 months [cite:txn_001]. Top customer is 39.0% [cite:txn_010]."
    checks.append(("correct rounding to the token's precision passes", validate_narrative(rounded, two_facts)["ok"],
                   validate_narrative(rounded, two_facts)))
    cases = {
        "an invented number with a valid citation fails":
            "Runway is 9.7 months [cite:txn_001]. Top customer is 39% [cite:txn_010].",
        "an invented number with no citation fails":
            "Runway is 4.2 months [cite:txn_001]. Top customer is 39% [cite:txn_010]. Burn is 12,500.",
        "a real value cited to the wrong fact's source fails":
            "Runway is 4.2 months [cite:txn_010]. Top customer is 39% [cite:txn_001].",
        "an extra citation from another fact fails":
            "Runway is 4.2 months [cite:txn_001][cite:txn_010]. Top customer is 39% [cite:txn_010].",
        "an empty narrative fails": "",
        "a narrative that drops a fact fails": "Runway is 4.2 months [cite:txn_001].",
        "a wrong rounding fails": "Runway is 4 months [cite:txn_001]. Top customer is 40% [cite:txn_010].",
    }
    noted = two_facts + [Fact(label="Benford score", value=None, note="fewer than 10 transactions")]
    cases["a code note's number reused outside the note fails"] = (
        "Runway is 4.2 months [cite:txn_001]. Top customer is 39% [cite:txn_010]. Only 10 customers.")
    ok_note = ("Runway is 4.2 months [cite:txn_001]. Top customer is 39% [cite:txn_010]. "
               "Benford score: not available (fewer than 10 transactions).")
    checks.append(("a code-authored note quoted verbatim is not a claim",
                   validate_narrative(ok_note, noted)["ok"], validate_narrative(ok_note, noted)))
    for name, text in cases.items():
        res = validate_narrative(text, two_facts)
        checks.append((name, not res["ok"], res))
    ident = ("Revenue for biz_03 in FY2025 per 10-K 0000320193-25-000079, 3-month view, "
             "period ended 2025-09-27, is 4.2 [cite:txn_001]. Concentration 39 [cite:txn_010].")
    checks.append(("identifiers, dates and labels are not treated as numeric claims",
                   validate_narrative(ident, two_facts)["ok"], validate_narrative(ident, two_facts)))

    # Unknown backend name fails loud.
    try:
        narrate(good_facts, audience="x", room_label="y", backend="not_a_backend")
        checks.append(("unknown backend name raises ValueError", False, None))
    except ValueError:
        checks.append(("unknown backend name raises ValueError", True, None))

    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"   detail: {detail}")


if __name__ == "__main__":
    _self_check()
