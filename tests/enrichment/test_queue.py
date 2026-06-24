from __future__ import annotations

from src.enrichment.models import EnrichmentQueueItem
from src.enrichment.queue import due_for_processing


def test_recent_in_progress_item_is_not_reprocessed():
    item = EnrichmentQueueItem(
        enrichment_id="enr-1",
        status="in_progress",
        last_attempted_at="2026-06-24T12:00:00Z",
        updated_at="2026-06-24T12:00:00Z",
    )

    assert due_for_processing(item, now="2026-06-24T12:29:59Z") is False


def test_stale_in_progress_item_is_recovered():
    item = EnrichmentQueueItem(
        enrichment_id="enr-1",
        status="in_progress",
        last_attempted_at="2026-06-24T12:00:00Z",
        updated_at="2026-06-24T12:00:00Z",
    )

    assert due_for_processing(item, now="2026-06-24T12:30:00Z") is True


def test_retry_timestamp_comparison_uses_datetimes_not_text_ordering():
    item = EnrichmentQueueItem(
        enrichment_id="enr-1",
        status="retryable_failure",
        next_attempt_at="2026-06-24T13:00:00+00:00",
    )

    assert due_for_processing(item, now="2026-06-24T08:00:00-05:00") is True
