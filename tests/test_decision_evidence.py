from src.decision_evidence import (
    MoveCriteria,
    apply_user_decision_evidence,
    build_move_value_dashboard_sections,
    calculate_commute_bucket,
    calculate_move_value,
    compensation_status,
    estimate_total_compensation,
    infer_work_model,
    merge_decision_evidence,
    parse_compensation_text,
)
from src.models import JOB_FIELDS, SPRINT_37_DECISION_JOB_FIELDS, JobPosting


def make_job(**overrides):
    values = {
        "job_key": "sample-job",
        "company": "Acme Industrial",
        "title": "Director, Commercial Strategy",
        "location": "Plano, TX",
        "status": "open",
        "total_score": 82,
        "verified_total_score": 82,
        "score_status": "verified",
        "potential_priority": "high",
        "potential_priority_score": 75,
        "role_family": "Commercial Strategy",
        "role_level": "Director",
        "p_and_l_path_score": 16,
        "description_text": "Own revenue growth and business unit margin expansion.",
        "first_seen_date": "2026-06-20",
        "last_seen_date": "2026-06-27",
    }
    values.update(overrides)
    return JobPosting(**values)


def test_decision_evidence_schema_fields_are_trailing_job_fields():
    assert JOB_FIELDS[-len(SPRINT_37_DECISION_JOB_FIELDS):] == SPRINT_37_DECISION_JOB_FIELDS


def test_compensation_parsing_handles_salary_ranges():
    parsed = parse_compensation_text("Base salary range is $170,000 to $210,000 plus bonus.")

    assert parsed["base_salary_min"] == 170000
    assert parsed["base_salary_max"] == 210000
    assert parsed["salary_currency"] == "USD"


def test_compensation_parsing_handles_k_suffix_and_single_amount():
    parsed = parse_compensation_text("Target base is 185k.")

    assert parsed["base_salary_min"] == 185000
    assert parsed["base_salary_max"] == 185000


def test_total_compensation_calculation_includes_bonus_and_other_pay():
    low, high = estimate_total_compensation(
        170000,
        200000,
        bonus_target_percent=15,
        bonus_max_percent=25,
        equity_or_lti_estimate=10000,
        sign_on_bonus=5000,
    )

    assert low == 210500
    assert high == 265000


def test_confirmed_versus_estimated_compensation_classification():
    assert compensation_status("employer_posted", has_amount=True) == "confirmed"
    assert compensation_status("recruiter_provided", has_amount=True) == "confirmed"
    assert compensation_status("user_entered", has_amount=True) == "confirmed"
    assert compensation_status("trusted_external_estimate", has_amount=True) == "estimated"
    assert compensation_status("inferred_from_title", has_amount=True) == "estimated"
    assert compensation_status("unknown", has_amount=False) == "unknown"


def test_missing_compensation_does_not_mark_role_worse():
    job = make_job(base_salary_min=None, base_salary_max=None, estimated_total_comp_min=None, estimated_total_comp_max=None, work_model="unknown", commute_bucket="")

    assert calculate_move_value(job)["move_value_classification"] != "worse"


def test_work_model_extraction_and_required_office_days():
    assert infer_work_model("Hybrid role, 3 days per week in office") == "hybrid"
    job = make_job(work_model="hybrid", required_office_days_per_week=4)

    result = calculate_move_value(job)

    assert result["work_model_improvement"] == "neutral"


def test_commute_bucket_calculation_uses_time_then_distance():
    assert calculate_commute_bucket(travel_time_minutes=12) == "under_15_minutes"
    assert calculate_commute_bucket(travel_time_minutes=30) == "15_to_30_minutes"
    assert calculate_commute_bucket(travel_time_minutes=45) == "30_to_45_minutes"
    assert calculate_commute_bucket(travel_time_minutes=55) == "over_45_minutes"
    assert calculate_commute_bucket(distance_miles=10) == "15_to_30_minutes"


