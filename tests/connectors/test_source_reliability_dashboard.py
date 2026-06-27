from __future__ import annotations

from src.source_reliability import SourceHealthState
from src.source_reliability_dashboard import build_source_reliability_dashboard_rows


class FakeSheetClient:
    def __init__(self, states):
        self.tables = {"Source_Health": [state.to_dict() for state in states]}

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables.get(worksheet_name, [])]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables.get(worksheet_name, []))]


def test_source_reliability_dashboard_renders_platform_health_rows():
    client = FakeSheetClient(
        [
            SourceHealthState(
                platform="greenhouse",
                attempt_count=3,
                success_count=2,
                failure_count=1,
                jobs_found=12,
                jobs_accepted=4,
                rate_limit_events=1,
                last_error_category="rate_limited",
            )
        ]
    )

    rows = build_source_reliability_dashboard_rows(client, generated_at="2026-06-27T12:00:00Z")

    assert rows[0] == ["ATS platform source reliability"]
    assert rows[2][0] == "Platform"
    assert rows[3][0] == "greenhouse"
    assert rows[3][2] == 3
    assert rows[3][6] == 4
    assert "rate_limited" in rows[3][11]


def test_source_reliability_dashboard_handles_empty_health_table():
    rows = build_source_reliability_dashboard_rows(FakeSheetClient([]), generated_at="2026-06-27T12:00:00Z")

    assert rows[3][0] == "No source reliability rows recorded yet"
