"""Capacity forecast calibrated from anonymized outbound attempt observations.

The observed durations must have a consistent meaning (port occupancy seconds).
No unique-customer or future answer-rate inference is made.
"""
from __future__ import annotations
from math import isfinite, ceil


def forecast_observed_capacity(records: list[dict], *, remaining_records: int,
                               ports: int, extra_overhead_seconds: float = 0.0) -> dict:
    """Estimate a single-attempt remaining workload using CSV observed means.

    ``remaining_records`` means planned future attempts, not unique customers.
    ``ports`` means allocated simultaneous ports, not observed distinct port IDs.
    ``extra_overhead_seconds`` must be zero if durations already include all occupancy.
    """
    if not records:
        raise ValueError('En az bir arama kaydı gereklidir.')
    if isinstance(remaining_records, bool) or not isinstance(remaining_records, int) or remaining_records < 0:
        raise ValueError('remaining_records negatif olmayan tam sayı olmalıdır.')
    if isinstance(ports, bool) or not isinstance(ports, int) or ports < 1:
        raise ValueError('ports pozitif tam sayı olmalıdır.')
    if isinstance(extra_overhead_seconds, bool) or not isinstance(extra_overhead_seconds, (int, float)) or not isfinite(extra_overhead_seconds) or extra_overhead_seconds < 0:
        raise ValueError('extra_overhead_seconds geçerli, negatif olmayan sayı olmalıdır.')
    total_seconds = 0.0
    answered = 0
    counts = {'ANSWERED': 0, 'NO_ANSWER': 0, 'BUSY': 0, 'FAILED': 0}
    for row in records:
        disposition = row.get('disposition')
        duration = row.get('duration_seconds')
        if disposition not in counts:
            raise ValueError(f'Geçersiz disposition: {disposition}')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not isfinite(duration) or duration < 0:
            raise ValueError('Geçersiz duration_seconds')
        counts[disposition] += 1
        total_seconds += duration
        answered += disposition == 'ANSWERED'
    avg_observed = total_seconds / len(records)
    avg_port_seconds = avg_observed + extra_overhead_seconds
    if avg_port_seconds <= 0:
        raise ValueError('Ortalama port işgal süresi sıfırdan büyük olmalıdır.')
    hours = remaining_records * avg_port_seconds / (ports * 3600)
    return {
        'observed_attempts': len(records),
        'observed_answer_rate_pct': round(answered / len(records) * 100, 2),
        'disposition_counts': counts,
        'observed_avg_duration_seconds': round(avg_observed, 4),
        'extra_overhead_seconds': float(extra_overhead_seconds),
        'avg_port_seconds': round(avg_port_seconds, 4),
        'remaining_attempts': remaining_records,
        'allocated_ports': ports,
        'calls_per_hour': round(ports * 3600 / avg_port_seconds, 4),
        'completion_hours': round(hours, 4),
        'required_ports_for_4h': ceil(remaining_records * avg_port_seconds / (4 * 3600)) if remaining_records else 0,
        'assumption': 'CSV örnekleminin sonuç dağılımı ve port işgal süresi gelecekte sabit kalır; tek deneme modeli, retry dahil değildir.',
    }
