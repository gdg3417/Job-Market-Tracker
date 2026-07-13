from src.verification_health import HealthThresholds, calculate_verification_health
from tests.verification_health_helpers import AS_OF, job, successful_daily_run


def test_health_is_blocked_when_latest_workflow_is_failed():
    result = calculate_verification_health(
        jobs=[job("one")], job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run(status="failed", finished_at="2026-06-26T17:00:00Z")],
        as_of=AS_OF,
    )
    workflow = next(item for item in result.health_components if item.component == "workflow_reliability")
    assert result.overall_classification == "Blocked"
    assert result.critical_overrides
    assert workflow.critical is True
    assert workflow.score == 0


def test_zero_queue_does_not_make_unresolved_verification_healthy():
    result = calculate_verification_health(
        jobs=[job("stuck", first_seen_date="2026-06-20", created_at="2026-06-20T12:00:00Z")],
        job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()], as_of=AS_OF,
    )
    verification = next(item for item in result.health_components if item.component == "verification_health")
    assert verification.classification in {"Degraded", "Blocked"}
    assert result.sla_breaches
    assert result.blocker_counts["enrichment_not_attempted"] == 1


def test_empty_workbook_is_reproducible_and_does_not_crash():
    result = calculate_verification_health(
        jobs=[], job_sources=[], queue_rows=[], evidence_rows=[], runs_rows=[], as_of=AS_OF,
    )
    assert len(result.funnel) == 15
    assert all(metric.current_count == 0 for metric in result.funnel)
    assert result.overall_classification == "Blocked"
    assert result.critical_overrides


def test_blank_canonical_rows_are_ignored_without_losing_raw_read_counts():
    result = calculate_verification_health(
        jobs=[{}, {"job_key": "   "}, job("valid")],
        job_sources=[{}],
        queue_rows=[{}],
        evidence_rows=[{}],
        resolution_rows=[{}],
        runs_rows=[successful_daily_run()],
        as_of=AS_OF,
    )

    normalized = next(metric for metric in result.funnel if metric.stage == "jobs_normalized")
    leads = next(metric for metric in result.funnel if metric.stage == "leads_received")
    assert normalized.current_count == 1
    assert leads.current_count == 0
    assert result.blocker_counts == {"enrichment_not_attempted": 1}
    assert "" not in result.high_potential_blockers
    assert result.records_read == {
        "jobs": 3,
        "job_sources": 1,
        "queue": 1,
        "evidence": 1,
        "resolutions": 1,
        "runs": 1,
    }


def test_configurable_health_boundaries_change_classification():
    thresholds = HealthThresholds(
        verification_watch_breach_rate=0.05,
        verification_degraded_breach_rate=0.10,
        verification_watch_conversion_rate=0.80,
        verification_degraded_conversion_rate=0.50,
        decision_watch_ready_rate=0.80,
        decision_degraded_ready_rate=0.50,
    )
    jobs = [
        job("breached-1", first_seen_date="2026-06-20", created_at="2026-06-20T12:00:00Z"),
        job("breached-2", first_seen_date="2026-06-20", created_at="2026-06-20T12:00:00Z"),
        job("verified", score_status="verified", verified_total_score=80),
    ]
    result = calculate_verification_health(
        jobs=jobs, job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()], thresholds=thresholds, as_of=AS_OF,
    )
    verification = next(item for item in result.health_components if item.component == "verification_health")
    decision = next(item for item in result.health_components if item.component == "decision_readiness_health")
    assert verification.score < 60
    assert verification.classification in {"Degraded", "Blocked"}
    assert decision.score < 40
    assert decision.classification == "Blocked"


def test_configuration_mapping_loads_conversion_boundaries():
    thresholds = HealthThresholds.from_mapping({
        "health": {
            "verification_watch_conversion_rate": 0.75,
            "verification_degraded_conversion_rate": 0.30,
        }
    })
    assert thresholds.verification_watch_conversion_rate == 0.75
    assert thresholds.verification_degraded_conversion_rate == 0.30


def test_workflow_validation_run_is_not_used_as_operational_anchor():
    enrichment = successful_daily_run(
        run_id="sprint32_daily_20260626T160000Z",
        finished_at="2026-06-26T16:00:00Z",
    )
    validation = {
        "run_id": "sprint16_workflow_validation_20260626T175900Z",
        "run_type": "sprint_16_workflow_validation",
        "source_name": "Daily run schema preflight",
        "status": "success",
        "started_at": "2026-06-26T17:59:00Z",
        "finished_at": "2026-06-26T17:59:00Z",
    }
    result = calculate_verification_health(
        jobs=[], job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[enrichment, validation], as_of=AS_OF,
    )
    workflow = next(item for item in result.health_components if item.component == "workflow_reliability")
    assert workflow.supporting_metrics["latest_run_id"] == enrichment["run_id"]
    assert result.run_id == "sprint33_verification_health_sprint32_daily_20260626T160000Z"


def test_source_health_uses_latest_enrichment_notes_not_daily_completion_notes():
    enrichment = successful_daily_run(run_id="sprint32_daily_source_metrics")
    daily_completion = {
        "run_id": "daily_workflow_completion_20260626T170000Z",
        "run_type": "daily_workflow_completion",
        "source_name": "GitHub Actions daily run",
        "status": "success",
        "started_at": "2026-06-26T17:00:00Z",
        "finished_at": "2026-06-26T17:00:00Z",
        "notes": "{}",
    }
    result = calculate_verification_health(
        jobs=[], job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[enrichment, daily_completion], as_of=AS_OF,
    )
    source = next(item for item in result.health_components if item.component == "source_health")
    assert source.supporting_metrics["latest_enrichment_run_id"] == enrichment["run_id"]
    assert source.supporting_metrics["attempts"] == 3
    assert source.supporting_metrics["status"] == "measured"


def test_lifecycle_health_requires_authoritative_eligibility_and_real_check_timestamp():
    generic_lead = job(
        "generic",
        canonical_url="https://www.linkedin.com/jobs/view/123",
        updated_at="2026-06-26T17:00:00Z",
    )
    verified_unchecked = job(
        "verified",
        score_status="verified",
        canonical_url="https://jobs.acme.com/verified",
        updated_at="2026-06-26T17:00:00Z",
    )
    result = calculate_verification_health(
        jobs=[generic_lead, verified_unchecked], job_sources=[], queue_rows=[], evidence_rows=[],
        runs_rows=[successful_daily_run()], as_of=AS_OF,
    )
    lifecycle = next(item for item in result.health_components if item.component == "lifecycle_health")
    assert lifecycle.supporting_metrics["eligible_jobs"] == 1
    assert lifecycle.supporting_metrics["unchecked_jobs"] == 1
    assert lifecycle.supporting_metrics["stale_jobs"] == 1
    assert lifecycle.classification == "Blocked"
