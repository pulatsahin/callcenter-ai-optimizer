from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from services.retry_policy import simulate_retry_policy

TR = ZoneInfo("Europe/Istanbul")


def scenario(**changes):
    values = dict(
        records=7000, ports=5, answer_rate=40,
        avg_ivr_seconds=20, avg_no_answer_seconds=30,
        overhead_seconds=2, max_attempts=3, retry_fraction=1.0,
        retry_wait_hours=24,
        start_datetime=datetime(2026, 10, 26, 16, tzinfo=TR),
    )
    values.update(changes)
    return simulate_retry_policy(**values)


def test_expected_load_unchanged():
    r = scenario()
    assert r["analysis"]["expected_total_attempts"] == 13720
    assert [x["expected_attempts"] for x in r["rounds"]] == [7000, 4200, 2520]


def test_baseline_finish():
    assert scenario()["baseline_finish"] == "2026-10-27T17:53:24+03:00"


def test_rounds_order_and_minimum_wait():
    r = scenario()
    for previous, following in zip(r["rounds"], r["rounds"][1:]):
        previous_end = datetime.fromisoformat(previous["finish"])
        following_start = datetime.fromisoformat(following["start"])
        assert following_start >= previous_end + __import__("datetime").timedelta(hours=24)
        assert following_start.weekday() < 5
        assert 9 <= following_start.hour < 18


def test_no_retry_means_single_round():
    r = scenario(retry_fraction=0)
    assert len(r["rounds"]) == 1
    assert r["retry_finish"] == r["baseline_finish"]


def test_single_attempt():
    assert len(scenario(max_attempts=1)["rounds"]) == 1


def test_half_day_and_holiday_skip():
    r = scenario(records=1, max_attempts=1, start_datetime=datetime(2026, 10, 28, 14, tzinfo=TR))
    assert r["rounds"][0]["start"] == "2026-10-30T09:00:00+03:00"


def test_cross_year_calendar():
    r = scenario(records=1, max_attempts=1, start_datetime=datetime(2026, 12, 31, 19, tzinfo=TR))
    assert r["rounds"][0]["start"] == "2027-01-04T09:00:00+03:00"


def test_zero_records():
    r = scenario(records=0)
    assert r["rounds"] == []
    assert r["retry_finish"] == r["baseline_finish"]


@pytest.mark.parametrize("kwargs", [
    {"retry_wait_hours": -1},
    {"retry_wait_hours": float("inf")},
    {"start_datetime": datetime(2026, 10, 26, 16)},
])
def test_invalid_inputs(kwargs):
    with pytest.raises(ValueError):
        scenario(**kwargs)
