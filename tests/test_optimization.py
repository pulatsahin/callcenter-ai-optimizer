
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from database.init_db import initialize_database
from mcp_server.server import (
    build_finish_time,
    load_campaign,
)
from services.retry_simulator import simulate_retries
from services.port_optimizer import optimize_port_allocation
from services.schedule_planner import (
    estimate_finish_datetime,
    load_holidays,
)


def sample_campaigns():
    return [
        {
            "id": 101,
            "name": "IVR Tahsilat",
            "total_records": 10000,
            "completed_records": 3000,
            "ports": 5,
            "answer_rate": 40,
            "avg_ivr_seconds": 20,
            "avg_no_answer_seconds": 30,
            "overhead_seconds": 2,
        },
        {
            "id": 102,
            "name": "Musteri Bilgilendirme",
            "total_records": 15000,
            "completed_records": 5000,
            "ports": 8,
            "answer_rate": 55,
            "avg_ivr_seconds": 25,
            "avg_no_answer_seconds": 25,
            "overhead_seconds": 2,
        },
    ]


def istanbul_datetime(year, month, day, hour, minute=0):
    return datetime(
        year, month, day, hour, minute,
        tzinfo=ZoneInfo("Europe/Istanbul"),
    )


def test_retry_simulation():
    result = simulate_retries(
        records=1000,
        answer_rate=40,
        max_attempts=3,
        retry_fraction=1.0,
    )

    assert result["expected_total_attempts"] == 1960
    assert result["expected_answers"] == 784
    assert result["expected_unanswered_after_final_attempt"] == 216


def test_campaign_101_retry():
    result = simulate_retries(
        records=7000,
        answer_rate=40,
        max_attempts=3,
        retry_fraction=1.0,
    )

    assert result["expected_total_attempts"] == 13720


def test_port_optimizer():
    result = optimize_port_allocation(
        sample_campaigns(),
        total_ports=20,
    )

    assert result["allocated_ports"] == 20
    assert len(result["allocation"]) == 2
    assert all(
        row["recommended_ports"] >= 1
        for row in result["allocation"]
    )


def test_schedule_planner():
    start = istanbul_datetime(2026, 10, 26, 16)

    result = estimate_finish_datetime(
        completion_hours=5,
        start_datetime=start,
    )

    assert result["estimated_finish"] == (
        "2026-10-27T12:00:00+03:00"
    )


@pytest.fixture
def campaign_101():
    return sample_campaigns()[0]


def test_actual_finish_uses_database_ports(campaign_101):
    result = build_finish_time(
        campaign=campaign_101,
        start_datetime="2026-10-26T16:00:00+03:00",
        ports=campaign_101["ports"],
        calculation_type="ACTUAL",
    )

    assert result["actual_ports"] == 5
    assert result["calculation_ports"] == 5
    assert result["calculation_type"] == "ACTUAL"
    assert result["schedule"]["estimated_finish"] == (
        "2026-10-27T17:53:24+03:00"
    )


def test_simulated_finish_keeps_actual_ports(campaign_101):
    result = build_finish_time(
        campaign=campaign_101,
        start_datetime="2026-10-26T16:00:00+03:00",
        ports=7,
        calculation_type="SIMULATION",
    )

    assert result["actual_ports"] == 5
    assert result["calculation_ports"] == 7
    assert result["calculation_type"] == "SIMULATION"
    assert result["schedule"]["estimated_finish"] == (
        "2026-10-27T14:46:48+03:00"
    )


def test_database_actual_ports_unchanged():
    initialize_database()

    before = load_campaign(101)["ports"]
    campaign = load_campaign(101)

    build_finish_time(
        campaign=campaign,
        start_datetime="2026-10-26T16:00:00+03:00",
        ports=7,
        calculation_type="SIMULATION",
    )

    after = load_campaign(101)["ports"]

    assert before == after


def test_holiday_calendar_loaded():
    holidays = load_holidays()

    assert holidays["year"] == 2026
    assert "2026-10-29" in holidays["full_days"]
    assert holidays["half_days"]["2026-10-28"] == "13:00"


def test_republic_day_half_and_full_holiday():
    start = istanbul_datetime(2026, 10, 28, 12)

    result = estimate_finish_datetime(
        completion_hours=3,
        start_datetime=start,
    )

    assert result["estimated_finish"] == (
        "2026-10-30T11:00:00+03:00"
    )

    assert any(
        row["date"] == "2026-10-29"
        for row in result["skipped_holidays"]
    )


def test_start_during_full_holiday():
    start = istanbul_datetime(2026, 10, 29, 10)

    result = estimate_finish_datetime(
        completion_hours=2,
        start_datetime=start,
    )

    assert result["estimated_finish"] == (
        "2026-10-30T11:00:00+03:00"
    )


def test_ramadan_eve_half_day():
    start = istanbul_datetime(2026, 3, 19, 12)

    result = estimate_finish_datetime(
        completion_hours=2,
        start_datetime=start,
    )

    # 19 Mart: 12:00-13:00, bir saat.
    # 20 Mart: bayram.
    # 21-22 Mart: hafta sonu.
    # 23 Mart: 09:00-10:00, bir saat.
    assert result["estimated_finish"] == (
        "2026-03-23T10:00:00+03:00"
    )
