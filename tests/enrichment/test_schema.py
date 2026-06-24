from __future__ import annotations

from src.enrichment.models import ENRICHMENT_EVIDENCE_FIELDS, ENRICHMENT_QUEUE_FIELDS
from src.schema import CANONICAL_SCHEMA


def test_enrichment_queue_is_part_of_canonical_workbook_schema():
    assert CANONICAL_SCHEMA["Enrichment_Queue"].headers == ENRICHMENT_QUEUE_FIELDS
    assert ENRICHMENT_QUEUE_FIELDS[0] == "enrichment_id"
    assert ENRICHMENT_QUEUE_FIELDS[-1] == "updated_at"


def test_enrichment_evidence_is_part_of_canonical_workbook_schema():
    assert CANONICAL_SCHEMA["Enrichment_Evidence"].headers == ENRICHMENT_EVIDENCE_FIELDS
    assert "raw_content_hash" in ENRICHMENT_EVIDENCE_FIELDS
    assert "accepted" in ENRICHMENT_EVIDENCE_FIELDS
    assert "raw_html" not in ENRICHMENT_EVIDENCE_FIELDS
