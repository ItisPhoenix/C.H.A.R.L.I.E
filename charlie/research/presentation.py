"""Bounded, presentation-safe research and briefing payload builders."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from charlie.research.models import Citation, ResearchReport, SearchResult, SourceDocument

_CONTRACT_PATH = Path(__file__).resolve().parents[2] / "shared" / "workspace_payload_contract.json"
_CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def workspace_payload_spec(kind: str) -> dict[str, Any]:
    """Return canonical schema metadata directly from shared contract."""
    try:
        return _CONTRACT["payloads"][kind]
    except KeyError as exc:
        raise ValueError(f"Unknown workspace payload kind: {kind}") from exc


def validate_workspace_payload(payload: Any, kind: str) -> bool:
    """Validate canonical schema, supported version, and required fields."""
    if not isinstance(payload, dict):
        return False
    spec = workspace_payload_spec(kind)
    return (
        payload.get("schema") == spec["schema"]
        and payload.get("version") == spec["version"]
        and all(field in payload for field in spec.get("required", []))
    )


PAYLOAD_SCHEMA_VERSION = int(workspace_payload_spec("research")["version"])
MAX_PAYLOAD_BYTES = int(_CONTRACT["limits"]["max_serialized_bytes"])
MAX_SOURCES = int(_CONTRACT["limits"]["max_sources"])
MAX_FINDINGS = int(_CONTRACT["limits"]["max_findings"])
MAX_STORIES = int(_CONTRACT["limits"]["max_stories"])
MAX_TEXT = 2_000
MAX_SNIPPET = 500

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TRUST_LINE_RE = re.compile(
    r"^\s*(?:UNTRUSTED(?: SOURCE CONTENT| SEARCH SNIPPET)?\s*:?|URL\s*:|Research mode\s*:|"
    r"\[/?(?:RESEARCH EVIDENCE|END RESEARCH EVIDENCE)\])\s*$",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]*>")


def _clean_text(value: Any, limit: int = MAX_TEXT) -> str:
    """Create bounded plain display text; React remains responsible for escaping."""
    text = _CONTROL_RE.sub("", str(value or ""))
    text = _TAG_RE.sub("", text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    text = " ".join(line for line in lines if line and not _TRUST_LINE_RE.match(line))
    return text[:limit].strip()


def _domain(url: str, fallback: str = "") -> str:
    return (urlparse(url).netloc or fallback).lower()


def _status(report: ResearchReport) -> str:
    reason = (report.stop_reason or "").lower()
    if "cancel" in reason:
        return "cancelled"
    if "timeout" in reason:
        return "timeout"
    if "error" in reason or report.errors and not report.search_results:
        return "error"
    if "no-result" in reason or not report.search_results:
        return "no_results"
    if report.evidence:
        return "complete"
    return "partial"


def _citation_ids(report: ResearchReport) -> dict[str, Citation]:
    return {citation.source_id: citation for citation in report.citations}


def _source_records(report: ResearchReport) -> list[dict[str, Any]]:
    citations = _citation_ids(report)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_urls: dict[str, str] = {}

    documents: Iterable[SourceDocument | SearchResult]
    # Materialize fetched documents and search-only results into one source set.
    documents = [*report.sources, *report.search_results]
    for index, item in enumerate(documents):
        url = getattr(item, "canonical_url", "") or getattr(item, "url", "")
        citation = citations.get(getattr(item, "source_id", ""))
        source_id = getattr(item, "source_id", "") or (citation.source_id if citation else f"S{index + 1}")
        if not getattr(item, "source_id", "") and url and url in seen_urls:
            source_id = seen_urls[url]
        if source_id in seen:
            continue
        seen.add(source_id)
        title = _clean_text(getattr(item, "title", "") or (citation.title if citation else ""), 300)
        snippet = getattr(item, "snippet", "")
        if not snippet:
            snippet = getattr(item, "content", "")
        records.append(
            {
                "id": source_id,
                "title": title or source_id,
                "domain": _domain(url, getattr(item, "domain", "")),
                "url": url,
                "snippet": _clean_text(snippet, MAX_SNIPPET),
                "published_at": getattr(item, "published_at", None),
                "source_type": "news" if "news" in report.query.lower() else None,
                "confidence": round(float(getattr(item, "quality_score", 0.0) or 0.0), 3) or None,
            }
        )
        if url:
            seen_urls[url] = source_id
        if len(records) >= MAX_SOURCES:
            break
    return records


def _finding_records(report: ResearchReport, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_ids = {source["id"] for source in sources}
    titles = {source["id"]: source["title"] for source in sources}
    findings: list[dict[str, Any]] = []
    for index, evidence in enumerate(report.evidence[:MAX_FINDINGS], start=1):
        valid_ids = [source_id for source_id in [evidence.source_id] if source_id in source_ids]
        if not valid_ids:
            continue
        findings.append(
            {
                "id": f"F{index}",
                "title": titles.get(valid_ids[0], f"Finding {index:02d}"),
                "detail": _clean_text(evidence.statement),
                "source_ids": valid_ids,
                "confidence": round(float(evidence.confidence), 3),
                "contradiction": bool(evidence.contradiction),
            }
        )
    return findings


def _summary(report: ResearchReport, findings: list[dict[str, Any]], empty: str) -> str:
    if report.answer:
        return _clean_text(report.answer, MAX_TEXT)
    if findings:
        return _clean_text(" ".join(item["detail"] for item in findings[:2]), MAX_TEXT)
    return empty


def _bounded(payload: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        payload["findings"] = payload.get("findings", [])[:8]
        payload["stories"] = payload.get("stories", [])[:8]
        payload["sources"] = payload.get("sources", [])[:8]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            payload["summary"] = _clean_text(payload.get("summary", ""), 800)
            for item in payload.get("sources", []):
                item["snippet"] = _clean_text(item.get("snippet", ""), 180)
            for item in payload.get("findings", []):
                item["detail"] = _clean_text(item.get("detail", ""), 500)
    if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Presentation payload exceeds serialized size limit")
    return payload


def build_research_workspace_payload(report: ResearchReport) -> dict[str, Any]:
    sources = _source_records(report)
    findings = _finding_records(report, sources)
    payload = {
        "schema": workspace_payload_spec("research")["schema"],
        "version": workspace_payload_spec("research")["version"],
        "query": _clean_text(report.query, 500),
        "objective": _clean_text(report.query, 500),
        "mode": report.mode.value,
        "title": "Research & Synthesis",
        "summary": _summary(report, findings, "No grounded findings were returned."),
        "status": _status(report),
        "confidence": round(float(report.confidence), 3),
        "findings": findings,
        "sources": sources,
        "stop_reason": _clean_text(report.stop_reason, 300) or None,
    }
    dated_sources = [source for source in sources if source.get("published_at")]
    if dated_sources:
        payload["timeline_items"] = [
            {"id": f"T{index}", "kind": "published", "timestamp": source["published_at"],
             "time": source["published_at"], "title": source["title"], "summary": source["snippet"]}
            for index, source in enumerate(dated_sources[:MAX_FINDINGS], start=1)
        ]
    return _bounded(payload)


def build_briefing_workspace_payload(report: ResearchReport) -> dict[str, Any]:
    sources = _source_records(report)
    source_by_url = {source["url"]: source["id"] for source in sources if source["url"]}
    stories: list[dict[str, Any]] = []
    for index, result in enumerate(report.search_results[:MAX_STORIES], start=1):
        source_id = source_by_url.get(result.canonical_url or result.url)
        if not source_id:
            continue
        story = {
            "id": f"ST{index}",
            "title": _clean_text(result.title, 300),
            "summary": _clean_text(result.snippet, 700),
            "source_ids": [source_id],
        }
        if result.published_at:
            story["published_at"] = result.published_at
        stories.append(story)
    headline = stories[0]["title"] if stories else "Daily Intelligence Briefing"
    timeline = [
        {
            "id": story["id"],
            "kind": "published",
            "timestamp": story["published_at"],
            "time": story["published_at"],
            "title": story["title"],
            "summary": story["summary"],
        }
        for story in stories
        if story.get("published_at")
    ]
    summary = _clean_text(report.answer, MAX_TEXT) if report.answer else _clean_text(
        " ".join(story["summary"] for story in stories[:2]), MAX_TEXT
    )
    payload = {
        "schema": workspace_payload_spec("briefing")["schema"],
        "version": workspace_payload_spec("briefing")["version"],
        "title": "Daily Briefing",
        "headline": headline,
        "summary": summary or "No grounded briefing stories were returned.",
        "stories": stories,
        "summaries": [story["summary"] for story in stories],
        "timeline_items": timeline,
        "sources": sources,
        "status": _status(report),
        "confidence": round(float(report.confidence), 3),
    }
    return _bounded(payload)


def build_presentation_payload(report: ResearchReport, *, briefing: bool = False) -> dict[str, Any]:
    return build_briefing_workspace_payload(report) if briefing else build_research_workspace_payload(report)
