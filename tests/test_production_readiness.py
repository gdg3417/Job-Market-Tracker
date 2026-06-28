from __future__ import annotations

from src.models import JobPosting
from src.production_readiness import (
    LifecycleCadencePolicy,
    ReadinessThresholds,
    audit_missed_roles,
    binary_metric,
    build_alerts,
    build_lifecycle_history_record,
    build_metrics_from_workbook,
    build_readiness_run_record,
    closure_decision_from_observation,
    detect_reopened,
    evaluate_gold_standard_cases,
    evaluate_readiness,
    lifecycle_due_rows,
    lifecycle_interval_days,
    load_gold_standard_cases,
)


def make_job(**overrides):
    values = {
        "job_key": "acme-director-strategy-dallas",
        "company": "Acme",
        "title": "Director, Strategy",
        "location": "Dallas, TX",
        "canonical_url": "https://acme.example/jobs/1",
        "first_seen_date": "2026-06-20",
        "last_seen_date": "2026-06-26",
        "status": "open",
        "potential_priority": "medium",
        "potential_priority_score": 60,
        "score_status": "partially_verified",
        "review_status": "not_reviewed",
        "application_status": "",
        "lifecycle_last_checked_at": "2026-06-26T12:00:00Z",
        "lifecycle_next_check_at": "",
    }
    values.update(overrides)
    return JobPosting.from_dict(values)


def test_priority_based_lifecycle_cadence():
    policy = LifecycleCadencePolicy()
    target_keys = {"acme"}

    high = make_job(potential_priority="high")
    target = make_job(potential_priority="medium")
    applied = make_job(review_status="applied", application_status="applied")
    reviewed = make_job(company="Other", review_status="watch")
    low = make_job(company="Other", potential_priority="low", score_status="provisional")
    closed = make_job(status="confirmed_closed")

    assert lifecycle_interval_days(high, target_company_keys=set(), policy=policy) == (1, "high_potential_daily")
    assert lifecycle_interval_days(target, target_company_keys=target_keys, policy=policy) == (1, "target_company_daily")
    assert lifecycle_interval_days(applied, target_company_keys=set(), policy=policy) == (1, "interested_or_applied_daily")
    assert lifecycle_interval_days(reviewed, target_company_keys=set(), policy=policy) == (7, "reviewed_weekly")
    assert lifecycle_interval_days(low, target_company_keys=set(), policy=policy) == (14, "low_priority_provisional_lower_frequency")
    assert lifecycle_interval_days(closed, target_company_keys=set(), policy=policy) == (30, "closed_limited_confirmation")


def test_lifecycle_due_rows_prioritizes_interested_then_high_potential():
    rows = lifecycle_due_rows(
        [
            make_job(job_key="low", potential_priority="low", score_status="provisional", lifecycle_last_checked_at=""),
            make_job(job_key="high", potential_priority="high", lifecycle_last_checked_at=""),
            make_job(job_key="applied", review_status="applied", application_status="applied", lifecycle_last_checked_at=""),
        ],
        now="2026-06-27T12:00:00Z",
    )

    assert [row["job_key"] for row in rows] == ["applied", "high", "low"]


def test_explicit_authoritative_closure_is_allowed():
    decision = closure_decision_from_observation({"authoritative": True, "explicitly_closed": True})

    assert decision.may_close is True
    assert decision.next_status == "confirmed_closed"
    assert decision.confidence == "confirmed"


def test_authoritative_expiration_closure_is_allowed():
    decision = closure_decision_from_observation(
        {"authoritative": True, "valid_through": "2026-06-01"},
        checked_at="2026-06-27",
    )

    assert decision.may_close is True
    assert decision.next_status == "expired"


def test_repeated_authoritative_absence_is_required_for_removal_closure():
    first = closure_decision_from_observation(
        {"authoritative": True, "removed": True, "consecutive_authoritative_absence_count": 1}
    )
    second = closure_decision_from_observation(
        {"authoritative": True, "removed": True, "consecutive_authoritative_absence_count": 2}
    )

    assert first.may_close is False
    assert first.next_status == "likely_closed"
    assert second.may_close is True
    assert second.next_status == "confirmed_closed"


def test_temporary_timeout_cannot_close_posting():
    decision = closure_decision_from_observation({"authoritative": True, "error_type": "timeout"})

    assert decision.may_close is False
    assert decision.safeguard_triggered is True


