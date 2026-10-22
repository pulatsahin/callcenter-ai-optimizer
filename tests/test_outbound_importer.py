import csv
from pathlib import Path
import pytest
from services.outbound_importer import OutboundImportError, load_outbound_csv, summarize_outbound

HEADER = ["campaign_id", "attempt_id", "timestamp", "disposition", "duration_seconds", "port_id"]


def write_csv(tmp_path, rows, header=HEADER):
    path = tmp_path / "attempts.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def sample_rows():
    return [
        [101, "a1", "2026-10-26T09:00:00+03:00", "ANSWERED", 20, "p1"],
        [101, "a2", "2026-10-26T09:01:00+03:00", "NO_ANSWER", 30, "p2"],
        [102, "a1", "2026-10-26T09:02:00+03:00", "BUSY", 10, "p1"],
    ]


def test_load_and_normalize(tmp_path):
    rows = load_outbound_csv(write_csv(tmp_path, sample_rows()))
    assert len(rows) == 3
    assert rows[0]["campaign_id"] == 101
    assert rows[0]["duration_seconds"] == 20.0


def test_summary(tmp_path):
    summary = summarize_outbound(load_outbound_csv(write_csv(tmp_path, sample_rows())))
    assert summary[0]["attempts"] == 2
    assert summary[0]["answer_rate_pct"] == 50
    assert summary[0]["observed_ports"] == 2
    assert summary[1]["busy_attempts"] == 1


def test_missing_header(tmp_path):
    with pytest.raises(OutboundImportError, match="Missing columns"):
        load_outbound_csv(write_csv(tmp_path, [[101]], header=["campaign_id"]))


def test_duplicate_attempt(tmp_path):
    rows = sample_rows()
    rows[1][1] = "a1"
    with pytest.raises(OutboundImportError, match="duplicate"):
        load_outbound_csv(write_csv(tmp_path, rows))


def test_duplicate_attempt_across_campaigns_allowed(tmp_path):
    assert len(load_outbound_csv(write_csv(tmp_path, sample_rows()))) == 3


def test_invalid_disposition(tmp_path):
    rows = sample_rows()
    rows[0][3] = "UNKNOWN"
    with pytest.raises(OutboundImportError, match="unknown disposition"):
        load_outbound_csv(write_csv(tmp_path, rows))


def test_timezone_required(tmp_path):
    rows = sample_rows()
    rows[0][2] = "2026-10-26T09:00:00"
    with pytest.raises(OutboundImportError, match="UTC offset"):
        load_outbound_csv(write_csv(tmp_path, rows))


def test_negative_duration(tmp_path):
    rows = sample_rows()
    rows[0][4] = -1
    with pytest.raises(OutboundImportError, match="invalid campaign or duration"):
        load_outbound_csv(write_csv(tmp_path, rows))


def test_empty_file(tmp_path):
    with pytest.raises(OutboundImportError, match="no attempts"):
        load_outbound_csv(write_csv(tmp_path, []))


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_outbound_csv(tmp_path / "missing.csv")
