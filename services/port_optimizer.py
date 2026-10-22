
from services.capacity_engine import calculate_capacity


def optimize_port_allocation(
    campaigns: list[dict],
    total_ports: int = 30,
) -> dict:
    """Kampanyalarin maksimum tamamlanma suresini azaltir."""

    if not campaigns:
        raise ValueError("En az bir kampanya gerekli.")

    if total_ports <= 0:
        raise ValueError("Toplam port pozitif olmali.")

    active = [
        c for c in campaigns
        if c["total_records"] > c["completed_records"]
    ]

    if not active:
        return {
            "total_ports": total_ports,
            "allocation": [],
            "estimated_finish_hours": 0,
        }

    if total_ports < len(active):
        raise ValueError(
            "Aktif her kampanyaya en az bir port gerekli."
        )

    if len({c["id"] for c in active}) != len(active):
        raise ValueError("Kampanya ID'leri benzersiz olmali.")

    allocation = {c["id"]: 1 for c in active}

    def hours(campaign, ports):
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
        )

        return result["completion_hours"]

    # Her adimda en uzun sureye sahip kampanyaya
    # bir ek port ver.
    for _ in range(total_ports - len(active)):
        slowest = max(
            active,
            key=lambda c: (
                hours(c, allocation[c["id"]]),
                -c["id"],
            ),
        )
        allocation[slowest["id"]] += 1

    results = []

    for campaign in active:
        ports = allocation[campaign["id"]]

        results.append({
            "campaign_id": campaign["id"],
            "campaign_name": campaign["name"],
            "original_ports": campaign["ports"],
            "recommended_ports": ports,
            "estimated_hours": hours(campaign, ports),
        })

    return {
        "total_ports": total_ports,
        "allocated_ports": sum(allocation.values()),
        "estimated_finish_hours": max(
            row["estimated_hours"] for row in results
        ),
        "allocation": results,
        "note": (
            "Portlarin kesintisiz kullanildigi, "
            "kampanyalarin paralel calistigi varsayilir. "
            "Retry ve calisma saatleri dahil degildir."
        ),
    }
