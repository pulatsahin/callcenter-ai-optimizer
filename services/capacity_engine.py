
from math import ceil


def calculate_capacity(
    remaining_records: int,
    ports: int,
    answer_rate: float,
    avg_ivr_seconds: float,
    avg_no_answer_seconds: float,
    overhead_seconds: float = 2,
    target_hours: float = 4,
) -> dict:
    """Outbound IVR kampanyası için kapasite tahmini."""

    if remaining_records < 0:
        raise ValueError("Kalan kayit negatif olamaz.")

    if ports <= 0:
        raise ValueError("Port sayisi sifirdan buyuk olmali.")

    if not 0 <= answer_rate <= 100:
        raise ValueError("Cevaplanma orani 0-100 arasinda olmali.")

    if min(
        avg_ivr_seconds,
        avg_no_answer_seconds,
        overhead_seconds
    ) < 0:
        raise ValueError("Sureler negatif olamaz.")

    if target_hours <= 0:
        raise ValueError("Hedef sure sifirdan buyuk olmali.")

    answer_probability = answer_rate / 100

    avg_port_seconds = (
        answer_probability * avg_ivr_seconds
        + (1 - answer_probability) * avg_no_answer_seconds
        + overhead_seconds
    )

    if avg_port_seconds <= 0:
        raise ValueError("Ortalama port suresi sifirdan buyuk olmali.")

    calls_per_hour = ports * 3600 / avg_port_seconds
    completion_hours = remaining_records / calls_per_hour

    required_ports = ceil(
        remaining_records * avg_port_seconds
        / (target_hours * 3600)
    )

    return {
        "remaining_records": remaining_records,
        "ports": ports,
        "avg_port_seconds": round(avg_port_seconds, 2),
        "calls_per_hour": round(calls_per_hour, 2),
        "completion_hours": round(completion_hours, 2),
        "target_hours": target_hours,
        "required_ports": required_ports,
    }
