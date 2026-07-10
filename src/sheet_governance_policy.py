from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from src.models import (
    JOB_FIELDS,
    SPRINT_36_REVIEW_JOB_FIELDS,
    SPRINT_37_DECISION_JOB_FIELDS,
    VALID_APPLICATION_STATUSES,
    VALID_COMPENSATION_SOURCE_TYPES,
    VALID_DISMISSAL_REASONS,
    VALID_EVIDENCE_CONFIDENCE,
    VALID_INTEREST_DECISIONS,
    VALID_REVIEW_STATUSES,
    VALID_WORK_MODEL_VALUES,
)

SYSTEM_HEADER_COLOR = "#B7B7B7"
EDITABLE_HEADER_COLOR = "#93C47D"
SHEET_GUIDE = "Sheet_Guide"
BOOLEAN_OPTIONS = ("TRUE", "FALSE")

JOBS_CONTROLLED_FIELDS: dict[str, tuple[str, ...]] = {
    "review_status": tuple(sorted(VALID_REVIEW_STATUSES)),
    "interest_decision": tuple(sorted(value for value in VALID_INTEREST_DECISIONS if value)),
    "dismissal_reason": tuple(sorted(value for value in VALID_DISMISSAL_REASONS if value)),
    "application_status": tuple(sorted(value for value in VALID_APPLICATION_STATUSES if value)),
    "work_model": tuple(sorted(VALID_WORK_MODEL_VALUES)),
    "compensation_source_type": tuple(sorted(VALID_COMPENSATION_SOURCE_TYPES)),
    "compensation_confidence": tuple(sorted(VALID_EVIDENCE_CONFIDENCE)),
    "work_model_confidence": tuple(sorted(VALID_EVIDENCE_CONFIDENCE)),
    "location_confidence": tuple(sorted(VALID_EVIDENCE_CONFIDENCE)),
    "benefits_confidence": tuple(sorted(VALID_EVIDENCE_CONFIDENCE)),
    "required_office_days_per_week": ("0", "1", "2", "3", "4", "5"),
}

DERIVED_DECISION_FIELDS = {
    "estimated_total_comp_min",
    "estimated_total_comp_max",
    "commute_bucket",
    "compensation_improvement",
    "total_compensation_improvement",
    "work_model_improvement",
    "commute_improvement",
    "benefits_confidence_summary",
    "scope_p_and_l_modifier",
    "move_value_classification",
    "move_value_notes",
    "move_value_updated_at",
    "decision_evidence_conflict_notes",
}
JOBS_MANUAL_FREE_TEXT_FIELDS = frozenset(
    (set(SPRINT_36_REVIEW_JOB_FIELDS) - {"manual_decision_conflict"})
    | (set(SPRINT_37_DECISION_JOB_FIELDS) - DERIVED_DECISION_FIELDS)
) - frozenset(JOBS_CONTROLLED_FIELDS)
JOBS_EDITABLE_FIELDS = JOBS_MANUAL_FREE_TEXT_FIELDS | frozenset(JOBS_CONTROLLED_FIELDS)


@dataclass(frozen=True, slots=True)
class SheetPolicy:
    worksheet_name: str
    header_row: int = 1
    frozen_rows: int = 1
    frozen_columns: int = 1
    filter_enabled: bool = True
    all_headers_editable: bool = False
    editable_fields: frozenset[str] = frozenset()
    controlled_fields: Mapping[str, tuple[str, ...]] | None = None
    required: bool = False
    purpose: str = "System-managed project data"

    def dropdowns(self) -> Mapping[str, tuple[str, ...]]:
        return self.controlled_fields or {}

    def is_editable(self, header: str) -> bool:
        return bool(header) and (
            self.all_headers_editable
            or header in self.editable_fields
            or header in self.dropdowns()
        )


def _policy(name: str, *, frozen_columns: int = 2, purpose: str) -> SheetPolicy:
    return SheetPolicy(name, frozen_columns=frozen_columns, purpose=purpose)


