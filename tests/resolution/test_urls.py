from __future__ import annotations

from src.resolution.ats_recognition import recognize_ats
from src.resolution.urls import canonicalize_url, unwrap_url


def test_linkedin_outbound_url_is_unwrapped_and_tracking_removed():
    wrapped = (
        "https://www.linkedin.com/redir/redirect?url="
        "https%3A%2F%2Fjobs.lever.co%2Facme%2Fabc-123%3Futm_source%3Dlinkedin%26ref%3Dalert"
    )

    assert unwrap_url(wrapped) == "https://jobs.lever.co/acme/abc-123?utm_source=linkedin&ref=alert"
    assert canonicalize_url(wrapped) == "https://jobs.lever.co/acme/abc-123"


def test_email_safe_nested_redirect_is_unwrapped_deterministically():
    wrapped = "https://safe.example.com/click?target=https%3A%2F%2Fcareers.example.com%2Fjob%2F123%3Futm_campaign%3Djobs"

    assert canonicalize_url(wrapped) == "https://careers.example.com/job/123"


def test_known_ats_hosts_and_stable_identifiers_are_recognized():
    cases = {
        "https://company.wd5.myworkdayjobs.com/en-US/jobs/job/Dallas-TX/Director_R12345": ("workday", "R12345"),
        "https://boards.greenhouse.io/acme/jobs/456789": ("greenhouse", "456789"),
        "https://jobs.lever.co/acme/9d7be829-1f2a-42a2-8db2-f14fa1b51234": ("lever", "9d7be829-1f2a-42a2-8db2-f14fa1b51234"),
        "https://careers-example.icims.com/jobs/24680/director/job": ("icims", "24680"),
        "https://jobs.smartrecruiters.com/Acme/743999123456789-director": ("smartrecruiters", "743999123456789-director"),
        "https://jobs.example.com/job/Dallas-Director/123456/?jobId=123456": ("", "123456"),
        "https://jobs.jobvite.com/acme/job/oAbc123": ("jobvite", "oAbc123"),
        "https://careers.example.phenompeople.com/us/en/job/REQ-123/director": ("phenom", "REQ-123"),
        "https://career012.successfactors.com/career?company=acme&career_job_req_id=98765": ("successfactors", "98765"),
        "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/4567": ("oracle_recruiting", "4567"),
    }
    for url, expected in cases.items():
        identity = recognize_ats(url)
        assert (identity.platform, identity.stable_identifier) == expected
