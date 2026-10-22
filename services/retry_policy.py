"""Sequential outbound retry rounds with waiting time and Turkish work calendar.

A scenario model, not a Genesys Engage dialer emulation. All calls in a round
finish before the next round starts; this is deliberately conservative.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

from services.retry_capacity import analyze_retry_capacity
from services.schedule_planner import estimate_finish_datetime, load_holiday_calendar

TR = ZoneInfo("Europe/Istanbul")
START = time(9, 0)
END = time(18, 0)


def _next_allowed_start(moment: datetime) -> datetime:
    """Move a timestamp to the next permitted working instant."""
    current = moment.astimezone(TR)
    for _ in range(3660):  # guard against accidental endless loops
        day = current.date()
        calendar = load_holiday_calendar(day.year)
        key = day.isoformat()
        if current.weekday() < 5 and key not in calendar["full_days"]:
            cutoff = calendar["half_days"].get(key)
            end = time.fromisoformat(cutoff) if cutoff else END
            opening = datetime.combine(day, START, tzinfo=TR)
            closing = datetime.combine(day, end, tzinfo=TR)
            if current < opening:
                return opening
            if current < closing:
                return current
        current = datetime.combine(day + timedelta(days=1), START, tzinfo=TR)
    raise RuntimeError("Uygun calisma araligi bulunamadi")


def simulate_retry_policy(
    *, records: int, ports: int, answer_rate: float,
    avg_ivr_seconds: float, avg_no_answer_seconds: float,
    start_datetime: datetime, overhead_seconds: float = 2.0,
    max_attempts: int = 3, retry_fraction: float = 1.0,
    retry_wait_hours: float = 24.0,
) -> dict:
    """Return per-round schedules and baseline comparison.

    Retry wait is elapsed (wall-clock) hours after the *previous round ends*.
    A later round may only start inside weekday 09:00-18:00, excluding Turkish
    public holidays and respecting 13:00 half-day cutoffs. No interleaving,
    predictive dialing, throttling or record-specific eligibility is modeled.
    """
    if not isinstance(start_datetime, datetime) or start_datetime.tzinfo is None:
        raise ValueError("start_datetime saat dilimi icermeli")
    if isinstance(retry_wait_hours, bool) or not isinstance(retry_wait_hours, (int, float)):
        raise ValueError("retry_wait_hours sayi olmali")
    if not isfinite(retry_wait_hours) or retry_wait_hours < 0:
        raise ValueError("retry_wait_hours negatif veya sonsuz olamaz")

    analysis = analyze_retry_capacity(
        records=records, ports=ports, answer_rate=answer_rate,
        avg_ivr_seconds=avg_ivr_seconds,
        avg_no_answer_seconds=avg_no_answer_seconds,
        overhead_seconds=overhead_seconds,
        max_attempts=max_attempts, retry_fraction=retry_fraction,
    )
    start = _next_allowed_start(start_datetime)
    if records == 0:
        return {
            "analysis": analysis,
            "start": start.isoformat(),
            "baseline_finish": start.isoformat(),
            "retry_finish": start.isoformat(),
            "calendar_delay_hours": 0.0,
            "retry_wait_hours": float(retry_wait_hours),
            "rounds": [],
            "assumptions": "Sirali turlar; bekleme onceki turun bitisinden itibaren gercek saat olarak hesaplanir.",
        }

    probability = analysis["answer_probability"]
    avg_seconds = (
        probability * avg_ivr_seconds
        + (1 - probability) * avg_no_answer_seconds
        + overhead_seconds
    )

    baseline = estimate_finish_datetime(
        round(analysis["baseline_completion_hours"], 2), start
    )["estimated_finish"]

    rounds = []
    current_start = start
    # Use unrounded duration to avoid accumulated rounding across rounds.
    attempts = float(records)
    for number in range(1, max_attempts + 1):
        if attempts <= 0:
            break
        hours = attempts * avg_seconds / ports / 3600
        if number == 1:
            hours = round(analysis["baseline_completion_hours"], 2)
        finish = datetime.fromisoformat(
            estimate_finish_datetime(hours, current_start)["estimated_finish"]
        )
        rounds.append({
            "attempt_number": number,
            "expected_attempts": round(attempts, 4),
            "expected_answers": round(attempts * probability, 4),
            "active_hours": round(hours, 4),
            "start": current_start.isoformat(),
            "finish": finish.isoformat(),
        })
        attempts = attempts * (1 - probability) * retry_fraction
        if number < max_attempts and attempts > 0:
            current_start = _next_allowed_start(
                finish + timedelta(hours=retry_wait_hours)
            )

    retry_finish = rounds[-1]["finish"]
    delta = datetime.fromisoformat(retry_finish) - datetime.fromisoformat(baseline)
    return {
        "analysis": analysis,
        "start": start.isoformat(),
        "baseline_finish": baseline,
        "retry_finish": retry_finish,
        "calendar_delay_hours": round(delta.total_seconds() / 3600, 4),
        "retry_wait_hours": float(retry_wait_hours),
        "rounds": rounds,
        "assumptions": (
            "Sirali turlar, sabit cevaplanma orani ve beklenen deneme adedi. "
            "Bekleme suresi onceki turun bitisinden sonra takvim saatidir; "
            "Genesys Engage pacing, DNC, retry codes ve kayit bazli kurallar dahil degildir."
        ),
    }
