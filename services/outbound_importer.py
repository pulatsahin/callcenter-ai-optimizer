"""Genesys Engage outbound CSV normalization and validation (no database writes).

The CSV schema is a project-defined interchange format, NOT a native Genesys schema.
Do not include customer identifiers, phone numbers, or other personal data.
"""

import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REQUIRED_COLUMNS = (
    "campaign_id", "attempt_id", "timestamp", "disposition", "duration_seconds", "port_id"
)
DISPOSITIONS = {"ANSWERED", "NO_ANSWER", "BUSY", "FAILED"}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000


class OutboundImportError(ValueError):
    """Raised for invalid or unsupported outbound import data."""


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OutboundImportError(f"Invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OutboundImportError("timestamp must include UTC offset")
    return parsed


def load_outbound_csv(path: str | Path) -> list[dict]:
    """Validate and normalize an outbound attempts CSV. Never modifies the source."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise OutboundImportError("CSV file exceeds 10 MB limit")
    records = []
    seen = set()
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise OutboundImportError("CSV header is missing")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise OutboundImportError("Duplicate CSV column names")
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames)
        if missing:
            raise OutboundImportError(f"Missing columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            if len(records) >= MAX_ROWS:
                raise OutboundImportError("CSV exceeds 100000 rows")
            if None in row or any(row[key] is None for key in REQUIRED_COLUMNS):
                raise OutboundImportError(f"Line {line_number}: malformed CSV row")
            try:
                campaign_id = int(row["campaign_id"].strip())
                duration = float(row["duration_seconds"].strip())
                attempt_id = row["attempt_id"].strip()
                port_id = row["port_id"].strip()
                disposition = row["disposition"].strip().upper()
                timestamp = _timestamp(row["timestamp"].strip())
            except (ValueError, TypeError) as exc:
                raise OutboundImportError(f"Line {line_number}: {exc}") from exc
            if campaign_id <= 0 or duration < 0 or duration > 86400:
                raise OutboundImportError(f"Line {line_number}: invalid campaign or duration")
            if not attempt_id or not port_id:
                raise OutboundImportError(f"Line {line_number}: attempt_id and port_id required")
            if disposition not in DISPOSITIONS:
                raise OutboundImportError(f"Line {line_number}: unknown disposition {disposition!r}")
            key = (campaign_id, attempt_id)
            if key in seen:
                raise OutboundImportError(f"Line {line_number}: duplicate attempt_id within campaign")
            seen.add(key)
            records.append({
                "campaign_id": campaign_id,
                "attempt_id": attempt_id,
                "timestamp": timestamp,
                "disposition": disposition,
                "duration_seconds": duration,
                "port_id": port_id,
            })
    if not records:
        raise OutboundImportError("CSV contains no attempts")
    return records


def summarize_outbound(records: list[dict]) -> list[dict]:
    """Summarize observed attempts per campaign, without inferring unique customers."""
    groups = defaultdict(list)
    for record in records:
        groups[record["campaign_id"]].append(record)
    results = []
    for campaign_id, rows in sorted(groups.items()):
        counts = Counter(r["disposition"] for r in rows)
        total = len(rows)
        results.append({
            "campaign_id": campaign_id,
            "attempts": total,
            "answered_attempts": counts["ANSWERED"],
            "answer_rate_pct": round(100 * counts["ANSWERED"] / total, 2),
            "no_answer_attempts": counts["NO_ANSWER"],
            "busy_attempts": counts["BUSY"],
            "failed_attempts": counts["FAILED"],
            "average_duration_seconds": round(sum(r["duration_seconds"] for r in rows) / total, 2),
            "observed_ports": len({r["port_id"] for r in rows}),
            "first_attempt": min(r["timestamp"] for r in rows).isoformat(),
            "last_attempt": max(r["timestamp"] for r in rows).isoformat(),
        })
    return results
