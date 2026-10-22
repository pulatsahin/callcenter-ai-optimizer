import math
import pytest
from services.observed_capacity import forecast_observed_capacity

ROWS = [
    {'disposition': 'ANSWERED', 'duration_seconds': 20},
    {'disposition': 'NO_ANSWER', 'duration_seconds': 30},
    {'disposition': 'BUSY', 'duration_seconds': 10},
    {'disposition': 'FAILED', 'duration_seconds': 8},
]


def test_metrics():
    result = forecast_observed_capacity(ROWS, remaining_records=7000, ports=5)
    assert result['observed_answer_rate_pct'] == 25
    assert result['observed_avg_duration_seconds'] == 17
    assert result['calls_per_hour'] == pytest.approx(1058.8235, abs=0.0001)
    assert result['completion_hours'] == pytest.approx(6.6111, abs=0.0001)
    assert result['required_ports_for_4h'] == 9


def test_extra_overhead():
    result = forecast_observed_capacity(ROWS, remaining_records=7000, ports=5, extra_overhead_seconds=2)
    assert result['avg_port_seconds'] == 19
    assert result['completion_hours'] == pytest.approx(7.3889, abs=0.0001)


def test_zero_remaining():
    result = forecast_observed_capacity(ROWS, remaining_records=0, ports=1)
    assert result['completion_hours'] == 0
    assert result['required_ports_for_4h'] == 0


@pytest.mark.parametrize('kwargs', [
    {'remaining_records': -1, 'ports': 5},
    {'remaining_records': 2.5, 'ports': 5},
    {'remaining_records': True, 'ports': 5},
    {'remaining_records': 10, 'ports': 0},
    {'remaining_records': 10, 'ports': True},
    {'remaining_records': 10, 'ports': 1, 'extra_overhead_seconds': -1},
    {'remaining_records': 10, 'ports': 1, 'extra_overhead_seconds': math.inf},
])
def test_bad_inputs(kwargs):
    with pytest.raises(ValueError):
        forecast_observed_capacity(ROWS, **kwargs)


def test_empty():
    with pytest.raises(ValueError):
        forecast_observed_capacity([], remaining_records=1, ports=1)


def test_bad_disposition():
    with pytest.raises(ValueError):
        forecast_observed_capacity([{'disposition':'OTHER','duration_seconds':10}], remaining_records=1, ports=1)


def test_bad_duration():
    with pytest.raises(ValueError):
        forecast_observed_capacity([{'disposition':'ANSWERED','duration_seconds':float('nan')}], remaining_records=1, ports=1)
