"""Deterministic and model-assisted fact extraction and verbatim quote verification."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from charlie.research.models import Fact, SourceDocument
from charlie.research.sources import citable_for, classify

# Regular expressions for deterministic hardware/software spec extraction
_INR_PRICE_RE = re.compile(
    r"(?:(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)(?:\s*(?:lakh|lac|cr|crore))?|"
    r"([\d,]+(?:\.\d+)?)\s*(?:₹|rs\.?|inr|rupees?))",
    re.IGNORECASE,
)
_LAKH_PRICE_RE = re.compile(r"([\d.]+)\s*(?:lakh|lac)\s*(?:rupees?|inr|rs\.?)?", re.IGNORECASE)




def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace and unicode spaces for strict verbatim comparison."""
    return re.sub(r"\s+", " ", text).strip()


def verify_quote(quote: str, document_or_content: SourceDocument | str) -> bool:
    """Verify that quote is a verbatim substring of the document content (whitespace-normalised).

    Decision 6: Every candidate and fact must quote a verbatim span from a fetched page.
    """
    if not quote or not quote.strip():
        return False
    content = (
        document_or_content.content
        if isinstance(document_or_content, SourceDocument)
        else document_or_content
    )
    if not content:
        return False
    norm_quote = normalize_whitespace(quote).lower()
    norm_content = normalize_whitespace(content).lower()
    return norm_quote in norm_content


def parse_inr_price(text: str) -> Optional[float]:
    """Extract numeric INR price, handling ₹, Rs, INR, comma grouping, and lakhs."""
    m_lakh = _LAKH_PRICE_RE.search(text)
    if m_lakh:
        try:
            return float(m_lakh.group(1)) * 100000.0
        except ValueError:
            pass
    m = _INR_PRICE_RE.search(text)
    if m:
        num_str = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            val = float(num_str)
            if "lakh" in text.lower() or "lac" in text.lower():
                val *= 100000.0
            return val
        except ValueError:
            pass
    return None


def product_identifiers(text: str) -> Set[str]:
    """Read explicit alphanumeric identifiers without a model-family catalogue."""
    return {
        token.casefold()
        for token in re.findall(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b", text)
        if len(token) >= 6 and re.search(r"[A-Za-z]", token)
        and len(re.findall(r"\d", token)) >= 2
    }


def matches_product_variant(name: str, title: str, url: str, span: str) -> bool:
    identifiers = product_identifiers(name)
    span_ids = product_identifiers(span)
    if identifiers:
        if span_ids - identifiers:
            return False
        identity = f"{title} {url} {span}".casefold()
        return all(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", identity) for token in identifiers)
    if span_ids or not re.search(r"\d", name):
        return False
    return normalize_whitespace(name).casefold() in normalize_whitespace(f"{title} {url} {span}").casefold()


def normalized_fact_value(fact: Fact) -> str:
    value = normalize_whitespace(fact.value).casefold()
    return re.sub(r"\s+", "", value)


def conflicting_facts(facts: Iterable[Fact]) -> bool:
    values: Dict[Tuple[str, str], Set[str]] = {}
    for fact in facts:
        if fact.value:
            subject = normalize_whitespace(fact.candidate or "").casefold()
            key = (subject, fact.aspect)
            values.setdefault(key, set()).add(normalized_fact_value(fact))
    return any(len(group) > 1 for group in values.values())


def extract_facts_from_document(
    doc: SourceDocument,
    candidate_name: Optional[str] = None,
    candidate_brand: Optional[str] = None,
    policy: str = "official_required",
    *,
    proposals: Optional[List[Dict[str, Any]]] = None,
) -> List[Fact]:
    """Compatibility entrypoint for source-backed model extraction, with no field taxonomy."""
    facts = model_facts(proposals or [], [doc], policy=policy)
    return [fact for fact in facts if not candidate_name or fact.candidate == candidate_name]


def model_facts(
    proposals: List[Dict[str, Any]],
    documents: List[SourceDocument],
    policy: str = "official_required",
) -> List[Fact]:
    """Validate model-proposed facts against documents. Kept ONLY if quote is verified."""
    doc_map = {doc.source_id: doc for doc in documents if doc.source_id}
    url_map = {doc.url: doc for doc in documents}
    valid_facts: List[Fact] = []

    for item in proposals:
        if not isinstance(item, dict):
            continue
        candidate = item.get("candidate") or item.get("name")
        aspect = item.get("aspect")
        value = item.get("value")
        quote = item.get("quote")
        source_id = item.get("source_id") or ""
        url = item.get("url") or ""

        if not all(isinstance(field, str) and field.strip() for field in (aspect, value, quote)):
            continue

        doc = doc_map.get(source_id) or url_map.get(url)
        if not doc:
            continue

        # Decision 6 gate: verbatim quote check
        if not verify_quote(quote, doc):
            continue

        # A matching number alone does not establish an otherwise invented value.
        norm_val = normalize_whitespace(str(value)).lower()
        norm_quote = normalize_whitespace(quote).lower()
        if norm_val not in norm_quote:
            continue
        if candidate and (
            not isinstance(candidate, str)
            or normalize_whitespace(candidate).casefold() not in normalize_whitespace(doc.title + " " + quote).casefold()
        ):
            continue

        s_class = doc.source_class or classify(doc.url).value
        if not citable_for(aspect, s_class, policy):
            continue

        fetched_at_str = doc.fetched_at.isoformat() if hasattr(doc, "fetched_at") and doc.fetched_at else ""
        valid_facts.append(
            Fact(
                candidate=candidate,
                aspect=aspect,
                value=str(value).strip(),
                quote=quote.strip(),
                source_id=doc.source_id,
                source_class=s_class,
                extractor="model",
                url=doc.url,
                fetched_at=fetched_at_str,
            )
        )

    return valid_facts


def fact_table(report: Any) -> str:
    """Format facts into a cited Markdown table for synthesis."""
    if not getattr(report, "facts", None):
        return "No verified facts extracted."

    facts_by_cand: Dict[str, Dict[str, List[Fact]]] = {}

    for f in report.facts:
        cand_name = f.candidate or "General"
        facts_by_cand.setdefault(cand_name, {}).setdefault(f.aspect, []).append(f)

    lines: List[str] = []
    for cand, aspects in facts_by_cand.items():
        lines.append(f"### Option: {cand}")
        lines.append("| Aspect | Verified Value | Source | Source Class | Verbatim Quote |")
        lines.append("| --- | --- | --- | --- | --- |")
        for aspect, flist in aspects.items():
            for f in flist:
                s_label = f"[{f.source_id}]" if f.source_id else "Verified"
                class_desc = f.source_class
                lines.append(
                    f"| {aspect} | {f.value} | {s_label} | {class_desc} | \"{f.quote[:80]}\" |"
                )
        lines.append("")

    return "\n".join(lines)
