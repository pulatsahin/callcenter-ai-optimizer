
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "campaigns.db"

SAMPLE_CAMPAIGNS = [
    (101, "IVR Tahsilat", 10000, 3000, 5, 40, 20, 30, 2),
    (102, "Musteri Bilgilendirme", 15000, 5000, 8, 55, 25, 25, 2),
    (103, "Kampanya Tanitim", 20000, 8000, 7, 30, 35, 30, 2),
]


def initialize_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                total_records INTEGER NOT NULL,
                completed_records INTEGER NOT NULL,
                ports INTEGER NOT NULL,
                answer_rate REAL NOT NULL,
                avg_ivr_seconds REAL NOT NULL,
                avg_no_answer_seconds REAL NOT NULL,
                overhead_seconds REAL NOT NULL
            )
        """)

        conn.executemany("""
            INSERT OR IGNORE INTO campaigns
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, SAMPLE_CAMPAIGNS)

    print("Veritabani hazir:", DB_PATH)


if __name__ == "__main__":
    initialize_database()