def test_blocked_source_cannot_close_posting():
    decision = closure_decision_from_observation({"authoritative": True, "error_type": "source_blocked"})

    assert decision.may_close is False
    assert decision.safeguard_triggered is True


def test_parser_failure_cannot_close_posting():
    decision = closure_decision_from_observation({"authoritative": True, "error_type": "parser_failure"})

    assert decision.may_close is False
    assert decision.safeguard_triggered is True


def test_external_search_miss_cannot_close_posting():
    decision = closure_decision_from_observation({"external_search_miss": True})

    assert decision.may_close is False
    assert decision.safeguard_triggered is True


def test_reopened_detection_requires_authoritative_listed_observation():
    assert detect_reopened("confirmed_closed", {"authoritative": True, "listed": True}) is True
    assert detect_reopened("confirmed_closed", {"authoritative": False, "listed": True}) is False
    assert detect_reopened("open", {"authoritative": True, "listed": True}) is False


def test_lifecycle_history_preserves_closure_and_reopen_context():
    job = make_job(status="confirmed_closed", closed_date="2026-06-24")
    decision = closure_decision_from_observation({"authoritative": True, "listed": True}, previous_status="confirmed_closed")
    record = build_lifecycle_history_record(
        job,
        {"authoritative": True, "listed": True, "retrieval_success": True, "source_url": "https://acme.example/jobs/1"},
        decision,
        previous_status="confirmed_closed",
        observed_at="2026-06-27T12:00:00Z",
    )

    assert record.previous_status == "confirmed_closed"
    assert record.reopened_date == "2026-06-27"
    assert record.last_successful_retrieval == "2026-06-27T12:00:00Z"


def test_binary_precision_and_recall_calculation():
    metric = binary_metric(
        [
            {"expected": {"positive": True}, "actual": {"positive": True}},
            {"expected": {"positive": False}, "actual": {"positive": True}},
            {"expected": {"positive": True}, "actual": {"positive": False}},
            {"expected": {"positive": False}, "actual": {"positive": False}},
        ],
        "expected.positive",
        "actual.positive",
    )

    assert metric.precision == 0.5
    assert metric.recall == 0.5
    assert metric.false_positive_rate == 0.5


def test_gold_standard_evaluation_metrics_are_generated():
    cases = load_gold_standard_cases("data/regression/sprint38_gold_standard_jobs.json")
    metrics = evaluate_gold_standard_cases(cases)

    assert metrics["case_count"] >= 18
    assert metrics["ingestion_precision"] == 1.0
    assert metrics["high_potential_recall"] == 1.0
    assert metrics["false_closure_rate"] == 0.0
    assert metrics["regression_pass_rate"] == 1.0


def test_missed_role_audit_flags_expected_accepts_not_in_jobs():
    audit = audit_missed_roles(
        [
            {"company": "Acme", "title": "Director Strategy", "location": "Dallas", "source_job_id": "1", "expected_accept": True},
            {"company": "Beta", "title": "Finance Manager", "location": "Plano", "source_job_id": "2", "expected_accept": True, "rejection_reason": "generic job board"},
            {"company": "Gamma Inc", "title": "Strategy Lead", "location": "Remote", "source_job_id": "3", "expected_accept": True, "expected_company_normalized": "Gamma", "priority": "low", "expected_priority": "high"},
        ],
        [
            {"company": "Acme", "title": "Director Strategy", "location": "Dallas", "source_job_id": "1"},
        ],
    )

    result = audit.to_dict()
    assert len(result["missed_jobs"]) == 2
    assert len(result["incorrectly_rejected_jobs"]) == 1
    assert len(result["incorrect_company_normalization"]) == 1
    assert len(result["incorrect_priority_classification"]) == 1


def ready_metrics(**overrides):
    metrics = {
        "daily_workflow_age_hours": 2,
        "schema_valid": True,
        "gmail_backlog": 0,
        "enrichment_backlog": 0,
        "high_priority_sla_breaches": 0,
        "resolution_success_rate": 0.9,
        "verification_conversion_rate": 0.8,
        "source_failure_rate": 0.0,
        "false_closure_count": 0,
        "regression_pass_rate": 1.0,
        "dashboard_refresh_success": True,
        "digest_refresh_success": True,
    }
    metrics.update(overrides)
    return metrics


def test_readiness_gate_critical_failures_override_aggregate_scores():
    readiness = evaluate_readiness(ready_metrics(schema_valid=False))

    assert readiness.classification == "not_ready"
    assert any(gate.name == "schema_validity" and gate.critical and gate.status == "fail" for gate in readiness.gates)


