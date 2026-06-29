from __future__ import annotations

import html
import statistics
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from .baseline_engine import SIGNAL_KEYS, collect_signals_for_window, compute_signal_stats

DISCLAIMER = (
    "These are pattern-based signals to support clinical assessment, not a diagnosis. "
    "If you feel unsafe, contact local emergency services."
)

# Higher value = more concerning, except where noted.
CONCERN_THRESHOLDS = {
    "mood_score": lambda v: v <= 3.0,
    "anxiety_score": lambda v: v >= 7.0,
    "hopelessness_score": lambda v: v >= 6.0,
    "energy_score": lambda v: v <= 3.0,
    "social_score": lambda v: v >= 6.0,
    "sleep_hours": lambda v: v < 6.0,
}

CLUSTERS = [
    {
        "key": "mood",
        "label": "Mood / Depressive signals",
        "signal_keys": ["mood_score", "hopelessness_score"],
        "eval_signals": ["Low mood rating", "Reported hopelessness"],
    },
    {
        "key": "anxiety",
        "label": "Anxiety signals",
        "signal_keys": ["anxiety_score"],
        "eval_signals": ["High anxiety rating"],
    },
    {
        "key": "sleep",
        "label": "Sleep disruption",
        "signal_keys": ["sleep_hours"],
        "eval_signals": [],
    },
    {
        "key": "energy",
        "label": "Energy / Fatigue signals",
        "signal_keys": ["energy_score"],
        "eval_signals": [],
    },
    {
        "key": "social",
        "label": "Social withdrawal signals",
        "signal_keys": ["social_score"],
        "eval_signals": ["Reported isolation"],
    },
]

STATUS_DESCRIPTIONS = {
    "frequently_elevated": "Pattern signals were frequently elevated in this period.",
    "occasionally_elevated": "Pattern signals were occasionally elevated in this period.",
    "stable": "Pattern signals were within the stable range in this period.",
    "insufficient_data": "Not enough data was logged in this period to assess this pattern.",
}


def is_concerning(signal_key: str, value: float) -> bool:
    check = CONCERN_THRESHOLDS.get(signal_key)
    if check is None:
        return False
    return bool(check(value))


def compute_signal_concern_rate(signal_key: str, daily_values: List[float]) -> Optional[float]:
    if not daily_values:
        return None
    hits = sum(1 for value in daily_values if is_concerning(signal_key, value))
    return hits / len(daily_values)


def compute_eval_hit_rate(eval_signal_names: List[str], all_signals_lists: List[List[str]]) -> Optional[float]:
    if not eval_signal_names or not all_signals_lists:
        return None
    hits = sum(1 for signals in all_signals_lists if any(name in signals for name in eval_signal_names))
    return hits / len(all_signals_lists)


def classify_cluster_status(rates: List[float]) -> str:
    if not rates:
        return "insufficient_data"
    combined = statistics.mean(rates)
    if combined >= 0.5:
        return "frequently_elevated"
    if combined >= 0.15:
        return "occasionally_elevated"
    return "stable"


def build_cluster_summary(
    cluster_def: dict,
    signals_by_date: Dict[date, Dict[str, float]],
    eval_signals_lists: List[List[str]],
) -> dict:
    rates: List[float] = []
    signal_stats: Dict[str, dict] = {}

    for signal_key in cluster_def["signal_keys"]:
        daily_values = [
            day_signals[signal_key]
            for day_signals in signals_by_date.values()
            if signal_key in day_signals
        ]
        signal_stats[signal_key] = compute_signal_stats(daily_values, total_days=max(len(signals_by_date), 1))
        rate = compute_signal_concern_rate(signal_key, daily_values)
        if rate is not None:
            rates.append(rate)

    eval_hit_rate = compute_eval_hit_rate(cluster_def["eval_signals"], eval_signals_lists)
    if eval_hit_rate is not None:
        rates.append(eval_hit_rate)

    status = classify_cluster_status(rates)
    return {
        "key": cluster_def["key"],
        "label": cluster_def["label"],
        "status": status,
        "description": STATUS_DESCRIPTIONS[status],
        "supporting_stats": {
            "signal_stats": signal_stats,
            "eval_signal_hit_rate": eval_hit_rate,
        },
    }


