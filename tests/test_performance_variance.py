from datetime import datetime, timezone, timedelta
import pytest
from services.performance_variance import analyze_hourly_variance


def row(hour, minute, duration=20, campaign=101):
    return {'campaign_id': campaign, 'timestamp': datetime(2026, 10, 26, hour, minute, tzinfo=timezone.utc),
            'duration_seconds': duration}


def test_ideal_and_hourly():
    result = analyze_hourly_variance([row(7, 1), row(7, 2), row(8, 0)], ports=5)
    assert result['ideal_attempts_per_hour'] == 900
    assert [x['attempts'] for x in result['hourly']] == [2, 1]
    assert result['hourly'][0]['hour'].startswith('2026-10-26T10:00')


def test_explicit_duration():
    result = analyze_hourly_variance([row(7, 0, 30)], ports=5, avg_port_seconds=20)
    assert result['ideal_attempts_per_hour'] == 900
    assert result['duration_source'] == 'Kullanıcı girdisi'


def test_uses_csv_mean():
    result = analyze_hourly_variance([row(7, 0, 10), row(7, 1, 30)], ports=2)
    assert result['avg_port_seconds'] == 20
    assert result['ideal_attempts_per_hour'] == 360


def test_not_invent_missing_hours():
    result = analyze_hourly_variance([row(7, 0), row(10, 0)], ports=5)
    assert len(result['hourly']) == 2


def test_different_campaigns_rejected():
    with pytest.raises(ValueError, match='Tek kampanya'):
        analyze_hourly_variance([row(7, 0), row(7, 1, campaign=102)], ports=5)

@pytest.mark.parametrize('ports', [0, -1, True, 2.5])
def test_bad_ports(ports):
    with pytest.raises(ValueError):
        analyze_hourly_variance([row(7, 0)], ports=ports)

@pytest.mark.parametrize('duration', [0, -1, float('nan'), float('inf'), True])
def test_bad_duration(duration):
    with pytest.raises(ValueError):
        analyze_hourly_variance([row(7, 0)], ports=5, avg_port_seconds=duration)


def test_naive_timestamp_rejected():
    r = row(7, 0)
    r['timestamp'] = datetime(2026, 10, 26, 7)
    with pytest.raises(ValueError, match='saat dilimi'):
        analyze_hourly_variance([r], ports=5)


def test_empty_rejected():
    with pytest.raises(ValueError):
        analyze_hourly_variance([], ports=5)


def test_timezone_boundary():
    result = analyze_hourly_variance([row(21, 0)], ports=1)
    assert result['hourly'][0]['hour'].startswith('2026-10-27T00:00')


def test_counts_can_exceed_ideal_without_clipping():
    result = analyze_hourly_variance([row(7, 0), row(7, 1)], ports=1, avg_port_seconds=3600)
    assert result['hourly'][0]['ratio_to_ideal_pct'] == 200
    assert result['hourly'][0]['below_ideal_pct'] == -100


def test_bad_observed_duration():
    with pytest.raises(ValueError):
        analyze_hourly_variance([row(7, 0, duration=-1)], ports=5)
