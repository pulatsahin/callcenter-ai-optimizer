"""Hourly outbound attempts versus an ideal, continuously busy port model.

Input rows are normalized by services.outbound_importer.load_outbound_csv.
A timestamp denotes the start of an attempt; counts are NOT port occupancy.
The observed CSV may be sampled or filtered, so the result is a descriptive
comparison, NOT measured port utilization or a diagnosis of a bottleneck.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Europe/Istanbul')


def analyze_hourly_variance(records: list[dict], *, ports: int,
                            avg_port_seconds: float | None = None,
                            timezone: str = 'Europe/Istanbul') -> dict:
    """Group attempt starts by local clock hour and compare with ideal capacity.

    If avg_port_seconds is omitted, the mean of duration_seconds is used.
    Missing hours are not fabricated as zeros: absence of rows does not prove
    the campaign was active or that the export is complete in those hours.
    """
    if not records:
        raise ValueError('En az bir arama kaydı gereklidir.')
    if isinstance(ports, bool) or not isinstance(ports, int) or ports <= 0:
        raise ValueError('ports pozitif tam sayı olmalıdır.')
    try:
        zone = ZoneInfo(timezone)
    except (KeyError, TypeError) as exc:
        raise ValueError('Geçersiz saat dilimi') from exc

    groups: dict[datetime, list[dict]] = defaultdict(list)
    durations = []
    campaign_ids = set()
    for row in records:
        moment = row.get('timestamp')
        duration = row.get('duration_seconds')
        if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError('timestamp saat dilimi içermelidir.')
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not isfinite(duration) or duration < 0:
            raise ValueError('duration_seconds negatif olmayan sonlu sayı olmalıdır.')
        if row.get('campaign_id') is None:
            raise ValueError('campaign_id gereklidir.')
        campaign_ids.add(row['campaign_id'])
        local = moment.astimezone(zone)
        hour = local.replace(minute=0, second=0, microsecond=0)
        groups[hour].append(row)
        durations.append(float(duration))
    if len(campaign_ids) != 1:
        raise ValueError('Tek kampanya seçilmelidir; farklı kampanyalar birleştirilemez.')

    if avg_port_seconds is None:
        avg_port_seconds = sum(durations) / len(durations)
        duration_source = 'CSV ortalaması'
    else:
        duration_source = 'Kullanıcı girdisi'
    if isinstance(avg_port_seconds, bool) or not isinstance(avg_port_seconds, (int, float)) or not isfinite(avg_port_seconds) or avg_port_seconds <= 0:
        raise ValueError('Ortalama port işgali pozitif sonlu sayı olmalıdır.')
    ideal_per_hour = ports * 3600 / avg_port_seconds
    rows = []
    for hour, attempts in sorted(groups.items()):
        count = len(attempts)
        ratio = count / ideal_per_hour * 100
        rows.append({
            'hour': hour.isoformat(),
            'attempts': count,
            'ideal_attempts_per_hour': round(ideal_per_hour, 4),
            'difference_attempts': round(count - ideal_per_hour, 4),
            'ratio_to_ideal_pct': round(ratio, 2),
            'below_ideal_pct': round((1 - count / ideal_per_hour) * 100, 2),
        })
    return {
        'campaign_id': next(iter(campaign_ids)),
        'allocated_ports': ports,
        'avg_port_seconds': round(avg_port_seconds, 4),
        'duration_source': duration_source,
        'ideal_attempts_per_hour': round(ideal_per_hour, 4),
        'observed_attempts': len(records),
        'observed_hour_buckets': len(rows),
        'hourly': rows,
        'timezone': timezone,
        'caveat': ('Saatlik sayılar arama BAŞLANGIÇ zamanına göre gruplanır. İlk/son saat kısmi olabilir; '
                   'eksik saatler sıfır sayılmaz. CSV eksiksiz ve port tahsisi sabit değilse oran gerçek '
                   'kapasite kullanımını göstermez. Pacing, bekleme veya arıza nedeni bu veriyle belirlenemez.'),
    }
