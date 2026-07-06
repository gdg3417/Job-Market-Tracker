from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


TARGET_FIT = "target"
STRETCH_FIT = "stretch"
MANUAL_REVIEW_FIT = "manual_review"
CONTEXT_DEPENDENT_FIT = "context_dependent"
TOO_SENIOR_FIT = "too_senior"
TOO_JUNIOR_FIT = "too_junior"
EXCLUDED_FIT = "excluded"
UNKNOWN_FIT = "unknown"

STRETCH_CONTEXT_TERMS = {
    "pe backed",
    "pe-backed",
    "private equity",
    "portfolio company",
    "lower middle market",
    "middle market",
    "small company",
    "smaller company",
    "growth company",
}

C_SUITE_PATTERNS = (
    r"\bchief\s+(?:executive|financial|operating|revenue|commercial|marketing|technology|strategy)\s+officer\b",
    r"\bceo\b",
    r"\bcfo\b",
    r"\bcoo\b",
    r"\bcro\b",
    r"\bcmo\b",
    r"\bcto\b",
    r"\bcso\b",
)


@dataclass(frozen=True, slots=True)
class SeniorityEvaluation:
    normalized_level: str
    seniority_fit: str
    reason_code: str
    score_penalty: int = 0
    manual_review: bool = False
    hard_exclude: bool = False


def _normalize(value: Any) -> str:
    text = str(value or "").replace("&", " and ").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _context_text(company_context: dict[str, Any] | None) -> str:
    if not company_context:
        return ""
    return _normalize(" ".join(str(value or "") for value in company_context.values()))


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _has_stretch_context(company_context: dict[str, Any] | None) -> bool:
    context = _context_text(company_context)
    return any(term in context for term in STRETCH_CONTEXT_TERMS)


def evaluate_seniority_fit(
    title: Any,
    fallback_role_level: Any = "",
    company_context: dict[str, Any] | None = None,
) -> SeniorityEvaluation:
    """Classify role seniority using user-specific target, stretch, and too-senior calibration."""

    title_text = _normalize(title)
    fallback_text = _normalize(fallback_role_level)
    text = " ".join(part for part in [title_text, fallback_text] if part).strip()

    if not text:
        return SeniorityEvaluation("Unknown", UNKNOWN_FIT, "unknown_seniority")

    if re.search(r"\bchief\s+of\s+staff\b", text):
        return SeniorityEvaluation("Chief of Staff", CONTEXT_DEPENDENT_FIT, "context_dependent_chief_of_staff")

    if _matches_any(text, C_SUITE_PATTERNS):
        return SeniorityEvaluation("C-suite", EXCLUDED_FIT, "likely_too_senior_c_suite", score_penalty=100, hard_exclude=True)

    if re.search(r"\bevp\b|\bexecutive\s+vice\s+president\b", text):
        return SeniorityEvaluation("EVP", EXCLUDED_FIT, "likely_too_senior_evp", score_penalty=100, hard_exclude=True)

    if re.search(r"\bsvp\b|\bsenior\s+vice\s+president\b", text):
        return SeniorityEvaluation("SVP", EXCLUDED_FIT, "likely_too_senior_svp", score_penalty=100, hard_exclude=True)

    if re.search(r"\bsr\.?\s+director\b|\bsenior\s+director\b", text):
        return SeniorityEvaluation("Senior Director", TOO_SENIOR_FIT, "likely_too_senior_senior_director", score_penalty=55)

    if re.search(r"\bvp\b|\bvice\s+president\b", text):
        return SeniorityEvaluation("VP", TOO_SENIOR_FIT, "likely_too_senior_vp", score_penalty=60)

    if re.search(r"\bhead\s+of\b", text):
        return SeniorityEvaluation("Head of", MANUAL_REVIEW_FIT, "manual_review_head_of", score_penalty=15, manual_review=True)

    if re.search(r"\bdirector\b", text):
        if _has_stretch_context(company_context):
            return SeniorityEvaluation("Director", STRETCH_FIT, "stretch_seniority_director_context_viable")
        return SeniorityEvaluation("Director", STRETCH_FIT, "stretch_seniority_director", score_penalty=5)

    if re.search(r"\bsr\.?\s+manager\b|\bsenior\s+manager\b", text):
        return SeniorityEvaluation("Senior Manager", TARGET_FIT, "target_seniority_senior_manager")

    if re.search(r"\bmanager\b", text):
        return SeniorityEvaluation("Manager", TARGET_FIT, "target_seniority_manager")

    if re.search(r"\bprincipal\b", text):
        return SeniorityEvaluation("Principal", CONTEXT_DEPENDENT_FIT, "context_dependent_principal", score_penalty=5)

    if re.search(r"\blead\b", text):
        return SeniorityEvaluation("Lead", CONTEXT_DEPENDENT_FIT, "context_dependent_lead", score_penalty=5)

    if re.search(r"\bsr\.?\b|\bsenior\b", text):
        return SeniorityEvaluation("Senior", TOO_JUNIOR_FIT, "likely_too_junior_senior_individual_contributor", score_penalty=15)

    if re.search(r"\banalyst\b|\bassociate\b|\bcoordinator\b", text):
        return SeniorityEvaluation("Analyst", TOO_JUNIOR_FIT, "likely_too_junior_analyst_or_associate", score_penalty=25)

    return SeniorityEvaluation("Unknown", UNKNOWN_FIT, "unknown_seniority")
