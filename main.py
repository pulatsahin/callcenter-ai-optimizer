
import sqlite3

from database.init_db import DB_PATH, initialize_database
from services.capacity_engine import calculate_capacity


def main():
    initialize_database()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        campaign = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?",
            (101,)
        ).fetchone()

    if campaign is None:
        print("Kampanya bulunamadi.")
        return

    remaining = (
        campaign["total_records"]
        - campaign["completed_records"]
    )

    result = calculate_capacity(
        remaining_records=remaining,
        ports=campaign["ports"],
        answer_rate=campaign["answer_rate"],
        avg_ivr_seconds=campaign["avg_ivr_seconds"],
        avg_no_answer_seconds=campaign["avg_no_answer_seconds"],
        overhead_seconds=campaign["overhead_seconds"],
    )

    print("\n=== CALLCENTER AI OPTIMIZER ===")
    print("Kampanya:", campaign["name"])
    print("Kalan kayit:", result["remaining_records"])
    print("Port sayisi:", result["ports"])
    print("Ortalama port suresi:", result["avg_port_seconds"])
    print("Saatlik arama:", result["calls_per_hour"])
    print("Tahmini tamamlanma:", result["completion_hours"], "saat")
    print("4 saat hedefi icin port:", result["required_ports"])


if __name__ == "__main__":
    main()
