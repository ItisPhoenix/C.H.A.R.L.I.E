"""Deterministic product constraint extraction and filtering."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

from charlie.research.models import ProductResult, SourceDocument


def parse_budget(query: str) -> Optional[float]:
    match = re.search(
        r"(?:under|below|less than|within)\s*[₹$€£]?\s*([\d,]+)",
        query,
        re.I,
    )
    return float(match.group(1).replace(",", "")) if match else None


def extract_products(documents: Iterable[SourceDocument], query: str, currency: str = "INR") -> List[ProductResult]:
    budget = parse_budget(query)
    products: List[ProductResult] = []
    for document in documents:
        prices = [
            float(item.replace(",", ""))
            for item in re.findall(
                r"(?:₹|INR|Rs\.?|\$)\s*([\d,]+(?:\.\d{1,2})?)",
                document.content,
                re.I,
            )
        ]
        if not prices:
            continue
        price = min(prices)
        if budget is not None and price > budget:
            continue
        products.append(
            ProductResult(
                name=document.title,
                price=price,
                currency=currency,
                store=document.domain,
                url=document.url,
                reason="price verified from extracted source text",
            )
        )
    return products
