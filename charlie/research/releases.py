"""Stable software release version and release date resolution."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from charlie.research.models import Fact, ResearchBrief, SourceDocument
from charlie.research.sources import OFFICIAL_REGISTRY, get_official_domains, resolve_brand

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


def pick_stable(
    facts_or_docs: Iterable[Any],
    brief: Optional[ResearchBrief] = None,
) -> Optional[Tuple[str, str, str]]:
    """Pick highest final/stable version and release date: (version, date, source_id).

    Excludes alpha, beta, rc, pre, dev releases.
    """
    items = list(facts_or_docs)
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
                ver = item.value
                if is_stable_version(ver):
                    parsed = parse_version_tuple(ver)
                    if parsed:
                        key = (parsed[0], parsed[1], parsed[2])
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
    best = stable_candidates[0]
    return (best[1], best[2], best[3])


def get_canonical_release_paths(domain_or_brand: str) -> List[str]:
    """Return canonical release paths from registry for a brand or domain."""
    for key, info in OFFICIAL_REGISTRY.items():
        if key == domain_or_brand.lower() or domain_or_brand.lower() in info.get("domains", set()):
            return list(info.get("canonical_paths", []))
    return []


def canonical_release_urls(brief: ResearchBrief) -> List[str]:
    """Reuse the official registry even when a search provider returns nothing."""
    brand = resolve_brand(brief.topic)
    domains = brief.explicit_domains or (get_official_domains(brand) if brand else [])
    return list(dict.fromkeys(
        f"https://www.{domain.removeprefix('www.')}{path}"
        for domain in domains for path in get_canonical_release_paths(domain.removeprefix("www."))
    ))
