"""Structured connector interfaces for priority ATS and career sources."""

from src.connectors.ats import discover_ats_candidates
from src.connectors.models import ConnectorLimits, ConnectorResult, ConnectorStatus

__all__ = ["ConnectorLimits", "ConnectorResult", "ConnectorStatus", "discover_ats_candidates"]
