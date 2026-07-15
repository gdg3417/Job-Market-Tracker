from __future__ import annotations

from src.jobs_write_contract import audit_write_contract, load_allowlist


def test_jobs_write_allowlist_entries_are_complete() -> None:
    entries = load_allowlist()
    assert entries
    assert all(entry["reason"].strip() for entry in entries)
    assert all(entry["guard"].strip() for entry in entries)


def test_every_direct_sheet_write_is_reviewed_and_allowlisted() -> None:
    result = audit_write_contract()
    assert result["status"] == "healthy", result["unallowlisted"]
    assert result["unallowlisted_count"] == 0