def test_user_entered_compensation_evidence_is_preserved_over_estimate():
    existing = make_job(
        base_salary_min=175000,
        base_salary_max=190000,
        estimated_total_comp_min=201250,
        estimated_total_comp_max=218500,
        compensation_source_type="user_entered",
        compensation_confidence="confirmed",
        compensation_notes="Recruiter phone screen.",
    )
    incoming = make_job(
        base_salary_min=150000,
        base_salary_max=165000,
        compensation_source_type="trusted_external_estimate",
        compensation_confidence="estimated",
    )

    merged = merge_decision_evidence(existing, incoming)

    assert merged.base_salary_min == 175000
    assert merged.compensation_source_type == "user_entered"
    assert merged.compensation_confidence == "confirmed"
    assert "conflicting_compensation_evidence" in merged.decision_evidence_conflict_notes


def test_user_entered_work_model_evidence_beats_automated_estimate():
    existing = make_job(work_model="unknown", work_model_source="trusted_external_estimate")
    incoming = make_job(work_model="remote", work_model_source="user_entered", work_model_confidence="confirmed")

    merged = merge_decision_evidence(existing, incoming)

    assert merged.work_model == "remote"
    assert merged.work_model_source == "user_entered"


def test_move_value_can_be_clearly_better_with_compensation_flexibility_and_scope():
    job = make_job(
        base_salary_min=180000,
        base_salary_max=210000,
        estimated_total_comp_min=207000,
        estimated_total_comp_max=241500,
        compensation_source_type="employer_posted",
        work_model="hybrid",
        required_office_days_per_week=2,
        commute_bucket="15_to_30_minutes",
    )

    result = calculate_move_value(job, MoveCriteria())

    assert result["move_value_classification"] == "clearly_better"
    assert result["total_compensation_improvement"] == "target_total_comp"


def test_worse_requires_negative_evidence_not_missing_evidence():
    missing = make_job(work_model="unknown", compensation_source_type="unknown", commute_bucket="")
    worse = make_job(
        base_salary_min=120000,
        base_salary_max=125000,
        estimated_total_comp_min=120000,
        estimated_total_comp_max=125000,
        compensation_source_type="employer_posted",
        work_model="on_site",
        required_office_days_per_week=5,
        commute_bucket="over_45_minutes",
        p_and_l_path_score=0,
        description_text="Budget reporting role.",
    )

    assert calculate_move_value(missing)["move_value_classification"] != "worse"
    assert calculate_move_value(worse)["move_value_classification"] == "worse"


def test_dashboard_sections_surface_sprint37_queues():
    jobs = [
        make_job(job_key="confirmed", base_salary_min=180000, base_salary_max=205000, estimated_total_comp_max=235000, compensation_source_type="employer_posted", work_model="hybrid", required_office_days_per_week=2, commute_bucket="15_to_30_minutes"),
        make_job(job_key="unknown", base_salary_min=None, base_salary_max=None, estimated_total_comp_min=None, estimated_total_comp_max=None, compensation_source_type="unknown", work_model="unknown"),
        make_job(job_key="onsite", work_model="on_site", required_office_days_per_week=5, commute_bucket="over_45_minutes"),
    ]

    values = build_move_value_dashboard_sections(jobs)
    flattened = "\n".join(str(cell) for row in values for cell in row)

    for expected in [
        "Move-value intelligence",
        "Strong roles with confirmed compensation",
        "Strong roles with unknown compensation",
        "Remote or hybrid opportunities",
        "Short-commute opportunities",
        "Five-day on-site penalties",
        "Roles meeting serious-move compensation",
        "Roles requiring compensation follow-up",
        "Roles requiring work-model follow-up",
    ]:
        assert expected in flattened


def test_apply_user_decision_evidence_sets_user_entered_observation_date():
    job = make_job()
    updated = apply_user_decision_evidence(
        job,
        base_salary_min=190000,
        base_salary_max=210000,
        compensation_source_type="user_entered",
        compensation_confidence="confirmed",
    )

    assert updated.compensation_source_type == "user_entered"
    assert updated.compensation_observed_date
