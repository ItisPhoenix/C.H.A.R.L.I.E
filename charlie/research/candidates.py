"""Candidate option discovery and extraction with verbatim quote gates."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set

from charlie.research.facts import normalize_whitespace, verify_quote
from charlie.research.models import Candidate, ResearchBrief, SourceDocument
from charlie.research.sources import OFFICIAL_REGISTRY, resolve_brand

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

def _normalize_candidate_name(name: str) -> str:
    """Normalize model string for deduplication."""
    cleaned = re.sub(r"\s+", " ", name).strip(" \t\r\n-:;,.")
    return cleaned


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
                )
            )

    # Rank by number of distinct source mentions descending
    final_candidates.sort(key=lambda c: c.mentions, reverse=True)

    # Keep top option_count + 2 slots
    slot_count = (brief.option_count or 3) + 2
    return final_candidates[:slot_count]
