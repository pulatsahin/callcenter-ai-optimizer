import pytest

from services.retry_capacity import analyze_retry_capacity


def analyze(**overrides):
    params = dict(
        records=7000,
        ports=5,
        answer_rate=40,
        avg_ivr_seconds=20,
        avg_no_answer_seconds=30,
        overhead_seconds=2,
        max_attempts=3,
        retry_fraction=1.0,
    )
    params.update(overrides)
    return analyze_retry_capacity(**params)


def test_three_attempts():
    result = analyze()
    assert result["expected_total_attempts"] == 13720
    assert result["expected_extra_attempts"] == 6720
    assert result["expected_answered_customers"] == 5488
    assert result["expected_never_answered_customers"] == 1512
    assert result["baseline_completion_hours"] == pytest.approx(10.8889, abs=0.0001)
    assert result["retry_completion_hours"] == pytest.approx(21.3422, abs=0.0001)
    assert [r["expected_attempts"] for r in result["attempt_breakdown"]] == [7000, 4200, 2520]


def test_single_attempt_has_no_retry():
    result = analyze(max_attempts=1)
    assert result["expected_extra_attempts"] == 0
    assert result["retry_completion_hours"] == result["baseline_completion_hours"]


def test_zero_retry_fraction():
    result = analyze(retry_fraction=0)
    assert result["expected_total_attempts"] == 7000


def test_half_retry_fraction():
    result = analyze(retry_fraction=0.5)
    assert result["expected_total_attempts"] == pytest.approx(9730)


def test_zero_records():
    result = analyze(records=0)
    assert result["retry_completion_hours"] == 0
    assert result["expected_total_attempts"] == 0


def test_probability_percent_and_fraction_match():
    assert analyze(answer_rate=40) == analyze(answer_rate=0.4)


@pytest.mark.parametrize("overrides", [
    {"records": -1},
    {"ports": 0},
    {"max_attempts": 0},
    {"retry_fraction": 1.2},
    {"answer_rate": 101},
    {"avg_ivr_seconds": -1},
])
def test_invalid_values(overrides):
    with pytest.raises(ValueError):
        analyze(**overrides)
