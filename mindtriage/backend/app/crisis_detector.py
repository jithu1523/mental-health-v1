from __future__ import annotations

import re
from typing import Dict, List, Optional

HIGH_PATTERNS = [
    r"\bkill myself\b",
    r"\bi will kill myself\b",
    r"\bi am going to kill myself\b",
    r"\bcommit suicide\b",
    r"\bsuicide\b",
    r"\bsuicidal\b",
    r"\bend my life\b",
    r"\bend it all\b",
    r"\bwant to die\b",
    r"\bplan to (die|kill myself|end my life|end it)\b",
]

ELEVATED_TERMS = [
    "self-harm",
    "self harm",
    "hurt myself",
    "cut myself",
    "overdose",
    "no reason to live",
    "can't go on",
    "cant go on",
    "no way out",
    "better off dead",
    "end it",
    "hopeless",
]


def _find_matches(text: str, patterns: List[str]) -> List[str]:
    matches: List[str] = []
    for pattern in patterns:
        if re.search(pattern, text):
            matches.append(pattern.strip(r"\b"))
    return matches


def detect_crisis(
    texts: Optional[List[str]] = None,
    structured: Optional[Dict[str, object]] = None,
) -> dict:
    texts = texts or []
    structured = structured or {}
    combined = " ".join(texts).lower()

    high_matches = _find_matches(combined, HIGH_PATTERNS)
    if structured.get("self_harm_plan") or structured.get("self_harm_intent"):
        high_matches.append("self_harm_plan")
    if high_matches:
        return {
            "is_crisis": True,
            "level": "high",
            "matched_terms": list(dict.fromkeys(high_matches)),
            "reason": "Explicit self-harm intent or plan detected.",
        }

    elevated_matches = [term for term in ELEVATED_TERMS if term in combined]
    hopelessness_score = structured.get("hopelessness_score")
    high_risk_score = structured.get("risk_score")
    hopeless_flag = isinstance(hopelessness_score, (int, float)) and hopelessness_score >= 8
    self_harm_hint = structured.get("self_harm_thoughts") is True or any(
        term in combined for term in ["self-harm", "self harm", "hurt myself", "cut myself"]
    )
    alarming_text = any(term in combined for term in ["can't go on", "no way out", "better off dead"])
    high_risk = isinstance(high_risk_score, (int, float)) and high_risk_score >= 18

    if (hopeless_flag and self_harm_hint) or (high_risk and alarming_text):
        return {
            "is_crisis": True,
            "level": "elevated",
            "matched_terms": list(dict.fromkeys(elevated_matches)),
            "reason": "Elevated risk signals with distress language.",
        }

    return {
        "is_crisis": False,
        "level": "none",
        "matched_terms": list(dict.fromkeys(elevated_matches)),
        "reason": "",
    }
