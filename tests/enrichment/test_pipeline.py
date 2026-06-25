from __future__ import annotations

from dataclasses import dataclass

from src.enrichment.pipeline import run_enrichment_pipeline


@dataclass
class Summary:
    stage: str

    def to_dict(self):
        return {"stage": self.stage}


def test_pipeline_runs_direct_link_before_company_ats(monkeypatch):
    calls = []

    def direct(*_args, **_kwargs):
        calls.append("direct_link")
        return Summary("direct_link")

    def company(*_args, **_kwargs):
        calls.append("company_ats")
        return Summary("company_ats")

    monkeypatch.setattr("src.enrichment.pipeline.run_direct_link_enrichment", direct)
    monkeypatch.setattr("src.enrichment.pipeline.run_company_ats_enrichment", company)

    result = run_enrichment_pipeline(object(), direct_limit=2, company_limit=3, job_key="job-1")

    assert calls == ["direct_link", "company_ats"]
    assert result == {
        "direct_link": {"stage": "direct_link"},
        "company_ats": {"stage": "company_ats"},
    }
