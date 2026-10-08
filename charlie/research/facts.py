"""Deterministic and model-assisted fact extraction and verbatim quote verification."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from charlie.research.models import Candidate, Fact, SourceClass, SourceDocument
from charlie.research.sources import classify, citable_for

# Regular expressions for deterministic hardware/software spec extraction
_INR_PRICE_RE = re.compile(
    r"(?:(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)(?:\s*(?:lakh|lac|cr|crore))?|"
    r"([\d,]+(?:\.\d+)?)\s*(?:₹|rs\.?|inr|rupees?))",
    re.IGNORECASE,
)
_LAKH_PRICE_RE = re.compile(r"([\d.]+)\s*(?:lakh|lac)\s*(?:rupees?|inr|rs\.?)?", re.IGNORECASE)

_GPU_RE = re.compile(
    r"\b(?:NVIDIA\s+)?(?:GeForce\s+)?(RTX\s*(?:4090|4080|4070|4060|4050|3080|3070|3060|3050(?:\s*6GB)?|2050|2060)|"
    r"GTX\s*(?:1650|1660\s*Ti)|"
    r"Radeon\s+(?:RX\s*\d{4}[A-Z]*|780M|680M)|"
    r"Intel\s+(?:Arc\s+[A-Z]\d+|Iris\s+X[e]|Graphics)|"
    r"Apple\s+M[1234](?:\s+(?:Pro|Max|Ultra))?)\b",
    re.IGNORECASE,
)

_VRAM_RE = re.compile(
    r"\b(\d{1,2}\s*GB)\s*(?:GDDR[56]X?|VRAM|dedicated|video\s+memory)\b|"
    r"\b(?:with\s+)?(\d{1,2}\s*GB)\s*(?:VRAM|graphics\s+memory)\b",
    re.IGNORECASE,
)

_RAM_RE = re.compile(
    r"\b(\d{1,3}\s*GB)\s*(?:DDR[45]|LPDDR[45]X?|RAM|system\s+memory|unified\s+memory)\b|"
    r"\bmemory[:\s]+(\d{1,3}\s*GB)\b",
    re.IGNORECASE,
)

_RAM_UPGRADE_RE = re.compile(
    r"\b(up\s+to\s+\d{1,3}\s*GB|expandable\s+to\s+\d{1,3}\s*GB|upgradable\s+to\s+\d{1,3}\s*GB|"
    r"\d\s*x\s*SO-DIMM|2x\s*slots|two\s*slots|dual\s*channel\s*capable|soldered|onboard|not\s*upgradable|non-upgradable)\b",
    re.IGNORECASE,
)

_STORAGE_RE = re.compile(
    r"\b(\d{1,3}\s*(?:GB|TB))\s*(?:PCIe\s*(?:Gen\s*\d)?\s*NVMe\s*M\.2\s*SSD|NVMe\s*SSD|SSD|PCIe\s*SSD)\b|"
    r"\bstorage[:\s]+(\d{1,3}\s*(?:GB|TB)\s*SSD)\b",
    re.IGNORECASE,
)

_CPU_RE = re.compile(
    r"\b(Intel\s+(?:Core\s+)?(?:i[3579]|Ultra\s+[579])[\w\s-]{1,20}|"
    r"AMD\s+Ryzen\s+[3579][\w\s-]{1,20}|"
    r"Apple\s+M[1234][\w\s-]{0,10})\b",
    re.IGNORECASE,
)

_VERSION_RE = re.compile(
    r"\b(?:Python|Release|Version|v)\s*(\d+\.\d+(?:\.\d+)?(?:(?:a|b|rc)\d+)?)\b|"
    r"\b(\d+\.\d+\.\d+)\b",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"\b(?:released\s+on\s+|release\s+date[:\s]+|dated[:\s]+)?("
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
    r"\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
    r"\s+\d{4})\b",
    re.IGNORECASE,
)


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


def extract_facts_from_document(
    doc: SourceDocument,
    candidate_name: Optional[str] = None,
    candidate_brand: Optional[str] = None,
    policy: str = "official_required",
) -> List[Fact]:
    """Extract hardware/software facts deterministically from a fetched document."""
    facts: List[Fact] = []
    text = doc.content
    s_class = doc.source_class or classify(doc.url, candidate_brand=candidate_brand).value
    fetched_at_str = doc.fetched_at.isoformat() if hasattr(doc, "fetched_at") and doc.fetched_at else ""

    # Split into lines and sentences
    lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]

    for line in lines:
        norm_line = normalize_whitespace(line)
        if len(norm_line) < 3:
            continue
        fact_line = norm_line.replace("®", "").replace("™", "").replace("℠", "")

        # 1. Price
        non_price_amount = re.search(r"\b(?:save|savings?|emi|instalments?|installments?|monthly)\b", fact_line, re.I)
        if citable_for("price", s_class, policy) and not non_price_amount:
            price_val = parse_inr_price(fact_line)
            if price_val and 5000 <= price_val <= 1000000:
                # Format price nicely
                formatted_price = f"₹{int(price_val):,}"
                facts.append(
                    Fact(
                        candidate=candidate_name,
                        aspect="price",
                        value=formatted_price,
                        quote=norm_line[:200],
                        source_id=doc.source_id,
                        source_class=s_class,
                        extractor="regex:price",
                        url=doc.url,
                        fetched_at=fetched_at_str,
                    )
                )

        # 2. GPU
        if citable_for("gpu", s_class, policy):
            gpu_m = _GPU_RE.search(fact_line)
            if gpu_m:
                val = normalize_whitespace(gpu_m.group(0))
                facts.append(
                    Fact(
                        candidate=candidate_name,
                        aspect="gpu",
                        value=val,
                        quote=norm_line[:200],
                        source_id=doc.source_id,
                        source_class=s_class,
                        extractor="regex:gpu",
                        url=doc.url,
                        fetched_at=fetched_at_str,
                    )
                )

        # 3. VRAM
        if citable_for("vram", s_class, policy):
            vram_m = _VRAM_RE.search(fact_line)
            if vram_m:
                val = normalize_whitespace(vram_m.group(1) or vram_m.group(2))
                facts.append(
                    Fact(
                        candidate=candidate_name,
                        aspect="vram",
                        value=val,
                        quote=norm_line[:200],
                        source_id=doc.source_id,
                        source_class=s_class,
                        extractor="regex:vram",
                        url=doc.url,
                        fetched_at=fetched_at_str,
                    )
                )

        # 4. RAM
        if citable_for("ram", s_class, policy):
            ram_m = _RAM_RE.search(fact_line)
            if ram_m:
                val = normalize_whitespace(ram_m.group(1) or ram_m.group(2))
                facts.append(
                    Fact(
                        candidate=candidate_name,
                        aspect="ram",
                        value=val,
                        quote=norm_line[:200],
                        source_id=doc.source_id,
                        source_class=s_class,
                        extractor="regex:ram",
                        url=doc.url,
                        fetched_at=fetched_at_str,
                    )
                )

        # 5. RAM Upgradeability
        if citable_for("ram_upgradeable", s_class, policy):
            upg_m = _RAM_UPGRADE_RE.search(fact_line)
            if upg_m:
                val = normalize_whitespace(upg_m.group(0))
                facts.append(
                    Fact(
                        candidate=candidate_name,
                        aspect="ram_upgradeable",
                        value=val,
                        quote=norm_line[:200],
                        source_id=doc.source_id,
                        source_class=s_class,
                        extractor="regex:ram_upgradeable",
                        url=doc.url,
                        fetched_at=fetched_at_str,
                    )
                )

        # 6. CPU
        if citable_for("cpu", s_class, policy):
            cpu_m = _CPU_RE.search(fact_line)
            if cpu_m:
                val = normalize_whitespace(cpu_m.group(0))
                facts.append(
                    Fact(
                        candidate=candidate_name,
                        aspect="cpu",
                        value=val,
                        quote=norm_line[:200],
                        source_id=doc.source_id,
                        source_class=s_class,
                        extractor="regex:cpu",
                        url=doc.url,
                        fetched_at=fetched_at_str,
                    )
                )

        # 7. Release Version & Date
        if citable_for("version", s_class, policy):
            ver_m = _VERSION_RE.search(fact_line)
            if ver_m:
                val = ver_m.group(1) or ver_m.group(2)
                facts.append(
                    Fact(
                        candidate=candidate_name,
                        aspect="version",
                        value=val,
                        quote=norm_line[:200],
                        source_id=doc.source_id,
                        source_class=s_class,
                        extractor="regex:version",
                        url=doc.url,
                        fetched_at=fetched_at_str,
                    )
                )
        if citable_for("release_date", s_class, policy):
            dt_m = _DATE_RE.search(fact_line)
            if dt_m:
                val = normalize_whitespace(dt_m.group(1))
                facts.append(
                    Fact(
                        candidate=candidate_name,
                        aspect="release_date",
                        value=val,
                        quote=norm_line[:200],
                        source_id=doc.source_id,
                        source_class=s_class,
                        extractor="regex:release_date",
                        url=doc.url,
                        fetched_at=fetched_at_str,
                    )
                )

    # Deduplicate facts by (candidate, aspect, value)
    unique_facts: List[Fact] = []
    seen = set()
    for f in facts:
        key = (f.candidate, f.aspect, f.value.lower())
        if key not in seen:
            seen.add(key)
            unique_facts.append(f)

    return unique_facts


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

        if not aspect or not value or not quote:
            continue

        doc = doc_map.get(source_id) or url_map.get(url)
        if not doc:
            continue

        # Decision 6 gate: verbatim quote check
        if not verify_quote(quote, doc):
            continue

        # Check value appears in quote or is direct numeric equivalent
        norm_val = normalize_whitespace(str(value)).lower()
        norm_quote = normalize_whitespace(quote).lower()
        if norm_val not in norm_quote:
            # Check digits match
            val_digits = re.sub(r"\D", "", norm_val)
            quote_digits = re.sub(r"\D", "", norm_quote)
            if not val_digits or val_digits not in quote_digits:
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
    general_facts: List[Fact] = []

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
                if f.source_class == SourceClass.RETAILER.value and aspect == "price":
                    class_desc = "Retailer Price"
                lines.append(
                    f"| {aspect} | {f.value} | {s_label} | {class_desc} | \"{f.quote[:80]}\" |"
                )
        lines.append("")

    return "\n".join(lines)
