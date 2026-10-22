"""Expected outbound retry workload and capacity analysis.

Assumptions: independent, constant answer probability per attempt; only
unanswered calls may be retried; retry_fraction applies at each retry stage.
All figures are expected values, not guaranteed integer call counts.
"""

from __future__ import annotations

from math import isfinite


def analyze_retry_capacity(
    records: int,
    ports: int,
    answer_rate: float,
    avg_ivr_seconds: float,
    avg_no_answer_seconds: float,
    overhead_seconds: float = 2.0,
    max_attempts: int = 3,
    retry_fraction: float = 1.0,
) -> dict:
    """Return baseline vs retry-adjusted campaign workload.

    ``answer_rate`` accepts percentages (40) or proportions (0.40).
    ``retry_fraction`` is a proportion between 0 and 1.
    Completion hours are active port-hours, not business-calendar elapsed time.
    """
    if isinstance(records, bool) or not isinstance(records, int) or records < 0:
        raise ValueError("records sifir veya pozitif tam sayi olmali")
    if isinstance(ports, bool) or not isinstance(ports, int) or ports <= 0:
        raise ValueError("ports pozitif tam sayi olmali")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts en az 1 olmali")

    for name, value in {
        "answer_rate": answer_rate,
        "avg_ivr_seconds": avg_ivr_seconds,
        "avg_no_answer_seconds": avg_no_answer_seconds,
        "overhead_seconds": overhead_seconds,
        "retry_fraction": retry_fraction,
    }.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"{name} sonlu bir sayi olmali")

    if not 0 <= answer_rate <= 100:
        raise ValueError("answer_rate 0-100 araliginda olmali")
    if not 0 <= retry_fraction <= 1:
        raise ValueError("retry_fraction 0-1 araliginda olmali")
    if min(avg_ivr_seconds, avg_no_answer_seconds, overhead_seconds) < 0:
        raise ValueError("sureler negatif olamaz")

    probability = answer_rate / 100 if answer_rate > 1 else float(answer_rate)
    avg_attempt_seconds = (
        probability * avg_ivr_seconds
        + (1 - probability) * avg_no_answer_seconds
        + overhead_seconds
    )
    if avg_attempt_seconds <= 0 and records > 0:
        raise ValueError("ortalama arama suresi sifirdan buyuk olmali")

    attempt_rows = []
    attempts_this_round = float(records)
    total_attempts = 0.0
    total_answers = 0.0

    for attempt_number in range(1, max_attempts + 1):
        answered = attempts_this_round * probability
        unanswered = attempts_this_round - answered
        total_attempts += attempts_this_round
        total_answers += answered
        attempt_rows.append({
            "attempt_number": attempt_number,
            "expected_attempts": round(attempts_this_round, 4),
            "expected_answers": round(answered, 4),
            "expected_unanswered": round(unanswered, 4),
        })
        attempts_this_round = unanswered * retry_fraction

    baseline_seconds = records * avg_attempt_seconds
    retry_seconds = total_attempts * avg_attempt_seconds
    baseline_hours = baseline_seconds / ports / 3600
    retry_hours = retry_seconds / ports / 3600

    return {
        "records": records,
        "ports": ports,
        "answer_probability": probability,
        "max_attempts": max_attempts,
        "retry_fraction": retry_fraction,
        "avg_attempt_seconds": round(avg_attempt_seconds, 4),
        "baseline_attempts": records,
        "expected_total_attempts": round(total_attempts, 4),
        "expected_extra_attempts": round(total_attempts - records, 4),
        "expected_answered_customers": round(total_answers, 4),
        "expected_never_answered_customers": round(records - total_answers, 4),
        "baseline_completion_hours": round(baseline_hours, 4),
        "retry_completion_hours": round(retry_hours, 4),
        "extra_completion_hours": round(retry_hours - baseline_hours, 4),
        "attempt_breakdown": attempt_rows,
        "note": (
            "Beklenen deger modelidir; retry bekleme araliklari, arama saatleri, "
            "operasyonel kesintiler ve kampanya politikalari dahil degildir."
        ),
    }
