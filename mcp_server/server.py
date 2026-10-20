
import sqlite3

from mcp.server.fastmcp import FastMCP

from database.init_db import DB_PATH
from services.capacity_engine import calculate_capacity


mcp = FastMCP("CallCenterOptimizer")


def load_campaign(campaign_id: int) -> dict:
    """SQLite veritabanından kampanya bilgilerini getirir."""

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        row = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?",
            (campaign_id,)
        ).fetchone()

    if row is None:
        raise ValueError(f"Kampanya bulunamadi: {campaign_id}")

    return dict(row)


@mcp.tool()
def get_campaign_stats(campaign_id: int) -> dict:
    """Kampanya bilgilerini ve kalan kayıt sayısını getirir."""

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
    """Kampanyanın kapasitesini ve hedef süre için gereken portu hesaplar.

    ports=0 verilirse kampanyanın mevcut port sayısı kullanılır.
    """

    campaign = load_campaign(campaign_id)

    selected_ports = ports if ports > 0 else campaign["ports"]

    remaining = (
        campaign["total_records"]
        - campaign["completed_records"]
    )

    result = calculate_capacity(
        remaining_records=remaining,
        ports=selected_ports,
        answer_rate=campaign["answer_rate"],
        avg_ivr_seconds=campaign["avg_ivr_seconds"],
        avg_no_answer_seconds=campaign["avg_no_answer_seconds"],
        overhead_seconds=campaign["overhead_seconds"],
        target_hours=target_hours,
    )

    return {
        "campaign_id": campaign_id,
        "campaign_name": campaign["name"],
        **result,
    }


@mcp.tool()
def simulate_port_scenarios(
    campaign_id: int,
    port_options: list[int],
) -> list[dict]:
    """Farklı port sayılarının kampanya süresine etkisini karşılaştırır."""

    if not port_options or len(port_options) > 20:
        raise ValueError("1 ile 20 arasinda port senaryosu girilmeli.")

    if any(port <= 0 for port in port_options):
        raise ValueError("Tum port sayilari pozitif olmali.")

    campaign = load_campaign(campaign_id)

    remaining = (
        campaign["total_records"]
        - campaign["completed_records"]
    )

    results = []

    for ports in port_options:
        result = calculate_capacity(
            remaining_records=remaining,
            ports=ports,
            answer_rate=campaign["answer_rate"],
            avg_ivr_seconds=campaign["avg_ivr_seconds"],
            avg_no_answer_seconds=campaign["avg_no_answer_seconds"],
            overhead_seconds=campaign["overhead_seconds"],
        )

        results.append({
            "ports": ports,
            "calls_per_hour": result["calls_per_hour"],
            "completion_hours": result["completion_hours"],
        })

    return results


if __name__ == "__main__":
    mcp.run(transport="stdio")
