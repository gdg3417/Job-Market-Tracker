from __future__ import annotations

from typing import Any

from src import jobs_integrity_core as _core
from src.jobs_boundaries import JOBS_WORKSHEET_NAME


class _PreloadedSheetClient:
    """Expose an already loaded Jobs worksheet without another retry layer."""

    def __init__(self, sheet_client: Any, worksheet: Any) -> None:
        self.workbook = sheet_client.workbook
        self._worksheet = worksheet

    def get_worksheet(self, worksheet_name: str) -> Any:
        if worksheet_name != JOBS_WORKSHEET_NAME:
            raise ValueError(f"Unexpected worksheet request during Jobs audit: {worksheet_name}")
        return self._worksheet


def audit_jobs_integrity(
    sheet_client: Any,
    *,
    offender_limit: int = _core.DEFAULT_OFFENDER_LIMIT,
) -> _core.JobsIntegrityAudit:
    """Load Jobs once, then apply one bounded retry budget to the read-only audit."""
    worksheet = sheet_client.get_worksheet(JOBS_WORKSHEET_NAME)
    preloaded_client = _PreloadedSheetClient(sheet_client, worksheet)
    return _core._with_quota_backoff(
        lambda: _core._audit_jobs_integrity_once(
            preloaded_client,
            offender_limit=offender_limit,
        ),
        operation_name="audit Jobs integrity",
    )


_core.audit_jobs_integrity = audit_jobs_integrity

for _name in _core.__all__:
    globals()[_name] = getattr(_core, _name)

parse_args = _core.parse_args
_load_sheet_client = _core._load_sheet_client
main = _core.main
__all__ = list(_core.__all__)


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


if __name__ == "__main__":
    main()
