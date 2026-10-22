"""Tests for synthetic outcome-aware retry calculations."""
import pytest
from services.retry_dispositions import analyze_retry_dispositions


def calculate(**overrides):
    args = dict(
        records=7000,
        ports=5,
        outcome_rates={"answered": .4, "no_answer": .4, "busy": .15, "failed": .05},
        retry_rates={"no_answer": 1, "busy": .5, "failed": 0},
        outcome_seconds={"answered": 20, "no_answer": 30, "busy": 10, "failed": 5},
        overhead_seconds=2,
        max_attempts=3,
    )
    args.update(overrides)
    return analyze_retry_dispositions(**args)


def test_expected_rounds():
    result = calculate()
    assert [r["attempts"] for r in result["rounds"]] == [7000, 3325, 1579.375]
    assert result["expected_total_attempts"] == 11904.375


def test_answers_are_unique_expected():
    result = calculate()
    assert result["expected_unique_answered"] == 4761.75
    assert result["expected_never_answered"] == 2238.25


def test_zero_retry():
    result = calculate(retry_rates={"no_answer": 0, "busy": 0, "failed": 0})
    assert len(result["rounds"]) == 1
    assert result["expected_total_attempts"] == 7000


def test_zero_records():
    result = calculate(records=0)
    assert result["rounds"] == []
    assert result["retry_active_hours"] == 0


def test_single_attempt():
    assert calculate(max_attempts=1)["expected_total_attempts"] == 7000


def test_all_answered():
    result = calculate(outcome_rates={"answered": 1, "no_answer": 0, "busy": 0, "failed": 0})
    assert result["expected_total_attempts"] == 7000


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan"), float("inf"), True])
def test_invalid_retry_fraction(bad):
    with pytest.raises(ValueError):
        calculate(retry_rates={"no_answer": bad, "busy": 0, "failed": 0})


def test_invalid_sum():
    with pytest.raises(ValueError):
        calculate(outcome_rates={"answered": .5, "no_answer": .4, "busy": .15, "failed": .05})


def test_missing_outcome():
    with pytest.raises(ValueError):
        calculate(outcome_rates={"answered": .4, "no_answer": .6})


def test_negative_seconds():
    with pytest.raises(ValueError):
        calculate(outcome_seconds={"answered": -1, "no_answer": 30, "busy": 10, "failed": 5})


def test_invalid_ports():
    with pytest.raises(ValueError):
        calculate(ports=0)


def test_no_attempts():
    with pytest.raises(ValueError):
        calculate(max_attempts=0)


def test_100_percent_retry_matches_simple_geometric():
    result = calculate(
        outcome_rates={"answered": .4, "no_answer": .6, "busy": 0, "failed": 0},
        retry_rates={"no_answer": 1, "busy": 0, "failed": 0},
    )
    assert result["expected_total_attempts"] == 13720
