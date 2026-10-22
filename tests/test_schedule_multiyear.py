
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.schedule_planner import (
    load_holiday_calendar,
    estimate_finish_datetime,
)


TR = ZoneInfo("Europe/Istanbul")


def dt(year, month, day, hour, minute=0):
    return datetime(
        year, month, day, hour, minute,
        tzinfo=TR,
    )


def test_load_2027_calendar():
    calendar = load_holiday_calendar(2027)

    assert calendar["year"] == 2027
    assert calendar["full_days"]["2027-01-01"] == "Yilbasi"
    assert calendar["half_days"]["2027-03-08"] == "13:00"


def test_existing_2026_result():
    result = estimate_finish_datetime(
        completion_hours=10.89,
        start_datetime=dt(2026, 10, 26, 16),
    )

    assert result["estimated_finish"] == (
        "2026-10-27T17:53:24+03:00"
    )


def test_cross_year_with_new_year_holiday():
    result = estimate_finish_datetime(
        completion_hours=5,
        start_datetime=dt(2026, 12, 31, 16),
    )

    assert result["estimated_finish"] == (
        "2027-01-04T12:00:00+03:00"
    )

    assert result["holiday_calendars"] == [
        "TR-2026",
        "TR-2027",
    ]

    assert any(
        holiday["date"] == "2027-01-01"
        for holiday in result["skipped_holidays"]
    )


def test_2027_ramadan_half_day():
    result = estimate_finish_datetime(
        completion_hours=3,
        start_datetime=dt(2027, 3, 8, 12),
    )

    assert result["estimated_finish"] == (
        "2027-03-12T11:00:00+03:00"
    )

    assert "2027-03-08" in result["partial_holidays"]


def test_2027_republic_day():
    result = estimate_finish_datetime(
        completion_hours=5,
        start_datetime=dt(2027, 10, 28, 12),
    )

    assert result["estimated_finish"] == (
        "2027-11-01T13:00:00+03:00"
    )


def test_missing_calendar_raises():
    with pytest.raises(FileNotFoundError):
        estimate_finish_datetime(
            completion_hours=1,
            start_datetime=dt(2028, 1, 3, 10),
        )
