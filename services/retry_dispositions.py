"""Outcome-aware outbound retry expectation model.

This is a synthetic scenario calculator, NOT an implementation of Genesys
Engage result codes or a prediction of its dialing engine behavior.

Each dial attempt has mutually exclusive outcomes: answered, no_answer, busy,
failed. Only unanswered outcomes are eligible for retry, with independent
per-outcome retry fractions. Probabilities remain constant across rounds.
"""
from __future__ import annotations

from math import isfinite

OUTCOMES = ("answered", "no_answer", "busy", "failed")
RETRYABLE = ("no_answer", "busy", "failed")


def _fraction(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} sonlu bir sayi olmali")
    if not 0 <= value <= 1:
        raise ValueError(f"{name} 0 ile 1 arasinda olmali")
    return float(value)


def analyze_retry_dispositions(
    *,
    records: int,
    ports: int,
    outcome_rates: dict[str, float],
    retry_rates: dict[str, float],
    outcome_seconds: dict[str, float],
    overhead_seconds: float = 2.0,
    max_attempts: int = 3,
) -> dict:
    """Calculate expected attempt counts and port working hours by outcome.

    Rates are fractions (0..1), not percentages. ``outcome_rates`` must sum
    to 1 (within floating-point tolerance). Retry rates apply to the outcomes
    from the *previous* round. Output counts are fractional expectations.
    """
    if isinstance(records, bool) or not isinstance(records, int) or records < 0:
        raise ValueError("records sifir veya pozitif tam sayi olmali")
    if isinstance(ports, bool) or not isinstance(ports, int) or ports < 1:
        raise ValueError("ports pozitif tam sayi olmali")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts en az 1 olmali")
    if set(outcome_rates) != set(OUTCOMES):
        raise ValueError(f"outcome_rates tam olarak {OUTCOMES} icermeli")
    if set(retry_rates) != set(RETRYABLE):
        raise ValueError(f"retry_rates tam olarak {RETRYABLE} icermeli")
    if set(outcome_seconds) != set(OUTCOMES):
        raise ValueError(f"outcome_seconds tam olarak {OUTCOMES} icermeli")

    probabilities = {k: _fraction(k, outcome_rates[k]) for k in OUTCOMES}
    policies = {k: _fraction(f"retry_{k}", retry_rates[k]) for k in RETRYABLE}
    if abs(sum(probabilities.values()) - 1.0) > 1e-9:
        raise ValueError("outcome_rates toplami 1 olmali")

    for name, value in {**outcome_seconds, "overhead_seconds": overhead_seconds}.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError(f"{name} negatif olmayan sonlu sure olmali")

    avg_seconds = sum(probabilities[k] * outcome_seconds[k] for k in OUTCOMES) + overhead_seconds
    if records and avg_seconds <= 0:
        raise ValueError("ortalama arama suresi sifirdan buyuk olmali")

    attempts = float(records)
    rows = []
    totals = {k: 0.0 for k in OUTCOMES}
    total_attempts = 0.0
    total_seconds = 0.0

    for round_number in range(1, max_attempts + 1):
        if attempts <= 0:
            break
        counts = {k: attempts * probabilities[k] for k in OUTCOMES}
        eligible = {k: counts[k] * policies[k] for k in RETRYABLE}
        next_attempts = sum(eligible.values())
        seconds = sum(counts[k] * outcome_seconds[k] for k in OUTCOMES) + attempts * overhead_seconds
        for key in OUTCOMES:
            totals[key] += counts[key]
        total_attempts += attempts
        total_seconds += seconds
        rows.append({
            "round": round_number,
            "attempts": round(attempts, 4),
            "outcomes": {k: round(v, 4) for k, v in counts.items()},
            "retry_eligible_next_round": round(next_attempts, 4) if round_number < max_attempts else 0.0,
            "active_hours": round(seconds / ports / 3600, 4),
        })
        attempts = next_attempts

    return {
        "records": records,
        "ports": ports,
        "max_attempts": max_attempts,
        "outcome_rates": probabilities,
        "retry_rates": policies,
        "outcome_seconds": dict(outcome_seconds),
        "overhead_seconds": overhead_seconds,
        "expected_total_attempts": round(total_attempts, 4),
        "expected_extra_attempts": round(total_attempts - records, 4),
        "expected_unique_answered": round(totals["answered"], 4),
        "expected_never_answered": round(records - totals["answered"], 4),
        "expected_outcome_counts": {k: round(v, 4) for k, v in totals.items()},
        "baseline_active_hours": round(records * avg_seconds / ports / 3600, 4),
        "retry_active_hours": round(total_seconds / ports / 3600, 4),
        "rounds": rows,
        "assumptions": (
            "Sentetik beklenen deger modeli; oranlar her turda sabittir. "
            "Retry bekleme, resmi tatiller, kayit bazli zamanlama ve "
            "Genesys Engage kampanya konfigurasyonu bu hesapta yoktur."
        ),
    }
