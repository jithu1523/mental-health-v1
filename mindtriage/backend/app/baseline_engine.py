from __future__ import annotations

import json
import re
import statistics
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

SIGNAL_KEYS = [
    "mood_score",
    "anxiety_score",
    "sleep_hours",
    "energy_score",
    "social_score",
    "hopelessness_score",
]


def parse_first_number(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"(-?\d+(\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_scale(value: float, source_min: float, source_max: float, target_max: float = 10.0) -> float:
    if source_max == source_min:
        return clamp(value, 0.0, target_max)
    ratio = (value - source_min) / (source_max - source_min)
    return clamp(ratio * target_max, 0.0, target_max)


def normalize_yes_no(text: str) -> Optional[bool]:
    lowered = text.strip().lower()
    if lowered in {"yes", "y", "true", "1"}:
        return True
    if lowered in {"no", "n", "false", "0"}:
        return False
    return None


def normalize_social_value(category: str, raw: str) -> Optional[float]:
    lowered = raw.strip().lower()
    if category == "isolation":
        flag = normalize_yes_no(raw)
        if flag is None:
            return None
        return 10.0 if flag else 0.0
    if category == "support":
        flag = normalize_yes_no(raw)
        if flag is None:
            return None
        return 0.0 if flag else 10.0
    if category == "connection":
        if lowered == "connected":
            return 0.0
        if lowered == "neutral":
            return 5.0
        if lowered == "isolated":
            return 10.0
    return None


def normalize_daily_answer(category: str, answer_text: str) -> Optional[Tuple[str, float]]:
    if not category:
        return None
    numeric = parse_first_number(answer_text)
    if category in {"mood", "anxiety", "energy"}:
        if numeric is None:
            return None
        value = normalize_scale(numeric, 1.0, 10.0, 10.0)
        return f"{category}_score", value
    if category == "sleep":
        if numeric is None:
            return None
        return "sleep_hours", clamp(numeric, 0.0, 12.0)
    if category == "hopelessness":
        if numeric is not None:
            value = normalize_scale(numeric, 1.0, 10.0, 10.0)
            return "hopelessness_score", value
        flag = normalize_yes_no(answer_text)
        if flag is None:
            return None
        return "hopelessness_score", 10.0 if flag else 0.0
    if category in {"isolation", "support", "connection"}:
        social_value = normalize_social_value(category, answer_text)
        if social_value is None:
            return None
        return "social_score", social_value
    return None


def normalize_micro_answer(category: str, value_raw: str) -> Optional[Tuple[str, float]]:
    if not category:
        return None
    numeric = parse_first_number(value_raw)
    if category in {"mood", "anxiety", "energy", "hopelessness"}:
        if numeric is None:
            return None
        value = normalize_scale(numeric, 1.0, 5.0, 10.0) if numeric <= 5 else normalize_scale(numeric, 1.0, 10.0, 10.0)
        signal = "hopelessness_score" if category == "hopelessness" else f"{category}_score"
        return signal, value
    if category in {"isolation", "support", "connection"}:
        social_value = normalize_social_value(category, value_raw)
        if social_value is None:
            return None
        return "social_score", social_value
    return None


def compute_signal_stats(values: List[float], total_days: int) -> dict:
    sample_days = len(values)
    coverage = round((sample_days / total_days) * 100, 2) if total_days else 0.0
    stats = {
        "mean": None,
        "median": None,
        "std": None,
        "coverage_percent": coverage,
        "samples": sample_days,
    }
    if sample_days >= 7 and coverage >= 70.0:
        stats["mean"] = round(statistics.mean(values), 2)
        stats["median"] = round(statistics.median(values), 2)
        stats["std"] = round(statistics.pstdev(values), 2) if sample_days >= 2 else 0.0
    return stats


def compute_confidence(baseline_signal_stats: Dict[str, dict], signals_today: Dict[str, float]) -> float:
    if not baseline_signal_stats:
        return 0.2
    baseline_coverages = [
        stat["coverage_percent"] / 100.0
        for stat in baseline_signal_stats.values()
        if stat and stat.get("coverage_percent") is not None
    ]
    baseline_coverage = statistics.mean(baseline_coverages) if baseline_coverages else 0.0
    today_coverage = len(signals_today) / max(len(SIGNAL_KEYS), 1)
    confidence = 0.5 * baseline_coverage + 0.5 * today_coverage
    return round(clamp(confidence, 0.05, 0.95), 2)


def classify_drift(delta: Optional[float], z_score: Optional[float]) -> str:
    if delta is None:
        return "missing"
    if z_score is not None:
        if z_score <= -1:
            return "down"
        if z_score >= 1:
            return "up"
    if delta <= -1.0:
        return "down"
    if delta >= 1.0:
        return "up"
    return "stable"


def build_drift_message(signal_key: str, status: str) -> Optional[str]:
    if status not in {"up", "down"}:
        return None
    if signal_key == "sleep_hours":
        return "Sleep is lower than your 2-week baseline." if status == "down" else "Sleep is higher than your 2-week baseline."
    if signal_key == "anxiety_score":
        return "Anxiety is higher than usual." if status == "up" else "Anxiety is lower than usual."
    if signal_key == "mood_score":
        return "Mood is lower than your baseline." if status == "down" else "Mood is higher than your baseline."
    if signal_key == "energy_score":
        return "Energy is lower than your baseline." if status == "down" else "Energy is higher than your baseline."
    if signal_key == "social_score":
        return "Isolation signals are higher than usual." if status == "up" else "Isolation signals are lower than usual."
    if signal_key == "hopelessness_score":
        return "Hopelessness signals are higher than usual." if status == "up" else "Hopelessness signals are lower than usual."
    return None


def build_recommendations(drift: Dict[str, dict]) -> List[str]:
    messages: List[str] = []
    down_signals = 0
    for key, info in drift.items():
        status = info.get("status")
        if status == "down":
            down_signals += 1
        message = build_drift_message(key, status)
        if message:
            messages.append(message)
    if down_signals >= 2:
        messages.append("Several signals are lower than your baseline. Try a 5-minute grounding or breathing exercise.")
    if not messages:
        messages.append("Signals look stable compared to your baseline. Keep using the check-in to track changes.")
    return messages[:5]


def compute_drift(signals_today: Dict[str, float], baseline_signals: Dict[str, dict]) -> Tuple[Dict[str, dict], List[dict], float, List[str]]:
    drift: Dict[str, dict] = {}
    for key in SIGNAL_KEYS:
        baseline = baseline_signals.get(key, {})
        mean = baseline.get("mean")
        std = baseline.get("std")
        today_value = signals_today.get(key)
        delta = None
        z_score = None
        if today_value is not None and mean is not None:
            delta = round(today_value - mean, 2)
            if std and std > 0:
                z_score = round(delta / std, 2)
        status = classify_drift(delta, z_score)
        drift[key] = {
            "delta": delta,
            "z": z_score,
            "status": status,
        }

    top_changes = []
    for key, info in drift.items():
        if info["delta"] is None:
            continue
        message = build_drift_message(key, info["status"])
        top_changes.append({
            "signal": key,
            "delta": info["delta"],
            "message": message,
        })
    top_changes.sort(key=lambda item: abs(item["delta"]), reverse=True)

    confidence = compute_confidence(baseline_signals, signals_today)
    recommendations = build_recommendations(drift)
    return drift, top_changes[:3], confidence, recommendations


def collect_signals_for_window(
    user_id: int,
    start_date: date,
    end_date: date,
    include_low_quality: bool,
    db,
) -> Dict[date, Dict[str, float]]:
    from .main import Answer, MicroAnswer, MicroQuestion, build_daily_category_map

    signals_by_date: Dict[date, Dict[str, float]] = {}
    daily_category_map = build_daily_category_map(db)
    daily_query = (
        db.query(Answer)
        .filter(
            Answer.user_id == user_id,
            Answer.entry_date >= start_date,
            Answer.entry_date <= end_date,
            Answer.kind == "daily",
        )
    )
    if not include_low_quality:
        daily_query = daily_query.filter(Answer.is_low_quality.is_(False))
    for answer in daily_query.all():
        category = answer.category or daily_category_map.get(answer.question_id)
        result = normalize_daily_answer(category or "", answer.answer_text)
        if not result or not answer.entry_date:
            continue
        signal_key, value = result
        signals_by_date.setdefault(answer.entry_date, {})[signal_key] = value

    micro_query = (
        db.query(MicroAnswer, MicroQuestion)
        .join(MicroQuestion, MicroAnswer.question_id == MicroQuestion.id)
        .filter(
            MicroAnswer.user_id == user_id,
            MicroAnswer.entry_date >= start_date,
            MicroAnswer.entry_date <= end_date,
        )
    )
    if not include_low_quality:
        micro_query = micro_query.filter(MicroAnswer.is_low_quality.is_(False))
    for answer, question in micro_query.all():
        category = answer.category or question.category
        value_raw = json.loads(answer.value_json).get("value", "")
        result = normalize_micro_answer(category or "", str(value_raw))
        if not result or not answer.entry_date:
            continue
        signal_key, value = result
        signals_by_date.setdefault(answer.entry_date, {})[signal_key] = value

    return signals_by_date


def compute_baseline_snapshot(
    user_id: int,
    window_days: int,
    include_low_quality: bool,
    end_date: date,
    db,
) -> dict:
    start_date = end_date - timedelta(days=window_days - 1)
    signals_by_date = collect_signals_for_window(user_id, start_date, end_date, include_low_quality, db)
    signal_values: Dict[str, List[float]] = {key: [] for key in SIGNAL_KEYS}

    for day, signal_map in signals_by_date.items():
        for key in SIGNAL_KEYS:
            if key in signal_map:
                signal_values[key].append(signal_map[key])

    baseline_signals = {
        key: compute_signal_stats(values, window_days)
        for key, values in signal_values.items()
    }

    payload = {
        "window_days": window_days,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "signals": baseline_signals,
        "total_days": window_days,
    }
    return payload

