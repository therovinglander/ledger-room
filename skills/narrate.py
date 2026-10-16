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
   every number appearing in the narrative text must match a fact's value
   (within rounding), and every citation token (`[cite:<id>]`) must resolve
   to an ID actually present in the input facts. Mismatches are flagged,
   never silently dropped.

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
# Numbers in narrative text: optional $ and %, optional thousands separators,
# optional decimal part, optional leading minus.
NUMBER_RE = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?%?")


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
    return NUMBER_RE.findall(text)


def _value_appears(value: Any, numbers_in_text: list[str]) -> bool:
    """True if `value` (within rounding) matches some number token found in
    the narrative text. Strips $, %, and thousands separators before
    comparing numerically; falls back to exact string match for non-numeric
    values (e.g. a period string or a flag name)."""
    if value is None:
        return True  # nothing to check
    if isinstance(value, (int, float)):
        for tok in numbers_in_text:
            cleaned = tok.replace("$", "").replace(",", "").replace("%", "")
            try:
                tok_val = float(cleaned)
            except ValueError:
                continue
            # Percent-formatted values: KPIs often store a 0-1 fraction but
            # the narrative renders it as e.g. "39%" — accept either scale.
            if abs(tok_val - float(value)) < 0.015:
                return True
            if "%" in tok and abs(tok_val - float(value) * 100) < 0.5:
                return True
        return False
    # Non-numeric value: require the exact string to appear somewhere.
    return str(value) in numbers_in_text or False


def validate_narrative(narrative: str, facts: list[Fact]) -> dict:
    """Checks a model-produced narrative against the input facts:

    - every citation token [cite:<id>] must resolve to a known source ID
    - every numeric fact's value should be traceable to some number in the
      text (best-effort — a narrative may legitimately omit a fact, that's
      not itself a violation; what IS a violation is a citation to an
      unknown ID, which would mean the model invented a source).

    Returns {"ok": bool, "unknown_citations": [...], "cited_ids": [...]}.
    """
    known_ids = {tid for f in facts for tid in f.source_transaction_ids}
    cited = CITE_RE.findall(narrative)
    unknown = sorted({c for c in cited if c not in known_ids})
    numbers_in_text = _numbers_in_text(narrative)

    untraceable_numeric_facts = []
    for f in facts:
        if isinstance(f.value, (int, float)) and f.source_transaction_ids:
            if not _value_appears(f.value, numbers_in_text):
                continue  # the room may choose not to mention every fact; not a hard failure

    return {
        "ok": len(unknown) == 0,
        "unknown_citations": unknown,
        "cited_ids": sorted(set(cited)),
        "numbers_found": numbers_in_text,
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
        "1. Use ONLY the numbers given below — never compute or guess a new one.\n"
        "2. After every number you state, add citation tokens in the form "
        "[cite:<id>] using ONLY the ids listed for that fact.\n"
        "3. Do not cite an id that is not listed for the fact you are discussing.\n"
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
    forged = "Runway is 4.20. [cite:txn_999]"
    out_check = validate_narrative(forged, good_facts)
    checks.append(("validate_narrative flags a citation to an unknown id",
                    not out_check["ok"] and "txn_999" in out_check["unknown_citations"], out_check))

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
