from src.resolution.models import POSTING_RESOLUTION_FIELDS, RESOLUTION_CANDIDATE_FIELDS
from src.schema import CANONICAL_SCHEMA


def test_resolution_sheets_are_canonical_and_migratable():
    assert CANONICAL_SCHEMA["Posting_Resolution"].headers == POSTING_RESOLUTION_FIELDS
    assert CANONICAL_SCHEMA["Resolution_Candidates"].headers == RESOLUTION_CANDIDATE_FIELDS
    assert "manual_authoritative_url" in POSTING_RESOLUTION_FIELDS
    assert "manual_resolution_decision" in POSTING_RESOLUTION_FIELDS
    assert "observed_url" in RESOLUTION_CANDIDATE_FIELDS
    assert "canonical_url" in RESOLUTION_CANDIDATE_FIELDS
    assert "match_confidence" in RESOLUTION_CANDIDATE_FIELDS


class _BlankWorksheet:
    def __init__(self):
        self.row_count = 10
        self.col_count = 1
        self.headers = []

    def resize(self, *, rows, cols):
        self.row_count = rows
        self.col_count = cols

    def row_values(self, _row):
        return list(self.headers)

    def update(self, *, range_name, values, value_input_option):
        assert value_input_option == "USER_ENTERED"
        self.headers.extend(values[0])


class _Workbook:
    def fetch_sheet_metadata(self):
        return {"properties": {"timeZone": "America/Chicago"}}


class _MigrationClient:
    def __init__(self):
        self.workbook = _Workbook()
        self.sheets = {}
        self._header_cache = {}

    def ensure_worksheet(self, worksheet_name, *, rows, cols):
        return self.sheets.setdefault(worksheet_name, _BlankWorksheet())


def test_resolution_schema_migration_creates_both_new_header_rows(monkeypatch):
    import src.schema as schema_module
    from src.schema import HeaderSpec, migrate_trailing_headers

    client = _MigrationClient()
    monkeypatch.setattr(
        schema_module,
        "CANONICAL_SCHEMA",
        {
            "Posting_Resolution": HeaderSpec("Posting_Resolution", POSTING_RESOLUTION_FIELDS),
            "Resolution_Candidates": HeaderSpec("Resolution_Candidates", RESOLUTION_CANDIDATE_FIELDS),
        },
    )

    result = migrate_trailing_headers(client)

    assert result.ok is True
    assert client.sheets["Posting_Resolution"].headers == POSTING_RESOLUTION_FIELDS
    assert client.sheets["Resolution_Candidates"].headers == RESOLUTION_CANDIDATE_FIELDS
