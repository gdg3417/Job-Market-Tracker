from types import SimpleNamespace

import src.main as main


class FakeSummary:
    def to_dict(self):
        return {"jobs_created": 0, "jobs_updated": 0}


class FakeSheetClient:
    def __init__(self):
        self.runs = []

    def append_run(self, record):
        self.runs.append(record)


def test_gmail_alerts_smoke_test_quarantines_rejected_alerts(monkeypatch):
    sheet_client = FakeSheetClient()
    rejected_alert = SimpleNamespace(confidence="rejected")
    valid_alert = SimpleNamespace(confidence="high")
    settings = SimpleNamespace(
        gmail_client_config="client.json",
        gmail_token_json="token.json",
        scoring_rules_path="config/scoring_rules.yml",
        gmail_label_name="Job Tracker",
        gmail_max_results=10,
    )
    quarantine_call = {}

    monkeypatch.setattr(main, "load_settings", lambda: settings)
    monkeypatch.setattr(main.SheetClient, "from_settings", lambda value: sheet_client)
    monkeypatch.setattr(main, "load_scoring_rules", lambda path: {})
    monkeypatch.setattr(main, "build_gmail_service", lambda client_config, token_json: object())
    monkeypatch.setattr(main, "fetch_labeled_gmail_emails", lambda service, label_name, max_results: ["email-1"])
    monkeypatch.setattr(main, "parse_job_alert_email", lambda email: [rejected_alert, valid_alert])
    monkeypatch.setattr(main, "parsed_alerts_to_jobs", lambda alerts, scoring_rules=None, seen_date=None: [])
    monkeypatch.setattr(main, "upsert_jobs", lambda client, jobs, seen_date=None: FakeSummary())

    def fake_append_rejected_alerts(client, alerts):
        quarantine_call["client"] = client
        quarantine_call["alerts"] = list(alerts)
        return 1

    monkeypatch.setattr(main, "append_rejected_alerts", fake_append_rejected_alerts)

    result = main.run_gmail_alerts_smoke_test()

    assert quarantine_call["client"] is sheet_client
    assert quarantine_call["alerts"] == [rejected_alert, valid_alert]
    assert result["rejected_alerts"] == 1
    assert result["quarantined_alerts"] == 1
    assert sheet_client.runs[0]["records_failed"] == 1
    assert "rejected_alerts" in sheet_client.runs[0]["notes"]
    assert "quarantined_alerts" in sheet_client.runs[0]["notes"]
