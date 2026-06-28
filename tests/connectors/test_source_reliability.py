from __future__ import annotations

from src.connectors.models import ConnectorError, ConnectorResult
from src.source_reliability import (
    SourceHealthState,
    SourceReliabilityThresholds,
    apply_source_observation,
    observe_connector_result,
    platform_health_metrics,
    should_skip_source,
)

NOW = "2026-06-27T12:00:00Z"


class FakeSheetClient:
    def __init__(self):
        self.tables = {"Source_Health": []}

    def read_records(self, worksheet_name):
        return [dict(row) for row in self.tables[worksheet_name]]

    def read_records_with_row_numbers(self, worksheet_name):
        return [(index + 2, dict(row)) for index, row in enumerate(self.tables[worksheet_name])]

    def append_record(self, worksheet_name, record):
        self.tables[worksheet_name].append(dict(record))

    def update_record(self, worksheet_name, row_number, record):
        self.tables[worksheet_name][row_number - 2] = dict(record)


def result(status="success", jobs=(), message="", http_status=None):
    return ConnectorResult(
        platform="greenhouse",
        company_id="example",
        company_name="Example Co",
        status=status,
        jobs=tuple(jobs),
        error=ConnectorError(status, message, http_status=http_status) if status not in {"success", "no_matching_jobs"} else None,
        requests=1,
        response_time_ms=100,
        source_url="https://boards.greenhouse.io/example",
    )


def test_successful_observation_resets_failures_and_records_metrics():
    prior = SourceHealthState(
        company_id="example",
        company_name="Example Co",
        platform="greenhouse",
        source_url="https://boards.greenhouse.io/example",
        consecutive_failures=2,
        attempt_count=2,
        failure_count=2,
        source_state="watch",
    )

    updated = apply_source_observation(prior, result("success", jobs=(object(),)), observed_at=NOW, jobs_accepted=1)

    assert updated.source_state == "healthy"
    assert updated.consecutive_failures == 0
    assert updated.last_successful_at == NOW
    assert updated.jobs_found == 1
    assert updated.jobs_accepted == 1
    assert updated.success_rate_percent == 33


def test_temporary_failures_pause_only_after_conservative_threshold():
    thresholds = SourceReliabilityThresholds(watch_consecutive_failures=2, pause_consecutive_failures=3)
    prior = SourceHealthState(company_id="example", company_name="Example Co", platform="greenhouse")

    first = apply_source_observation(prior, result("temporary_server_failure", message="server error"), observed_at=NOW, thresholds=thresholds)
    second = apply_source_observation(first, result("temporary_server_failure", message="server error"), observed_at=NOW, thresholds=thresholds)
    third = apply_source_observation(second, result("temporary_server_failure", message="server error"), observed_at=NOW, thresholds=thresholds)

    assert first.source_state == "healthy"
    assert second.source_state == "watch"
    assert third.source_state == "temporarily_paused"
    assert should_skip_source(third)[0] is True


def test_invalid_configuration_requires_manual_review_without_disabling():
    updated = apply_source_observation(
        SourceHealthState(company_id="example", company_name="Example Co", platform="greenhouse"),
        result("invalid_configuration", message="Missing board token"),
        observed_at=NOW,
    )

    assert updated.source_state == "manual_review_required"
    assert updated.configuration_valid is False
    assert updated.manual_review_reason == "Missing board token"
    assert updated.source_state != "disabled"


def test_empty_success_can_move_source_to_watch_without_failure():
    thresholds = SourceReliabilityThresholds(empty_success_watch_count=2)
    prior = SourceHealthState(company_id="example", company_name="Example Co", platform="greenhouse")

    first = apply_source_observation(prior, result("no_matching_jobs"), observed_at=NOW, thresholds=thresholds)
    second = apply_source_observation(first, result("no_matching_jobs"), observed_at=NOW, thresholds=thresholds)

    assert second.source_state == "watch"
    assert second.failure_count == 0
    assert second.empty_success_count == 2


def test_observe_connector_result_is_idempotent_for_current_source_row():
    client = FakeSheetClient()

    observe_connector_result(client, result("success"), observed_at=NOW)
    observe_connector_result(client, result("temporary_server_failure", message="server error"), observed_at="2026-06-27T13:00:00Z")

    assert len(client.tables["Source_Health"]) == 1
    assert client.tables["Source_Health"][0]["attempt_count"] == 2
    assert client.tables["Source_Health"][0]["failure_count"] == 1


def test_platform_health_metrics_groups_by_platform():
    states = [
        SourceHealthState(platform="greenhouse", attempt_count=3, success_count=2, failure_count=1, jobs_found=12, jobs_accepted=5),
        SourceHealthState(platform="greenhouse", source_state="temporarily_paused", attempt_count=2, success_count=0, failure_count=2, last_error_category="rate_limited"),
        SourceHealthState(platform="lever", attempt_count=1, success_count=1, failure_count=0),
    ]

    metrics = platform_health_metrics(states)

    assert metrics["greenhouse"]["requests"] == 5
    assert metrics["greenhouse"]["paused_sources"] == 1
    assert metrics["greenhouse"]["failures_by_category"]["rate_limited"] == 1
    assert metrics["lever"]["successes"] == 1
