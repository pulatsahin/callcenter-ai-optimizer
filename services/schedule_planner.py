
import json

from datetime import datetime, date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Europe/Istanbul")

ROOT = Path(__file__).resolve().parent.parent
HOLIDAY_DIR = ROOT / "data"

WORK_START = time(9, 0)
WORK_END = time(18, 0)


# ==================================================
# HOLIDAY CALENDAR
# ==================================================

def load_holiday_calendar(year: int) -> dict:
    """
    Belirtilen yilin Turkiye resmi tatil
    takvimini JSON dosyasindan yukler.

    Ornek:
        data/holidays_tr_2026.json
        data/holidays_tr_2027.json
    """

    path = HOLIDAY_DIR / f"holidays_tr_{year}.json"

    if not path.is_file():
        raise FileNotFoundError(
            f"{year} yili Turkiye resmi tatil "
            f"takvimi bulunamadi: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        calendar = json.load(file)

    if calendar.get("year") != year:
        raise ValueError(
            f"Tatil takvimi yil uyusmazligi: {year}"
        )

    if calendar.get("country") != "TR":
        raise ValueError(
            "Tatil takvimi ulkesi TR olmali."
        )

    if calendar.get("timezone") != "Europe/Istanbul":
        raise ValueError(
            "Tatil takvimi saat dilimi hatali."
        )

    full_days = calendar.get("full_days")
    half_days = calendar.get("half_days")

    if not isinstance(full_days, dict):
        raise ValueError(
            "full_days bir JSON nesnesi olmali."
        )

    if not isinstance(half_days, dict):
        raise ValueError(
            "half_days bir JSON nesnesi olmali."
        )

    for day, name in full_days.items():

        parsed = date.fromisoformat(day)

        if parsed.year != year:
            raise ValueError(
                f"Yanlis yil iceren tatil: {day}"
            )

        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"Gecersiz tatil adi: {day}"
            )

    for day, cutoff in half_days.items():

        parsed = date.fromisoformat(day)

        if parsed.year != year:
            raise ValueError(
                f"Yanlis yil iceren yari gun: {day}"
            )

        parsed_cutoff = time.fromisoformat(
            cutoff
        )

        if not (
            WORK_START < parsed_cutoff < WORK_END
        ):
            raise ValueError(
                f"Gecersiz yari gun bitisi: {day}"
            )

        if day in full_days:
            raise ValueError(
                f"Ayni gun hem tam hem yari tatil: {day}"
            )

    return calendar


# ==================================================
# BACKWARD COMPATIBILITY
# ==================================================

def load_holidays(year: int = 2026) -> dict:
    """
    Eski testler ve kodlar icin uyumluluk.

    Eski kullanim:
        load_holidays()

    Yeni kullanim:
        load_holiday_calendar(2027)

    Her iki fonksiyon da ayni takvim
    veri yapisini dondurur.
    """

    return load_holiday_calendar(year)


# ==================================================
# FINISH TIME CALCULATION
# ==================================================

