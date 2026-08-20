"""Deterministic product constraint extraction and filtering."""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

from charlie.research.models import ProductResult, ResearchPlan, SourceDocument

logger = logging.getLogger("charlie.research.shopping")

_COMMERCIAL_KEYWORDS = re.compile(
    r"\b(buy|purchase|shopping|shop|order|price|prices|cost|costs|deal|deals|discount|discounts|cheap|affordable)\b",
    re.IGNORECASE,
)

_PRODUCT_CATEGORIES = re.compile(
    r"\b(iem|iems|earphone|earphones|headphone|headphones|earbuds|phone|phones|smartphone|smartphones|"
    r"laptop|laptops|gpu|gpus|keyboard|keyboards|mouse|mice|monitor|monitors|tv|tvs|tablet|tablets|"
    r"watch|smartwatch|camera|cameras|shoes|gadget|gadgets|device|devices|hardware)\b",
    re.IGNORECASE,
)

_SHOPPING_RECOMMENDATION = re.compile(
    r"\b(recommend|best|top|compare)\b",
    re.IGNORECASE,
)

_PRICE_PATTERN = re.compile(
    r"(?:₹|INR|Rs\.?|\$|USD|EUR|GBP)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)

_BUDGET_PATTERN = re.compile(
    r"(?:under|below|less than|within)\s*[₹$€£]?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)


def parse_budget(query: str) -> Optional[float]:
    if not query or not isinstance(query, str):
        return None
    match = _BUDGET_PATTERN.search(query)
    if not match:
        return None
    cleaned = match.group(1).replace(",", "").strip()
    if not cleaned:
        return None
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def is_shopping_query(query: str, plan: Optional[ResearchPlan] = None) -> bool:
    """Determine if query exhibits genuine product or shopping intent."""
    if not query or not isinstance(query, str):
        return False
    text = query.strip()
    if not text:
        return False

    # Budget in query indicates shopping intent
    if parse_budget(text) is not None:
        return True

    # Plan constraints explicitly targeting price / market
    if plan and getattr(plan, "constraints", None):
        if any("price" in str(c).lower() or "market=" in str(c).lower() for c in plan.constraints):
            return True

    # Direct shopping/commercial keywords
    if _COMMERCIAL_KEYWORDS.search(text):
        return True

    # Product category combined with recommendation/comparison
    if _SHOPPING_RECOMMENDATION.search(text) and _PRODUCT_CATEGORIES.search(text):
        return True

    return False


def extract_products(
    documents: Iterable[SourceDocument],
    query: str,
    currency: str = "INR",
) -> List[ProductResult]:
    try:
        budget = parse_budget(query)
        products: List[ProductResult] = []
        for document in documents:
            if document is None or not getattr(document, "content", None):
                continue

            parsed_prices: List[float] = []
            for item in _PRICE_PATTERN.findall(document.content):
                cleaned = str(item).replace(",", "").strip()
                if not cleaned:
                    continue
                try:
                    val = float(cleaned)
                    if val > 0:
                        parsed_prices.append(val)
                except (ValueError, TypeError):
                    continue

            if not parsed_prices:
                continue

            price = min(parsed_prices)
            if budget is not None and price > budget:
                continue

            products.append(
                ProductResult(
                    name=getattr(document, "title", "Product") or "Product",
                    price=price,
                    currency=currency,
                    store=getattr(document, "domain", "") or "",
                    url=getattr(document, "url", "") or "",
                    reason="price verified from extracted source text",
                )
            )
        return products
    except Exception as exc:
        logger.warning("Product extraction encountered error: %s", exc, exc_info=True)
        return []