def build_clinician_summary(user_id: int, db, days: int, end_date: Optional[date] = None) -> dict:
    from .main import Answer, CrisisEvent, JournalEntry, RapidEvaluation
    import json

    end_date = end_date or date.today()
    start_date = end_date - timedelta(days=days - 1)

    signals_by_date = collect_signals_for_window(user_id, start_date, end_date, include_low_quality=False, db=db)

    rapid_rows = (
        db.query(RapidEvaluation)
        .filter(
            RapidEvaluation.user_id == user_id,
            RapidEvaluation.entry_date >= start_date,
            RapidEvaluation.entry_date <= end_date,
        )
        .all()
    )
    eval_signals_lists: List[List[str]] = []
    level_counts = {"RED": 0, "YELLOW": 0, "GREEN": 0}
    for row in rapid_rows:
        try:
            eval_signals_lists.append(json.loads(row.signals_json or "[]"))
        except ValueError:
            eval_signals_lists.append([])
        level_counts[str(row.level or "").upper()] = level_counts.get(str(row.level or "").upper(), 0) + 1

    clusters = [
        build_cluster_summary(cluster_def, signals_by_date, eval_signals_lists)
        for cluster_def in CLUSTERS
    ]

    crisis_rows = (
        db.query(CrisisEvent)
        .filter(
            CrisisEvent.user_id == user_id,
            CrisisEvent.entry_date >= start_date,
            CrisisEvent.entry_date <= end_date,
        )
        .order_by(CrisisEvent.entry_date.asc())
        .all()
    )
    safety_summary = {
        "crisis_event_count": len(crisis_rows),
        "crisis_events": [
            {"date": row.entry_date.isoformat(), "level": row.level, "source": row.source}
            for row in crisis_rows
        ],
        "rapid_level_counts": level_counts,
    }

    journal_count = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.entry_date >= start_date,
            JournalEntry.entry_date <= end_date,
        )
        .count()
    )
    low_quality_journal_count = (
        db.query(JournalEntry)
        .filter(
            JournalEntry.user_id == user_id,
            JournalEntry.entry_date >= start_date,
            JournalEntry.entry_date <= end_date,
            JournalEntry.is_low_quality.is_(True),
        )
        .count()
    )
    daily_checkin_days = len(
        {
            row.entry_date
            for row in db.query(Answer)
            .filter(
                Answer.user_id == user_id,
                Answer.entry_date >= start_date,
                Answer.entry_date <= end_date,
                Answer.kind == "daily",
            )
            .all()
            if row.entry_date
        }
    )
    data_completeness = {
        "window_days": days,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "journal_entries_logged": journal_count,
        "low_quality_journal_entries": low_quality_journal_count,
        "daily_checkin_days_logged": daily_checkin_days,
        "daily_checkin_coverage_percent": round((daily_checkin_days / days) * 100, 2) if days else 0.0,
        "rapid_evaluations_logged": len(rapid_rows),
    }

    return {
        "meta": {
            "generated_at": datetime.utcnow().isoformat(),
            "window_days": days,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "data_completeness": data_completeness,
        "clusters": clusters,
        "safety_summary": safety_summary,
        "disclaimer": DISCLAIMER,
    }


def render_clinician_summary_html(summary: dict) -> str:
    meta = summary["meta"]
    completeness = summary["data_completeness"]
    safety = summary["safety_summary"]

    cluster_rows = "\n".join(
        f"<tr><td>{html.escape(cluster['label'])}</td>"
        f"<td>{html.escape(cluster['status'].replace('_', ' ').title())}</td>"
        f"<td>{html.escape(cluster['description'])}</td></tr>"
        for cluster in summary["clusters"]
    )

    crisis_rows = "\n".join(
        f"<tr><td>{html.escape(event['date'])}</td><td>{html.escape(event['level'])}</td>"
        f"<td>{html.escape(event['source'])}</td></tr>"
        for event in safety["crisis_events"]
    ) or "<tr><td colspan=\"3\">No crisis events recorded in this period.</td></tr>"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>MindTriage Clinical Summary</title>
<style>
body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 2rem auto; color: #1a1a1a; }}
.disclaimer {{ background: #fff3cd; border: 1px solid #d4a017; padding: 0.75rem; margin: 1rem 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.5rem; text-align: left; }}
</style>
</head>
<body>
<h1>MindTriage Clinical Summary</h1>
<p>Generated: {html.escape(meta['generated_at'])} | Range: {html.escape(meta['start_date'])} to {html.escape(meta['end_date'])}</p>
<div class="disclaimer">{html.escape(summary['disclaimer'])}</div>

<h2>Data completeness</h2>
<ul>
<li>Daily check-ins logged: {completeness['daily_checkin_days_logged']} / {completeness['window_days']} days ({completeness['daily_checkin_coverage_percent']}%)</li>
<li>Journal entries logged: {completeness['journal_entries_logged']} ({completeness['low_quality_journal_entries']} flagged low-quality and excluded from trends)</li>
<li>Rapid evaluations logged: {completeness['rapid_evaluations_logged']}</li>
</ul>

<h2>Pattern signal clusters</h2>
<table>
<tr><th>Cluster</th><th>Status</th><th>Description</th></tr>
{cluster_rows}
</table>

<h2>Safety / crisis log</h2>
<p>Crisis events recorded: {safety['crisis_event_count']}</p>
<table>
<tr><th>Date</th><th>Level</th><th>Source</th></tr>
{crisis_rows}
</table>
<p>Rapid evaluation levels in range: RED={safety['rapid_level_counts'].get('RED', 0)}, YELLOW={safety['rapid_level_counts'].get('YELLOW', 0)}, GREEN={safety['rapid_level_counts'].get('GREEN', 0)}</p>

<div class="disclaimer">{html.escape(summary['disclaimer'])}</div>
</body>
</html>"""
