
import pytest

from services.capacity_engine import calculate_capacity


def test_capacity_calculation():
    result = calculate_capacity(
        remaining_records=7000,
        ports=5,
        answer_rate=40,
        avg_ivr_seconds=20,
        avg_no_answer_seconds=30,
        overhead_seconds=2,
    )

    assert result["avg_port_seconds"] == 28
    assert result["calls_per_hour"] == 642.86
    assert result["completion_hours"] == 10.89
    assert result["required_ports"] == 14


def test_invalid_ports():
    with pytest.raises(ValueError):
        calculate_capacity(1000, 0, 40, 20, 30)


def test_invalid_answer_rate():
    with pytest.raises(ValueError):
        calculate_capacity(1000, 5, 120, 20, 30)


def test_zero_remaining_records():
    result = calculate_capacity(0, 5, 40, 20, 30)

    assert result["completion_hours"] == 0
    assert result["required_ports"] == 0