def estimate_finish_datetime(
    completion_hours: float,
    start_datetime: datetime,
) -> dict:
    """
    Kampanyanin tahmini bitis zamanini hesaplar.

    Kurallar:
    - Pazartesi-Cuma calisma
    - 09:00-18:00
    - Tam gun resmi tatiller atlanir
    - Yari gun tatillerde 13:00'te durulur
    - Birden fazla yil desteklenir
    - Saniye hassasiyeti korunur
    - Eksik takvim dosyasi hata uretir
    """

    if completion_hours < 0:
        raise ValueError(
            "Tamamlanma suresi negatif olamaz."
        )

    if start_datetime.tzinfo is None:
        raise ValueError(
            "Baslangic tarihi timezone icermeli."
        )

    current = start_datetime.astimezone(
        TIMEZONE
    )

    remaining = timedelta(
        hours=completion_hours
    )

    calendars = {}

    skipped_holidays = []
    partial_holidays = []

    seen_full = set()
    seen_partial = set()

    def calendar_for(year):
        if year not in calendars:
            calendars[year] = (
                load_holiday_calendar(year)
            )

        return calendars[year]

    # Baslangic yilinin takvimini yukle.
    calendar_for(current.year)

    if remaining == timedelta(0):

        return {
            "estimated_finish": current.isoformat(),
            "completion_hours": completion_hours,
            "timezone": "Europe/Istanbul",
            "working_hours": "09:00-18:00",
            "holiday_calendar": f"TR-{current.year}",
            "holiday_calendars": [
                f"TR-{current.year}"
            ],
            "skipped_holidays": [],
            "partial_holidays": [],
            "note": (
                "Tahmini sonuc; hafta ici calisma "
                "saatleri ve resmi tatiller dikkate "
                "alinmistir."
            ),
        }

    # ==================================================
    # WORKING TIME LOOP
    # ==================================================

    while remaining > timedelta(0):

        calendar = calendar_for(
            current.year
        )

        day_key = current.date().isoformat()

        # ----------------------------------------------
        # WEEKEND
        # ----------------------------------------------

        if current.weekday() >= 5:

            current = datetime.combine(
                current.date() + timedelta(days=1),
                WORK_START,
                tzinfo=TIMEZONE,
            )

            continue

        # ----------------------------------------------
        # FULL HOLIDAY
        # ----------------------------------------------

        full_name = calendar[
            "full_days"
        ].get(day_key)

        if full_name:

            if day_key not in seen_full:

                skipped_holidays.append({
                    "date": day_key,
                    "name": full_name,
                })

                seen_full.add(day_key)

            current = datetime.combine(
                current.date() + timedelta(days=1),
                WORK_START,
                tzinfo=TIMEZONE,
            )

            continue

        # ----------------------------------------------
        # HALF HOLIDAY
        # ----------------------------------------------

        cutoff_value = calendar[
            "half_days"
        ].get(day_key)

        day_end_time = WORK_END

        if cutoff_value:

            day_end_time = time.fromisoformat(
                cutoff_value
            )

            if day_key not in seen_partial:

                partial_holidays.append(
                    day_key
                )

                seen_partial.add(day_key)

        # ----------------------------------------------
        # WORKING HOURS
        # ----------------------------------------------

        day_start = datetime.combine(
            current.date(),
            WORK_START,
            tzinfo=TIMEZONE,
        )

        day_end = datetime.combine(
            current.date(),
            day_end_time,
            tzinfo=TIMEZONE,
        )

        if current < day_start:
            current = day_start

        if current >= day_end:

            current = datetime.combine(
                current.date() + timedelta(days=1),
                WORK_START,
                tzinfo=TIMEZONE,
            )

            continue

        available = day_end - current

        consumed = min(
            available,
            remaining,
        )

        current += consumed
        remaining -= consumed

    # ==================================================
    # RESULT
    # ==================================================

    loaded_years = sorted(
        calendars.keys()
    )

    if len(loaded_years) == 1:

        holiday_calendar = (
            f"TR-{loaded_years[0]}"
        )

    else:

        holiday_calendar = ",".join(
            f"TR-{year}"
            for year in loaded_years
        )

    return {
        "estimated_finish": current.isoformat(),
        "completion_hours": completion_hours,
        "timezone": "Europe/Istanbul",
        "working_hours": "09:00-18:00",
        "holiday_calendar": holiday_calendar,
        "holiday_calendars": [
            f"TR-{year}"
            for year in loaded_years
        ],
        "skipped_holidays": skipped_holidays,
        "partial_holidays": partial_holidays,
        "note": (
            "Tahmini sonuc; hafta ici calisma "
            "saatleri ve resmi tatiller dikkate "
            "alinmistir. Retry ve operasyonel "
            "kesintiler bu hesaplamaya dahil degildir."
        ),
    }
