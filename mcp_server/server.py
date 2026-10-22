
import sqlite3
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from database.init_db import DB_PATH
from services.capacity_engine import calculate_capacity
from services.retry_simulator import simulate_retries
from services.port_optimizer import optimize_port_allocation
from services.schedule_planner import estimate_finish_datetime


mcp = FastMCP("CallCenterOptimizer")


def load_campaign(campaign_id: int) -> dict:
    """Kampanyanin mevcut durumunu SQLite'dan okur."""

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()

    if row is None:
        raise ValueError(
            f"Kampanya bulunamadi: {campaign_id}"
        )

    return dict(row)


def build_capacity(
    campaign: dict,
    ports: int,
    target_hours: float = 4,
) -> dict:
    """Kampanya icin kapasite sonucunu olusturur."""

    remaining = (
        campaign["total_records"]
        - campaign["completed_records"]
    )

    result = calculate_capacity(
        remaining_records=remaining,
        ports=ports,
        answer_rate=campaign["answer_rate"],
        avg_ivr_seconds=campaign["avg_ivr_seconds"],
        avg_no_answer_seconds=campaign["avg_no_answer_seconds"],
        overhead_seconds=campaign["overhead_seconds"],
        target_hours=target_hours,
    )

    return {
        "campaign_id": campaign["id"],
        "campaign_name": campaign["name"],
        **result,
    }


def build_finish_time(
    campaign: dict,
    start_datetime: str,
    ports: int,
    calculation_type: str,
) -> dict:
    """Kapasite ve calisma takvimini birlikte hesaplar."""

    start = datetime.fromisoformat(start_datetime)

    capacity = build_capacity(
        campaign=campaign,
        ports=ports,
    )

    schedule = estimate_finish_datetime(
        completion_hours=capacity["completion_hours"],
        start_datetime=start,
    )

    return {
        "campaign_id": campaign["id"],
        "campaign_name": campaign["name"],
        "calculation_type": calculation_type,
        "actual_ports": campaign["ports"],
        "calculation_ports": ports,
        "capacity": capacity,
        "schedule": schedule,
        "warning": (
            "Bu sonuc bir tahmindir. "
            "Veritabanindaki port sayisi degistirilmemistir."
        ),
    }


@mcp.tool()
def get_campaign_stats(campaign_id: int) -> dict:
    """Kampanyanin mevcut verilerini SQLite'dan getirir."""

    campaign = load_campaign(campaign_id)

    campaign["remaining_records"] = (
        campaign["total_records"]
        - campaign["completed_records"]
    )

    return campaign


@mcp.tool()
def calculate_campaign_capacity(
    campaign_id: int,
    ports: int = 0,
    target_hours: float = 4,
) -> dict:
    """Kampanya kapasitesini hesaplar.

    ports=0 ise veritabanindaki mevcut port kullanilir.
    ports>0 ise alternatif port senaryosu hesaplanir.
    """

    if ports < 0:
        raise ValueError("Port negatif olamaz.")

    campaign = load_campaign(campaign_id)

    selected_ports = (
        campaign["ports"]
        if ports == 0
        else ports
    )

    result = build_capacity(
        campaign,
        selected_ports,
        target_hours,
    )

    return {
        **result,
        "actual_ports": campaign["ports"],
        "calculation_type": (
            "ACTUAL" if ports == 0 else "SIMULATION"
        ),
    }


@mcp.tool()
def simulate_port_scenarios(
    campaign_id: int,
    port_options: list[int],
) -> list[dict]:
    """Alternatif port sayilarini karsilastirir."""

    if not port_options or len(port_options) > 20:
        raise ValueError(
            "1-20 arasinda port senaryosu gerekli."
        )

    if any(port <= 0 for port in port_options):
        raise ValueError(
            "Tum portlar pozitif olmali."
        )

    campaign = load_campaign(campaign_id)

    results = []

    for ports in port_options:
        capacity = build_capacity(campaign, ports)

        results.append({
            "ports": ports,
            "calls_per_hour": capacity["calls_per_hour"],
            "completion_hours": capacity["completion_hours"],
            "calculation_type": "SIMULATION",
        })

    return results


@mcp.tool()
def simulate_campaign_retries(
    campaign_id: int,
    max_attempts: int = 3,
) -> dict:
    """Kampanyanin beklenen retry arama yukunu hesaplar.

    Cevaplanma orani SQLite'dan okunur.
    Tum basarisiz kayitlar yeniden denenir.
    max_attempts ilk arama dahil toplam deneme sayisidir.
    """

    campaign = load_campaign(campaign_id)

    remaining = (
        campaign["total_records"]
        - campaign["completed_records"]
    )

    result = simulate_retries(
        records=remaining,
        answer_rate=campaign["answer_rate"],
        max_attempts=max_attempts,
        retry_fraction=1.0,
    )

    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign["name"],
        **result,
    }


@mcp.tool()
def optimize_campaign_ports(
    campaign_ids: list[int],
    total_ports: int = 30,
) -> dict:
    """Birden fazla kampanya icin port dagilimi onerir.

    Sonuc yalnizca oneridir; veritabani degismez.
    """

    if not campaign_ids:
        raise ValueError("Kampanya listesi bos olamaz.")

    if len(set(campaign_ids)) != len(campaign_ids):
        raise ValueError("Tekrarlanan kampanya ID'si var.")

    campaigns = [
        load_campaign(campaign_id)
        for campaign_id in campaign_ids
    ]

    result = optimize_port_allocation(
        campaigns=campaigns,
        total_ports=total_ports,
    )

    return {
        "calculation_type": "PROPOSAL",
        "database_updated": False,
        **result,
    }


@mcp.tool()
def calculate_campaign_finish_time(
    campaign_id: int,
    start_datetime: str,
) -> dict:
    """MEVCUT portlarla tahmini bitis zamanini hesaplar.

    Port sayisi yalnizca SQLite veritabanindan okunur.
    Onceki optimizasyon onerileri dikkate alinmaz.

    Baslangic ISO 8601 ve timezone icermelidir.
    Ornek: 2026-10-26T16:00:00+03:00
    """

    campaign = load_campaign(campaign_id)

    return build_finish_time(
        campaign=campaign,
        start_datetime=start_datetime,
        ports=campaign["ports"],
        calculation_type="ACTUAL",
    )


@mcp.tool()
def simulate_campaign_finish_time(
    campaign_id: int,
    start_datetime: str,
    scenario_ports: int,
) -> dict:
    """ALTERNATIF portlarla bitis zamani simulasyonu yapar.

    Yalnizca kullanici acikca farkli port sayisi
    istediginde kullanilmalidir.

    scenario_ports zorunludur ve pozitif olmalidir.
    SQLite'daki mevcut port sayisi degistirilmez.
    """

    if scenario_ports <= 0:
        raise ValueError(
            "Senaryo port sayisi pozitif olmali."
        )

    campaign = load_campaign(campaign_id)

    return build_finish_time(
        campaign=campaign,
        start_datetime=start_datetime,
        ports=scenario_ports,
        calculation_type="SIMULATION",
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
