from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:100]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if marker in text:
        return
    file_path.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8")


# 1. Fail closed when converting a static source to a structured ATS.
replace_once(
    "src/source_quality.py",
    "\ndef apply_approved_source_updates(\n",
    '''\ndef _structured_ats_slug(\n    platform: str,\n    *,\n    row: dict[str, Any],\n    finding: SourceAuditFinding,\n) -> str:\n    platform = clean_text(platform).lower()\n    url_candidates = [finding.final_url, finding.source_url, row.get("source_url")]\n    for value in url_candidates:\n        candidate = normalize_url(value)\n        if not candidate:\n            continue\n        parts = urlsplit(candidate)\n        host = (parts.hostname or "").lower().removeprefix("www.")\n        path_parts = [part for part in parts.path.split("/") if part]\n        if platform == "greenhouse":\n            if host == "boards.greenhouse.io" and path_parts:\n                return path_parts[0]\n            if host == "boards-api.greenhouse.io" and "boards" in path_parts:\n                index = path_parts.index("boards")\n                if len(path_parts) > index + 1:\n                    return path_parts[index + 1]\n        elif platform == "lever":\n            if host in {"jobs.lever.co", "hire.lever.co"} and path_parts:\n                return path_parts[0]\n            if host == "api.lever.co" and "postings" in path_parts:\n                index = path_parts.index("postings")\n                if len(path_parts) > index + 1:\n                    return path_parts[index + 1]\n\n    existing = clean_text(row.get("source_slug")).strip("/")\n    if re.fullmatch(r"[A-Za-z0-9._-]+", existing):\n        return existing\n    return ""\n\n\ndef apply_approved_source_updates(\n''',
)

replace_once(
    "src/source_quality.py",
    '''        elif finding.classification == STRUCTURED_ATS and finding.ats_platform in {"greenhouse", "lever"}:\n            platform = finding.ats_platform\n            updated["source_type"] = platform\n            updated["ats_platform"] = platform\n            updated["ingestion_mode"] = f"ats_{platform}"\n            updated["source_quality"] = "success"\n            updated["source_url"] = finding.final_url or finding.source_url\n            updated["active"] = "TRUE"\n''',
    '''        elif finding.classification == STRUCTURED_ATS and finding.ats_platform in {"greenhouse", "lever"}:\n            platform = finding.ats_platform\n            source_slug = _structured_ats_slug(platform, row=row, finding=finding)\n            if not source_slug:\n                continue\n            updated["source_type"] = platform\n            updated["source_slug"] = source_slug\n            updated["ats_platform"] = platform\n            updated["ingestion_mode"] = f"ats_{platform}"\n            updated["source_quality"] = "success"\n            updated["source_url"] = finding.final_url or finding.source_url\n            updated["active"] = "TRUE"\n''',
)

replace_once(
    "src/source_quality.py",
    '''                    "source_type": row.get("source_type", ""),\n                    "ats_platform": row.get("ats_platform", ""),\n''',
    '''                    "source_type": row.get("source_type", ""),\n                    "source_slug": row.get("source_slug", ""),\n                    "ats_platform": row.get("ats_platform", ""),\n''',
)
replace_once(
    "src/source_quality.py",
    '''                    "source_type": updated.get("source_type", ""),\n                    "ats_platform": updated.get("ats_platform", ""),\n''',
    '''                    "source_type": updated.get("source_type", ""),\n                    "source_slug": updated.get("source_slug", ""),\n                    "ats_platform": updated.get("ats_platform", ""),\n''',
)

# 2. Make no-probe reporting non-authoritative for the execution policy.
replace_once(
    "src/source_quality.py",
    '''def write_source_quality_surfaces(\n    sheet_client: Any,\n    *,\n    findings: Iterable[SourceAuditFinding],\n    yield_rows: Iterable[SourceYieldRow],\n) -> dict[str, int]:\n    finding_records = [finding.to_dict() for finding in findings]\n    yield_records = [row.to_dict() for row in yield_rows]\n    return {\n        "source_audit_rows_written": _replace_generated_sheet(sheet_client, SOURCE_AUDIT_SHEET, SOURCE_AUDIT_HEADERS, finding_records),\n        "source_yield_rows_written": _replace_generated_sheet(sheet_client, SOURCE_YIELD_SHEET, SOURCE_YIELD_HEADERS, yield_records),\n    }\n''',
    '''def write_source_quality_surfaces(\n    sheet_client: Any,\n    *,\n    findings: Iterable[SourceAuditFinding],\n    yield_rows: Iterable[SourceYieldRow],\n    write_audit: bool = True,\n) -> dict[str, int]:\n    finding_records = [finding.to_dict() for finding in findings]\n    yield_records = [row.to_dict() for row in yield_rows]\n    audit_rows_written = 0\n    if write_audit:\n        audit_rows_written = _replace_generated_sheet(\n            sheet_client, SOURCE_AUDIT_SHEET, SOURCE_AUDIT_HEADERS, finding_records\n        )\n    return {\n        "source_audit_rows_written": audit_rows_written,\n        "source_yield_rows_written": _replace_generated_sheet(\n            sheet_client, SOURCE_YIELD_SHEET, SOURCE_YIELD_HEADERS, yield_records\n        ),\n    }\n''',
)
replace_once(
    "src/source_quality.py",
    '''        writes = write_source_quality_surfaces(client, findings=findings, yield_rows=yield_rows)\n''',
    '''        writes = write_source_quality_surfaces(\n            client,\n            findings=findings,\n            yield_rows=yield_rows,\n            write_audit=probe_sources,\n        )\n''',
)

