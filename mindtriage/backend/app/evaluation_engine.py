from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional
import re

FOLLOWUP_BANK = [
    {"key": "followup_mood", "prompt": "Can you rate your mood right now (1-10)?", "category": "mood"},
    {"key": "followup_safety", "prompt": "Do you feel safe right now?", "category": "safety"},
    {"key": "followup_support", "prompt": "Is there someone you can reach out to today?", "category": "support"},
    {"key": "followup_sleep", "prompt": "How was your sleep last night?", "category": "sleep"},
    {"key": "followup_anxiety", "prompt": "How intense is your anxiety right now (1-10)?", "category": "anxiety"},
]


@dataclass
class QualityResult:
    score: float
    flags: List[str]
    is_suspected_fake: bool
    reason_summary: str


@dataclass
class EvaluationResult:
    risk_score: int
    risk_level: str
    signals: List[str]
    confidence: float
    quality: QualityResult
    recommended_followups: List[dict]


def evaluate(
    journal_text: Optional[str] = None,
    daily_answers: Optional[Dict[str, str]] = None,
    rapid_answers: Optional[Dict[str, str]] = None,
    duration_seconds: Optional[float] = None,
    followup_answers: Optional[Dict[str, str]] = None,
) -> EvaluationResult:
    signals: List[str] = []
    score = 0
    inputs = {}
    if daily_answers:
        inputs.update(daily_answers)
    if rapid_answers:
        inputs.update(rapid_answers)
    if followup_answers:
        inputs.update(followup_answers)

    mood_value = parse_numeric(inputs.get("daily_mood") or inputs.get("rapid_mood") or inputs.get("followup_mood", ""))
    anxiety_value = parse_numeric(inputs.get("daily_anxiety") or inputs.get("rapid_anxiety") or inputs.get("followup_anxiety", ""))

    if mood_value is not None and mood_value <= 3:
        score += 1
        signals.append("Low mood rating")
    if anxiety_value is not None and anxiety_value >= 8:
        score += 1
        signals.append("High anxiety rating")

    if is_yes(inputs.get("daily_hopeless", "")) or is_yes(inputs.get("rapid_hopeless", "")):
        score += 2
        signals.append("Reported hopelessness")
    if is_yes(inputs.get("daily_isolation", "")) or is_yes(inputs.get("rapid_isolation", "")):
        score += 1
        signals.append("Reported isolation")

    if journal_text and contains_risk_keywords(journal_text):
        score += 3
        signals.append("Risk keywords in journal")

    score = min(20, score)
    if score >= 4:
        risk_level = "high"
    elif score >= 2:
        risk_level = "medium"
    else:
        risk_level = "low"

    quality = assess_quality(journal_text or "", inputs, duration_seconds)
    confidence = max(0.05, min(0.95, 0.7 * quality.score + 0.3))
    if quality.is_suspected_fake:
        confidence = min(confidence, 0.4)

    followups = []
    if confidence < 0.55 or quality.is_suspected_fake:
        followups = select_followups(inputs)

    return EvaluationResult(
        risk_score=score,
        risk_level=risk_level,
        signals=list(dict.fromkeys(signals)),
        confidence=confidence,
        quality=quality,
        recommended_followups=followups,
    )


def assess_quality(text: str, answers: Dict[str, str], duration_seconds: Optional[float]) -> QualityResult:
    flags: List[str] = []
    combined = " ".join([text] + list(answers.values())).strip()
    tokens = re.findall(r"\b\w+\b", combined.lower())

    if len(combined) < 20:
        flags.append("too_short")
    if len(tokens) < 4:
        flags.append("low_word_count")
    if tokens:
        unique_ratio = len(set(tokens)) / len(tokens)
        if unique_ratio < 0.5:
            flags.append("repeated_tokens")
    if re.search(r"(.)\1{4,}", combined.lower()):
        flags.append("repeated_characters")

    symbol_ratio = symbol_char_ratio(combined)
    if symbol_ratio > 0.35:
        flags.append("gibberish_symbols")

    if duration_seconds is not None and duration_seconds < 15:
        flags.append("too_fast")

    if contradiction_detected(answers):
        flags.append("contradiction")

    score = 1.0
    penalties = {
        "too_short": 0.15,
        "low_word_count": 0.15,
        "repeated_tokens": 0.1,
        "repeated_characters": 0.1,
        "gibberish_symbols": 0.2,
        "too_fast": 0.2,
        "contradiction": 0.15,
    }
    for flag in flags:
        score -= penalties.get(flag, 0)
    score = max(0.0, min(1.0, score))

    is_suspected_fake = score < 0.4 or "gibberish_symbols" in flags
    reason_summary = summarize_flags(flags)
    return QualityResult(score=score, flags=flags, is_suspected_fake=is_suspected_fake, reason_summary=reason_summary)


def select_followups(inputs: Dict[str, str]) -> List[dict]:
    prompts = []
    for item in FOLLOWUP_BANK:
        if item["key"] == "followup_mood" and not inputs.get("daily_mood") and not inputs.get("rapid_mood"):
            prompts.append(item)
        if item["key"] == "followup_anxiety" and not inputs.get("daily_anxiety") and not inputs.get("rapid_anxiety"):
            prompts.append(item)
    for item in FOLLOWUP_BANK:
        if item not in prompts:
            prompts.append(item)
    return prompts[:2]


def is_yes(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in {"yes", "y", "true", "1"}


def parse_numeric(value: str) -> Optional[int]:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def contains_risk_keywords(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in [
        "suicide",
        "kill myself",
        "end it",
        "end my life",
        "self-harm",
        "self harm",
        "can't go on",
    ])


def symbol_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    symbols = sum(1 for ch in text if not ch.isalnum() and not ch.isspace())
    return symbols / max(1, len(text))


def contradiction_detected(answers: Dict[str, str]) -> bool:
    mood_value = parse_numeric(answers.get("daily_mood") or answers.get("rapid_mood") or "")
    hopeless = is_yes(answers.get("daily_hopeless", "")) or is_yes(answers.get("rapid_hopeless", ""))
    if mood_value is not None and mood_value >= 8 and hopeless:
        return True
    return False


def summarize_flags(flags: List[str]) -> str:
    mapping = {
        "too_short": "Too short",
        "low_word_count": "Not enough detail",
        "repeated_tokens": "Repetitive wording",
        "repeated_characters": "Repeated characters",
        "gibberish_symbols": "High symbol ratio",
        "too_fast": "Completed very quickly",
        "contradiction": "Answers conflict",
    }
    if not flags:
        return "Looks consistent."
    return "; ".join(mapping.get(flag, flag) for flag in flags[:3])