SHEET_POLICIES: dict[str, SheetPolicy] = {
    "Jobs": SheetPolicy(
        "Jobs",
        frozen_columns=4,
        editable_fields=JOBS_EDITABLE_FIELDS,
        controlled_fields=JOBS_CONTROLLED_FIELDS,
        required=True,
        purpose="Canonical source of truth; edit only green columns",
    ),
    "Config_Searches": SheetPolicy(
        "Config_Searches",
        frozen_columns=2,
        all_headers_editable=True,
        controlled_fields={
            "remote_allowed": BOOLEAN_OPTIONS,
            "hybrid_allowed": BOOLEAN_OPTIONS,
            "active": BOOLEAN_OPTIONS,
        },
        purpose="User-managed search configuration",
    ),
    "Config_Companies": SheetPolicy(
        "Config_Companies",
        frozen_columns=2,
        all_headers_editable=True,
        controlled_fields={"active": BOOLEAN_OPTIONS, "enrichment_active": BOOLEAN_OPTIONS},
        purpose="User-managed company configuration",
    ),
    "Scoring_Rules": SheetPolicy(
        "Scoring_Rules",
        frozen_columns=2,
        all_headers_editable=True,
        controlled_fields={"active": BOOLEAN_OPTIONS},
        purpose="User-managed scoring configuration",
    ),
    "Target_Companies": SheetPolicy(
        "Target_Companies",
        frozen_columns=2,
        all_headers_editable=True,
        controlled_fields={"active": BOOLEAN_OPTIONS},
        purpose="User-managed target-company configuration",
    ),
    "Review_Queue": _policy("Review_Queue", frozen_columns=7, purpose="Generated review surface; edit Jobs"),
    "Follow_Up_Queue": _policy("Follow_Up_Queue", frozen_columns=3, purpose="Generated follow-up surface; edit Jobs"),
    "Weekly_Value": _policy("Weekly_Value", purpose="Generated weekly metrics dashboard"),
    "Weekly_Context": _policy("Weekly_Context", purpose="Generated weekly email contract"),
    "Dashboard": SheetPolicy("Dashboard", filter_enabled=False, purpose="Generated dashboard"),
    "Digest": SheetPolicy(
        "Digest",
        header_row=5,
        frozen_rows=5,
        frozen_columns=3,
        purpose="Generated digest table",
    ),
}
SHEET_POLICIES.update(
    {
        name: _policy(name, purpose=purpose)
        for name, purpose in {
            "Job_Sources": "System-managed source lineage",
            "Runs": "System-managed run history",
            "Snapshots": "System-managed historical snapshots",
            "Rejected_Jobs": "System-managed rejection diagnostics",
            "Gmail_Messages": "System-managed Gmail diagnostics",
            "Enrichment_Queue": "System-managed enrichment queue",
            "Enrichment_Evidence": "System-managed enrichment evidence",
            "Posting_Resolution": "System-managed posting resolution",
            "Resolution_Candidates": "System-managed resolution candidates",
            "Source_Health": "System-managed source health",
        }.items()
    }
)
GENERATED_SURFACE_NAMES = {
    "Review_Queue",
    "Follow_Up_Queue",
    "Weekly_Value",
    "Weekly_Context",
    "Dashboard",
    "Digest",
}
GENERATED_SURFACE_POLICIES = {name: SHEET_POLICIES[name] for name in GENERATED_SURFACE_NAMES}


@dataclass(frozen=True, slots=True)
class GovernanceValidation:
    ok: bool
    errors: tuple[str, ...]
    governed_sheets: int
    jobs_editable_fields: int
    jobs_controlled_fields: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_governance_definitions() -> GovernanceValidation:
    errors: list[str] = []
    job_fields = set(JOB_FIELDS)
    for label, fields in {
        "editable": JOBS_EDITABLE_FIELDS,
        "controlled": set(JOBS_CONTROLLED_FIELDS),
    }.items():
        unknown = sorted(set(fields) - job_fields)
        if unknown:
            errors.append(f"Jobs {label} fields are not in JOB_FIELDS: {', '.join(unknown)}")
    for sheet_name, policy in SHEET_POLICIES.items():
        for field, options in policy.dropdowns().items():
            if not options or len(options) != len(set(options)):
                errors.append(f"{sheet_name}.{field} has invalid dropdown options")
            if not policy.all_headers_editable and field not in policy.editable_fields:
                errors.append(f"{sheet_name}.{field} is controlled but not editable")
    for sheet_name in GENERATED_SURFACE_NAMES:
        policy = SHEET_POLICIES[sheet_name]
        if policy.all_headers_editable or policy.editable_fields or policy.dropdowns():
            errors.append(f"Generated surface {sheet_name} must remain read-only")
    return GovernanceValidation(
        ok=not errors,
        errors=tuple(errors),
        governed_sheets=len(SHEET_POLICIES),
        jobs_editable_fields=len(JOBS_EDITABLE_FIELDS),
        jobs_controlled_fields=len(JOBS_CONTROLLED_FIELDS),
    )