def test_readiness_warnings_do_not_become_ready():
    readiness = evaluate_readiness(ready_metrics(source_failure_rate=0.4), thresholds=ReadinessThresholds(source_failure_rate_max=0.25))

    assert readiness.classification == "ready_with_warnings"
    assert any(gate.name == "source_failure_rate" and gate.status == "warn" for gate in readiness.gates)


def test_warning_alerts_keep_warning_severity_when_overall_not_ready():
    readiness = evaluate_readiness(ready_metrics(daily_workflow_age_hours=99, verification_conversion_rate=0.0))
    alerts = build_alerts(readiness, created_at="2026-06-27T12:00:00Z")
    severity_by_category = {alert.category: alert.severity for alert in alerts}

    assert readiness.classification == "not_ready"
    assert severity_by_category["daily_workflow_freshness"] == "critical"
    assert severity_by_category["verification_conversion"] == "warning"


def test_alert_generation_is_low_noise_and_deduplicated():
    readiness = evaluate_readiness(ready_metrics(daily_workflow_age_hours=99, source_failure_rate=0.4))
    alerts = build_alerts(readiness, created_at="2026-06-27T12:00:00Z")
    repeated = build_alerts(readiness, prior_alert_ids={alert.alert_id for alert in alerts}, created_at="2026-06-27T12:00:00Z")

    assert alerts
    assert any(alert.category == "daily_workflow_freshness" for alert in alerts)
    assert repeated == []


def test_readiness_run_record_is_idempotent_for_same_timestamp():
    readiness = evaluate_readiness(ready_metrics())
    first = build_readiness_run_record(readiness, now="2026-06-27T12:00:00Z")
    second = build_readiness_run_record(readiness, now="2026-06-27T12:00:00Z")

    assert first["run_id"] == second["run_id"]
    assert first["run_type"] == "sprint_38_production_readiness"
    assert first["records_failed"] == 0


def test_build_metrics_from_workbook_flags_stale_high_priority_jobs_without_blockers():
    jobs = [
        make_job(potential_priority="high", score_status="provisional", first_seen_date="2026-06-24"),
        make_job(job_key="verified", potential_priority="high", score_status="verified", first_seen_date="2026-06-26"),
    ]
    metrics = build_metrics_from_workbook(
        jobs=jobs,
        runs=[
            {"run_type": "daily production", "status": "success", "finished_at": "2026-06-27T10:00:00Z"},
            {"run_type": "workflow_validation", "status": "success", "finished_at": "2026-06-27T10:00:00Z"},
            {"run_type": "dashboard_digest", "status": "success", "finished_at": "2026-06-27T10:00:00Z"},
        ],
        queue_rows=[{"status": "pending"}],
        now="2026-06-27T12:00:00Z",
        regression_metrics={"regression_pass_rate": 1.0},
    )

    assert metrics["daily_workflow_age_hours"] == 2.0
    assert metrics["schema_valid"] is True
    assert metrics["enrichment_backlog"] == 1
    assert metrics["high_priority_sla_breaches"] == 1


def test_build_metrics_from_workbook_does_not_count_blocked_high_priority_jobs_as_breaches():
    jobs = [
        make_job(job_key="partial", potential_priority="high", score_status="partially_verified", first_seen_date="2026-06-24"),
        make_job(job_key="failure", potential_priority="high", score_status="provisional", enrichment_status="not_found", first_seen_date="2026-06-24"),
        make_job(job_key="active", potential_priority="high", score_status="provisional", enrichment_status="pending", first_seen_date="2026-06-24"),
    ]
    metrics = build_metrics_from_workbook(
        jobs=jobs,
        runs=[
            {"run_type": "daily production", "status": "success", "finished_at": "2026-06-27T10:00:00Z"},
            {"run_type": "workflow_validation", "status": "success", "finished_at": "2026-06-27T10:00:00Z"},
            {"run_type": "dashboard_digest", "status": "success", "finished_at": "2026-06-27T10:00:00Z"},
        ],
        now="2026-06-27T12:00:00Z",
        regression_metrics={"regression_pass_rate": 1.0},
    )

    assert metrics["high_priority_unresolved_aged"] == 3
    assert metrics["high_priority_blocked"] == 2
    assert metrics["high_priority_active_enrichment"] == 1
    assert metrics["high_priority_sla_breaches"] == 0