replace_once(
    "src/source_quality_report.py",
    '''    if write_report:\n        # Persist the detailed evidence before any approved configuration mutation.\n        writes = write_source_quality_surfaces(\n            client,\n            findings=findings,\n            yield_rows=yield_rows,\n        )\n\n    updates: list[dict[str, Any]] = []\n    if approved_company_ids:\n''',
    '''    if approved_company_ids and not probe_sources:\n        raise ValueError("Approved cleanup requires live source probes.")\n\n    if write_report:\n        # Persist live audit evidence before any approved configuration mutation.\n        # A no-probe report refreshes Source_Yield but preserves the last authoritative\n        # Source_Audit so it cannot change daily execution policy.\n        writes = write_source_quality_surfaces(\n            client,\n            findings=findings,\n            yield_rows=yield_rows,\n            write_audit=probe_sources,\n        )\n\n    updates: list[dict[str, Any]] = []\n    if approved_company_ids:\n''',
)
replace_once(
    "src/source_quality_report.py",
    '''        "probe_sources": probe_sources,\n        "sources_audited": len(findings),\n''',
    '''        "probe_sources": probe_sources,\n        "source_audit_preserved": not probe_sources,\n        "sources_audited": len(findings),\n''',
)
replace_once(
    "src/source_quality_report.py",
    '''    if approved and not args.write_report:\n        raise SystemExit(\n            "Approved configuration updates require --write-report so the audit evidence is persisted."\n        )\n''',
    '''    if approved and not args.write_report:\n        raise SystemExit(\n            "Approved configuration updates require --write-report so the audit evidence is persisted."\n        )\n    if approved and args.skip_live_probes:\n        raise SystemExit("Approved cleanup requires live source probes.")\n''',
)

# 3. Make the workflow reject cleanup without live evidence and describe no-probe safety.
replace_once(
    ".github/workflows/source-quality.yml",
    '''      skip_live_probes:\n        description: Build the report from configuration and workbook history without current HTTP probes\n''',
    '''      skip_live_probes:\n        description: Refresh yield reporting without HTTP probes while preserving the last live Source_Audit\n''',
)
replace_once(
    ".github/workflows/source-quality.yml",
    '''          if [ "${MODE}" = "apply_reviewed_cleanup" ]; then\n            raw_ids="${APPROVED_COMPANY_IDS:-}"\n''',
    '''          if [ "${MODE}" = "apply_reviewed_cleanup" ]; then\n            if [ "${SKIP_LIVE_PROBES}" = "true" ]; then\n              echo "::error::apply_reviewed_cleanup requires live source probes."\n              exit 1\n            fi\n            raw_ids="${APPROVED_COMPANY_IDS:-}"\n''',
)

# 4. Focused regression tests for both findings and downstream safety.
replace_once(
    "tests/test_source_quality_hardening.py",
    '''    SourceAuditFinding,\n    apply_approved_source_updates,\n''',
    '''    STRUCTURED_ATS,\n    SourceAuditFinding,\n    apply_approved_source_updates,\n''',
)

