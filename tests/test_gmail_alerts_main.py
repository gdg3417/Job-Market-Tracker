import src.main as main


def test_legacy_gmail_command_uses_sprint23_runner(monkeypatch):
    expected = {"status": "success", "run_mode": "gmail_ingestion_reliability"}
    call_count = {"value": 0}

    def fake_runner():
        call_count["value"] += 1
        return expected

    monkeypatch.setattr(main, "run_gmail_ingestion", fake_runner)

    assert main.run_gmail_alerts_smoke_test() == expected
    assert call_count["value"] == 1
