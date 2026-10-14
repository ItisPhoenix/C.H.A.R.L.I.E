"""Stable software release version and release date resolution."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from charlie.research.models import Fact, ResearchBrief, SourceDocument

# Regex for semver and PEP 440 version strings
# Matches: 3.13.2, 3.14.0rc1, 3.12.8, etc.
_VERSION_PARSE_RE = re.compile(
    r"\b(\d+)\.(\d+)(?:\.(\d+))?(?:([a-zA-Z]+)(\d+))?\b"
)

_PRE_RELEASE_TOKENS = {"a", "alpha", "b", "beta", "rc", "c", "pre", "preview", "dev", "nightly"}

# Release page extraction patterns
_PYTHON_RELEASE_ROW_RE = re.compile(
    r"(?:(?:Python|Node(?:\.js)?|Rust|Go|Linux(?:\s+Kernel)?|Release|Version|v)?\s*)?"
    r"(\d+\.\d+(?:\.\d+)?)\s*[-–—:|]?\s*(?:\([^\)]*\)\s*)?(?:(?:was\s+)?(?:released|dated)(?:\s+on)?[:\s]+)?("
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
    r"\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def parse_version_tuple(ver_str: str) -> Optional[Tuple[int, int, int, str, int]]:
    """Parse version string into (major, minor, micro, pre_tag, pre_num)."""
    m = _VERSION_PARSE_RE.search(ver_str)
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2))
    micro = int(m.group(3) or 0)
    pre_tag = (m.group(4) or "").lower()
    pre_num = int(m.group(5) or 0)
    return (major, minor, micro, pre_tag, pre_num)


def is_stable_version(ver_str: str) -> bool:
    """Return True if the version is final/stable, not alpha/beta/rc/dev."""
    parsed = parse_version_tuple(ver_str)
    if not parsed:
        return False
    pre_tag = parsed[3]
    return not any(token in pre_tag for token in _PRE_RELEASE_TOKENS)


def stable_release_span(
    document: SourceDocument, version: str, released: str = ""
) -> Optional[Tuple[int, int]]:
    """Return the exact source row that ties a stable version to its date."""
    expected_date = " ".join(released.split()).casefold()
    for match in _PYTHON_RELEASE_ROW_RE.finditer(document.content):
        if match.group(1).casefold() != version.casefold():
            continue
        if expected_date and " ".join(match.group(2).split()).casefold() != expected_date:
            continue
        return match.span()
    return None


def pick_stable(
    facts_or_docs: Iterable[Any],
    brief: Optional[ResearchBrief] = None,
) -> Optional[Tuple[str, str, str]]:
    """Pick highest final/stable version and release date: (version, date, source_id).

    Excludes alpha, beta, rc, pre, dev releases.
    """
    items = list(facts_or_docs)
    requested = parse_version_tuple(brief.requested_version) if brief and brief.requested_version else None
    lts_requested = bool(brief and re.search(r"\bLTS\b", brief.original_request or brief.topic, re.I))
    stable_candidates: List[Tuple[Tuple[int, int, int], str, str, str]] = []

    # Map dates by: candidate, quote content, and source_id
    dates_by_cand: Dict[str, str] = {}
    dates_by_quote: List[Tuple[str, str]] = []
    dates_by_src: Dict[str, str] = {}
    for item in items:
        if isinstance(item, Fact) and item.aspect == "release_date" and item.value:
            if item.candidate:
                dates_by_cand[item.candidate.strip().lower()] = item.value
            if item.quote:
                dates_by_quote.append((item.quote.lower(), item.value))
            dates_by_src.setdefault(item.source_id, item.value)

    for item in items:
        if isinstance(item, SourceDocument):
            prior_count = len(stable_candidates)
            if lts_requested:
                from charlie.research.facts import _DATE_RE

                if item.content.lstrip().startswith("["):
                    try:
                        release_rows = json.loads(item.content)
                    except (TypeError, ValueError):
                        release_rows = None
                    if isinstance(release_rows, list):
                        for row in release_rows:
                            if not isinstance(row, dict) or not row.get("lts"):
                                continue
                            raw_version = str(row.get("version") or "").strip()
                            version = re.sub(r"^v(?=\d)", "", raw_version, flags=re.I)
                            parsed = parse_version_tuple(version) if is_stable_version(version) else None
                            if parsed is None or (requested is not None and parsed[:3] != requested[:3]):
                                continue
                            stable_candidates.append((
                                parsed[:3], raw_version,
                                str(row.get("date") or "").strip(), item.source_id,
                            ))
                        if len(stable_candidates) > prior_count:
                            continue

                for line in re.split(r"[\r\n]+", item.content):
                    if not re.search(r"\bLTS\b", line, re.I):
                        continue
                    version_text = re.sub(r"\bv(?=\d)", "", line, flags=re.I)
                    versions = list(dict.fromkeys(
                        match.group(0)
                        for match in _VERSION_PARSE_RE.finditer(version_text)
                        if is_stable_version(match.group(0))
                    ))
                    if len(versions) != 1:
                        continue
                    version = versions[0]
                    parsed = parse_version_tuple(version)
                    if parsed is None or (requested is not None and parsed[:3] != requested[:3]):
                        continue
                    dates = list(_DATE_RE.finditer(line))
                    date_value = dates[0].group(1).strip() if len(dates) == 1 else ""
                    stable_candidates.append((parsed[:3], version, date_value, item.source_id))
                continue
            if requested is not None:
                from charlie.research.facts import _DATE_RE

                for line in re.split(r"[\r\n]+", item.content):
                    versions = list(dict.fromkeys(
                        match.group(0)
                        for match in _VERSION_PARSE_RE.finditer(line)
                        if is_stable_version(match.group(0))
                    ))
                    matching = [
                        version
                        for version in versions
                        if parse_version_tuple(version)[:3] == requested[:3]
                    ]
                    if len(versions) != 1 or len(matching) != 1:
                        continue
                    date_matches = list(_DATE_RE.finditer(line))
                    if len(date_matches) > 1:
                        continue
                    date_value = date_matches[0].group(1).strip() if date_matches else ""
                    stable_candidates.append((requested[:3], matching[0], date_value, item.source_id))
                continue
            # Scan document content for canonical release rows
            for m in _PYTHON_RELEASE_ROW_RE.finditer(item.content):
                ver = m.group(1)
                date_str = m.group(2).strip()
                if is_stable_version(ver):
                    parsed = parse_version_tuple(ver)
                    if parsed:
                        key = (parsed[0], parsed[1], parsed[2])
                        stable_candidates.append((key, ver, date_str, item.source_id))
            # Fallback scan if strict row regex didn't find candidates
            if len(stable_candidates) == prior_count:
                ver_matches = re.findall(r"\b(?:Python|Node|Rust|Release|Version|v)?\s*(\d+\.\d+\.\d+)\b", item.content, re.I)
                dt_matches = re.findall(
                    r"\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
                    r"\s+\d{1,2},?\s+\d{4})\b",
                    item.content,
                    re.I,
                )
                for ver in ver_matches:
                    if is_stable_version(ver):
                        parsed = parse_version_tuple(ver)
                        if parsed:
                            key = (parsed[0], parsed[1], parsed[2])
                            dt_str = dt_matches[0] if len(set(ver_matches)) == 1 and len(set(dt_matches)) == 1 else ""
                            stable_candidates.append((key, ver, dt_str, item.source_id))
        elif isinstance(item, Fact):
            if item.aspect == "version":
                if lts_requested and not re.search(r"\bLTS\b", item.quote, re.I):
                    continue
                ver = item.value
                if is_stable_version(ver):
                    parsed = parse_version_tuple(ver)
                    if parsed:
                        key = (parsed[0], parsed[1], parsed[2])
                        if requested is not None and key != requested[:3]:
                            continue
                        dt_str = ""
                        if item.quote:
                            from charlie.research.facts import _DATE_RE
                            dm = _DATE_RE.search(item.quote)
                            if dm:
                                dt_str = dm.group(1).strip()
                        if not dt_str and item.candidate:
                            dt_str = dates_by_cand.get(item.candidate.strip().lower(), "")
                        if not dt_str:
                            for q, dval in dates_by_quote:
                                if ver in q:
                                    dt_str = dval
                                    break
                        source_versions = {f.value for f in items if isinstance(f, Fact)
                                           and f.aspect == "version" and f.source_id == item.source_id}
                        if not dt_str and item.source_id and len(source_versions) == 1:
                            dt_str = dates_by_src.get(item.source_id, "")
                        stable_candidates.append((key, ver, dt_str, item.source_id))

    if not stable_candidates:
        return None

    # Sort by version tuple descending
    stable_candidates.sort(key=lambda x: x[0], reverse=True)
    best_group = [value for value in stable_candidates if value[0] == stable_candidates[0][0]]
    best_dates = {value[2].casefold() for value in best_group if value[2]}
    if len(best_dates) > 1:
        return None
    best = next((value for value in best_group if value[2]), best_group[0])
    return (best[1], best[2], best[3])