append_once(
    "tests/test_source_quality_hardening.py",
    "def test_structured_ats_conversion_populates_required_source_slug():",
    '''def test_structured_ats_conversion_populates_required_source_slug():\n    cases = [\n        ("greenhouse", "https://boards.greenhouse.io/acme/jobs", "acme"),\n        ("lever", "https://jobs.lever.co/acme", "acme"),\n    ]\n    for platform, final_url, expected_slug in cases:\n        row = _company(source_url="https://example.com/careers", source_slug="")\n        finding = _finding(\n            source_url=row["source_url"],\n            final_url=final_url,\n            classification=STRUCTURED_ATS,\n            ats_platform=platform,\n            http_status=200,\n            retry_eligible=False,\n            requires_configuration_change=True,\n            failure_observations=0,\n            recommended_action="prefer_structured_ats",\n        )\n        client = FakeUpdateClient()\n\n        updates = apply_approved_source_updates(\n            [(2, row)],\n            [finding],\n            approved_company_ids={"example"},\n            sheet_client=client,\n        )\n\n        assert len(updates) == 1\n        updated = client.updates[0][2]\n        assert updated["source_slug"] == expected_slug\n        assert updated["source_type"] == platform\n        assert updates[0]["before"]["source_slug"] == ""\n        assert updates[0]["after"]["source_slug"] == expected_slug\n\n\ndef test_structured_ats_conversion_refuses_unusable_slug():\n    row = _company(source_url="https://example.com/careers", source_slug="")\n    finding = _finding(\n        source_url=row["source_url"],\n        final_url="https://example.com/careers",\n        classification=STRUCTURED_ATS,\n        ats_platform="greenhouse",\n        http_status=200,\n        retry_eligible=False,\n        requires_configuration_change=True,\n        failure_observations=0,\n        recommended_action="prefer_structured_ats",\n    )\n    client = FakeUpdateClient()\n\n    updates = apply_approved_source_updates(\n        [(2, row)],\n        [finding],\n        approved_company_ids={"example"},\n        sheet_client=client,\n    )\n\n    assert updates == []\n    assert client.updates == []\n\n\ndef test_no_probe_report_preserves_authoritative_source_audit(monkeypatch):\n    events = []\n\n    class FakeClient:\n        def read_records_with_row_numbers(self, worksheet_name):\n            return [(2, _company())]\n\n        def read_records(self, worksheet_name):\n            return []\n\n        def append_run(self, record):\n            events.append(("append_run", record))\n\n    monkeypatch.setattr("src.source_quality_report.build_source_yield_report", lambda **kwargs: [])\n    monkeypatch.setattr("src.source_quality_report.configured_zero_yield_rows", lambda **kwargs: [])\n    monkeypatch.setattr(\n        "src.source_quality_report.write_source_quality_surfaces",\n        lambda *args, **kwargs: events.append(("write_surfaces", kwargs["write_audit"])) or {\n            "source_audit_rows_written": 0,\n            "source_yield_rows_written": 0,\n        },\n    )\n\n    result = run_source_quality_report(\n        probe_sources=False,\n        write_report=True,\n        sheet_client=FakeClient(),\n    )\n\n    assert events[0] == ("write_surfaces", False)\n    assert result["source_audit_preserved"] is True\n    assert result["source_audit_rows_written"] == 0\n\n\ndef test_no_probe_cleanup_is_rejected_before_writes():\n    class FakeClient:\n        def read_records_with_row_numbers(self, worksheet_name):\n            return [(2, _company())]\n\n        def read_records(self, worksheet_name):\n            return []\n\n    try:\n        run_source_quality_report(\n            probe_sources=False,\n            write_report=True,\n            approved_company_ids={"example"},\n            sheet_client=FakeClient(),\n        )\n    except ValueError as exc:\n        assert "requires live source probes" in str(exc)\n    else:\n        raise AssertionError("Expected no-probe cleanup to be rejected")\n''',
)

replace_once(
    "tests/test_source_quality_workflow.py",
    '''    assert "No valid company_id values were supplied" in text\n''',
    '''    assert "No valid company_id values were supplied" in text\n    assert "apply_reviewed_cleanup requires live source probes" in text\n''',
)

# 5. Sprint 51-only documentation corrections. Sprint 52 remains untouched.
replace_once(
    "docs/sprint_51_source_quality_yield.md",
    '''2. Move a validated Greenhouse or Lever source to its structured ingestion mode.\n''',
    '''2. Move a validated Greenhouse or Lever source to its structured ingestion mode only when a usable `source_slug` is already configured or can be derived from the validated ATS URL.\n''',
)
replace_once(
    "docs/sprint_51_source_quality_yield.md",
    '''Run without live HTTP probes:\n\n```text\npython -m src.source_quality_report --write-report --weeks 4 --skip-live-probes\n```\n''',
    '''Run without live HTTP probes:\n\n```text\npython -m src.source_quality_report --write-report --weeks 4 --skip-live-probes\n```\n\nNo-probe mode refreshes `Source_Yield` while preserving the most recent live `Source_Audit`. It cannot apply reviewed cleanup and cannot change the daily static-source execution policy.\n''',
)

print("Sprint 51 final safety patch applied")
