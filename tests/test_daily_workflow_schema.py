from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-run.yml"


def test_daily_workflow_migrates_schema_before_ingestion():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    migrate_index = workflow.index("python -m src.schema --migrate")
    static_index = workflow.index("python -m src.main --static-pages-smoke-test")
    gmail_index = workflow.index("python -m src.gmail_ingestion --run")

    assert "Migrate and validate workbook schema" in workflow
    assert migrate_index < static_index
    assert migrate_index < gmail_index
    assert "python -m src.schema --validate" not in workflow
