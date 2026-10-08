"""Candidate option discovery and extraction with verbatim quote gates."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set

from charlie.research.facts import normalize_whitespace, verify_quote
from charlie.research.models import Candidate, ResearchBrief, SourceDocument
from charlie.research.sources import resolve_brand

# Regex pattern for hardware model tokens following brand
_LAPTOP_MODEL_PATTERN = re.compile(
    r"\b(Lenovo|ASUS|HP|Dell|Acer|MSI|Apple|Samsung|Razer|Gigabyte)\s+"
    r"((?:LOQ|Legion|IdeaPad|ThinkPad|Yoga|TUF(?:\s+Gaming)?|ROG(?:\s+Strix|\s+Zephyrus)?|"
    r"Victus|Omen|Pavilion|Alienware|G15|G16|XPS|Inspiron|Nitro(?:\s+V)?|Predator(?:\s+Helios)?|"
    r"Katana|Cyborg|Bravo|Thin|Sword|MacBook(?:\s+(?:Air|Pro))?|Galaxy\s+Book|Blade)\s*"
    r"[A-Za-z0-9-]{0,15}(?:\s+[A-Za-z0-9-]{1,10})?)\b",
    re.IGNORECASE,
)

_PHONE_MODEL_PATTERN = re.compile(
    r"\b(Samsung|Apple|Google|OnePlus|Xiaomi)\s+"
    r"((?:Galaxy\s+S\d{2}(?:\s+(?:Ultra|Plus|\+))?|iPhone\s+\d{1,2}(?:\s+(?:Pro(?:\s+Max)?|Plus))?|"
    r"Pixel\s+\d{1,2}(?:\s+Pro)?|OnePlus\s+\d{1,2}(?:\s+Pro|R)?|Xiaomi\s+\d{1,2}(?:\s+Pro)?))\b",
    re.IGNORECASE,
)

_LLM_TOPIC_RE = re.compile(r"\b(?:llms?|large\s+language\s+models?)\b", re.I)
_LLM_RELEASE_STATEMENT_RE = re.compile(
    r"(?P<brand>[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,2})"
    r"(?:\s*\([^\)\r\n]{1,80}\))?\s+"
    r"(?P<verb>(?i:releases?|released|launches?|launched|debuts?|debuted|introduces?|introduced|publishes?|published|open-sources?|open-sourced))"
    r"\s+(?:the\s+|its\s+(?:first\s+)?)?"
    r"(?P<model>[A-Z0-9][A-Za-z0-9.+-]*(?:\s+[A-Z0-9][A-Za-z0-9.+-]*){0,3})"
)
_LLM_MODEL_FIRST_RELEASE_RE = re.compile(
    r"(?P<model>[A-Z0-9][A-Za-z0-9.+-]*(?:\s+[A-Z0-9][A-Za-z0-9.+-]*){0,3})"
    r"(?:\s+\([^\)\r\n]{1,80}\))?"
    r".{0,160}?\b(?i:released|launched|debuted|introduced|published)\b"
    r".{0,60}?\b(?P<date>20\d{2}-\d{2}-\d{2})\b"
)
_RELEASE_DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
_FUTURE_OPEN_WEIGHTS_RE = re.compile(
    r"\b(?:open[- ]?weights?|weights?)\b.{0,50}\b(?:promised|planned|will be released|will be published|expected later)\b|"
    r"\b(?:will|would|plan(?:s|ned)? to|promise[sd]? to)\s+(?:release|publish|open[- ]source)\b",
    re.I,
)

def _normalize_candidate_name(name: str) -> str:
    """Normalize model string for deduplication."""
    cleaned = re.sub(r"\s+", " ", name).strip(" \t\r\n-:;,.")
    return cleaned


def _merge_release_candidate(existing: Optional[Candidate], candidate: Candidate) -> Candidate:
    if existing is None:
        return candidate
    existing_date = existing.release_date or ""
    candidate_date = candidate.release_date or ""
    newest = candidate if candidate_date > existing_date else existing
    if existing.access and candidate.access and existing.access != candidate.access:
        access = None
    else:
        access = candidate.access or existing.access
    quoted = candidate if candidate.access and (not existing.access or candidate.access == existing.access) else existing
    if not quoted.quote:
        quoted = newest
    return Candidate(
        name=newest.name,
        brand=newest.brand or existing.brand or candidate.brand,
        quote=quoted.quote or newest.quote,
        source_id=quoted.source_id or newest.source_id,
        mentions=max(existing.mentions, candidate.mentions),
        access=access,
        release_date=newest.release_date or existing.release_date or candidate.release_date,
    )


def extract_candidates(
    documents: Iterable[SourceDocument],
    brief: ResearchBrief,
    model_proposals: Optional[List[Dict[str, Any]]] = None,
) -> List[Candidate]:
    """Extract candidate options from fetched documents with verbatim grounding.

    Candidates are discovered deterministically and/or from model proposals.
    Every candidate MUST be grounded in a fetched document verbatim quote.
    """
    candidates_by_key: Dict[str, Candidate] = {}
    mentions_by_key: Dict[str, Set[str]] = {}  # key -> set of source_ids
    docs_list = list(documents)
    doc_map = {doc.source_id: doc for doc in docs_list if doc.source_id}

    # 1. Deterministic pattern extraction
    for doc in docs_list:
        text = doc.content
        lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]

        for line in lines:
            # Check laptop pattern
            for match in _LAPTOP_MODEL_PATTERN.finditer(line):
                brand = match.group(1).title()
                full_name = _normalize_candidate_name(match.group(0))
                # Skip if too generic (e.g. just "Lenovo LOQ" with nothing else when longer names exist)
                norm_key = full_name.lower()
                quote = normalize_whitespace(line[:250])
                if norm_key not in candidates_by_key:
                    candidates_by_key[norm_key] = Candidate(
                        name=full_name,
                        brand=brand,
                        quote=quote,
                        source_id=doc.source_id,
                    )
                mentions_by_key.setdefault(norm_key, set()).add(doc.source_id)
            # Check phone pattern
            for match in _PHONE_MODEL_PATTERN.finditer(line):
                brand = match.group(1).title()
                full_name = _normalize_candidate_name(match.group(0))
                norm_key = full_name.lower()
                quote = normalize_whitespace(line[:250])
                if norm_key not in candidates_by_key:
                    candidates_by_key[norm_key] = Candidate(
                        name=full_name,
                        brand=brand,
                        quote=quote,
                        source_id=doc.source_id,
                    )
                mentions_by_key.setdefault(norm_key, set()).add(doc.source_id)

    if brief.entity_kind == "release" and _LLM_TOPIC_RE.search(brief.topic):
        for doc in docs_list:
            for line in (part.strip() for part in re.split(r"[\n\r]+", doc.content) if part.strip()):
                candidate_text = re.sub(r"(?i)(?:released|preview)(?=[A-Z])", " ", line)
                fragments = [
                    fragment.strip()
                    for fragment in re.split(r"(?<=[.!?;])\s+|\s*\|\s*", candidate_text)
                    if fragment.strip()
                ]
                for fragment in fragments:
                    statement_matches = list(_LLM_RELEASE_STATEMENT_RE.finditer(fragment))
                    for index, match in enumerate(statement_matches):
                        context_end = (
                            statement_matches[index + 1].start()
                            if index + 1 < len(statement_matches)
                            else len(fragment)
                        )
                        statement = fragment[match.start():context_end]
                        release_date = _RELEASE_DATE_RE.search(statement)
                        name = _normalize_candidate_name(match.group("model"))
                        brand = re.split(r"['’]s\b", match.group("brand"), maxsplit=1)[0].strip()
                        brand = re.sub(r"\s+(?:LLM|team|group)$", "", brand, flags=re.I)
                        if len(name) < 2 or not brand:
                            continue
                        access = (
                            "open"
                            if re.search(r"\bopen[- ](?:weight|weights|source)\b", statement, re.I)
                            else "closed"
                            if re.search(r"\b(?:closed[- ]source|proprietary)\b", statement, re.I)
                            else None
                        )
                        if access == "open" and _FUTURE_OPEN_WEIGHTS_RE.search(statement):
                            access = None
                        key = name.casefold()
                        candidate = Candidate(
                            name=name,
                            brand=brand,
                            quote=normalize_whitespace(statement[:500]),
                            source_id=doc.source_id,
                            access=access,
                            release_date=release_date.group(0) if release_date else None,
                        )
                        existing = candidates_by_key.get(key)
                        candidates_by_key[key] = _merge_release_candidate(existing, candidate)
                        mentions_by_key.setdefault(key, set()).add(doc.source_id)

                    if statement_matches:
                        continue
                    release_date = _RELEASE_DATE_RE.search(fragment)
                    if not release_date:
                        continue
                    match = _LLM_MODEL_FIRST_RELEASE_RE.search(fragment)
                    if not match:
                        continue
                    name = _normalize_candidate_name(match.group("model"))
                    access = (
                        "open"
                        if re.search(r"\bopen[- ](?:weight|weights|source)\b", fragment, re.I)
                        else "closed"
                        if re.search(r"\b(?:closed[- ]source|proprietary)\b", fragment, re.I)
                        else None
                    )
                    if access == "open" and _FUTURE_OPEN_WEIGHTS_RE.search(fragment):
                        access = None
                    key = name.casefold()
                    candidate = Candidate(
                        name=name,
                        brand=name.split()[0],
                        quote=normalize_whitespace(fragment[:500]),
                        source_id=doc.source_id,
                        access=access,
                        release_date=release_date.group(0),
                    )
                    existing = candidates_by_key.get(key)
                    candidates_by_key[key] = _merge_release_candidate(existing, candidate)
                    mentions_by_key.setdefault(key, set()).add(doc.source_id)

    # 2. Model proposals (must pass verbatim quote gate)
    if model_proposals:
        for item in model_proposals:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            brand = item.get("brand") or resolve_brand(name or "")
            quote = item.get("quote")
            source_id = item.get("source_id")

            if not name or not quote or not source_id:
                continue

            doc = doc_map.get(source_id)
            if not doc:
                continue

            # Verbatim quote gate
            if not verify_quote(quote, doc):
                continue

            # Ensure name or brand appears in quote
            norm_name = _normalize_candidate_name(name)
            norm_quote = normalize_whitespace(quote).lower()
            if norm_name.lower() not in norm_quote and (brand and brand.lower() not in norm_quote):
                continue

            norm_key = norm_name.lower()
            if norm_key not in candidates_by_key:
                candidates_by_key[norm_key] = Candidate(
                    name=norm_name,
                    brand=brand.title() if brand else None,
                    quote=normalize_whitespace(quote[:250]),
                    source_id=source_id,
                )
            mentions_by_key.setdefault(norm_key, set()).add(source_id)

    # Clean up candidates: deduplicate subsets (e.g. "Lenovo LOQ" vs "Lenovo LOQ 15IAX9")
    final_candidates: List[Candidate] = []
    sorted_keys = sorted(candidates_by_key.keys(), key=len, reverse=True)
    kept_keys: Set[str] = set()

    for key in sorted_keys:
        # Check if this key is a prefix/substring of an already kept key with same brand
        is_subset = False
        cand = candidates_by_key[key]
        for kept in kept_keys:
            if key in kept and key != kept:
                # Merge mentions into the more specific candidate
                mentions_by_key[kept].update(mentions_by_key.get(key, set()))
                is_subset = True
                break
        if not is_subset:
            kept_keys.add(key)
            # Create updated candidate with total mentions
            mentions_count = len(mentions_by_key.get(key, set()))
            final_candidates.append(
                Candidate(
                    name=cand.name,
                    brand=cand.brand,
                    quote=cand.quote,
                    source_id=cand.source_id,
                    mentions=max(1, mentions_count),
                    access=cand.access,
                    release_date=cand.release_date,
                )
            )

    if brief.entity_kind == "release" and _LLM_TOPIC_RE.search(brief.topic):
        buckets = {
            access: sorted(
                (candidate for candidate in final_candidates if candidate.access == access),
                key=lambda candidate: (candidate.release_date or "", candidate.mentions),
                reverse=True,
            )
            for access in ("closed", "open")
        }
        ordered: List[Candidate] = []
        while any(buckets.values()):
            for access in ("closed", "open"):
                if buckets[access]:
                    ordered.append(buckets[access].pop(0))
        other = [candidate for candidate in final_candidates if candidate.access not in buckets]
        other.sort(key=lambda candidate: (candidate.release_date or "", candidate.mentions), reverse=True)
        final_candidates = ordered + other
    else:
        final_candidates.sort(key=lambda c: c.mentions, reverse=True)

    # Keep top option_count + 2 slots
    slot_count = (brief.option_count or 3) + 2
    return final_candidates[:slot_count]
