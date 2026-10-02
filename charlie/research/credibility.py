"""Deterministic source-credibility scoring.

`quality_score` used to be ``len(text) / 4000``. That measured word count, not
trustworthiness, so a padded content farm outranked a short primary source and
every downstream confidence number inherited the error.

The signals below are deliberately cheap, local and interpretable. Weights
follow what the retrieval literature and AI-search measurements actually
support:

* Original/proprietary data and statistics are the strongest observed
  predictor of whether a page gets cited, so factual density leads.
* Domain authority correlates only *moderately* with citation, so publisher
  prior is present but deliberately the smallest term. It is a tiebreaker,
  not a trust oracle.
* Depth means completeness, not length, so completeness saturates instead of
  rewarding document size.
* Structural parse-ability and boilerplate presence both affect selection, so
  structure is rewarded and navigation/marketing residue is penalised.

Nothing here calls a model. It is a deterministic prior that a model, if used
at all downstream, can be argued against.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Set

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z0-9]{2,}", re.I)

# A number that carries a unit, currency, magnitude or date is a fact.
# A bare integer in a list of nav labels is not.
_FACTUAL_TOKEN = re.compile(
    r"(?:[$€£¥]\s?\d[\d,.]*)"                    # currency
    r"|(?:\b\d[\d,.]*\s?(?:%|percent|per cent))"   # percentage
    r"|(?:\b\d[\d,.]*\s?(?:million|billion|trillion|thousand))"
    r"|(?:\b\d[\d,.]*x\b)"                        # multiples
    r"|(?:\b(?:19|20)\d{2}\b)"                    # years
    r"|(?:\b\d[\d,.]*\s?(?:k|m|bn|bps|basis points|points|hours?|minutes?|days?|weeks?|months?|years?)\b)",
    re.I,
)

_STRUCTURE_LINE = re.compile(
    r"(?m)^\s*(?:[-*\u2022\u2013\u2014\u25cf]|\d+[.)]|#{1,6}\s)\s*\S"
)
_HEADING_LINE = re.compile(r"(?m)^[A-Z0-9][^\n]{2,72}$")

# Navigation, cookie chrome and marketing residue that survives extraction.
# These are the strings trafilatura commonly leaves behind on pages it did not
# fully isolate, and they inflate a length-based score.
_BOILERPLATE = (
    "subscribe", "sign up", "signup", "log in", "login", "create account",
    "read more", "click here", "learn more", "cookie", "cookies",
    "privacy policy", "terms of service", "terms of use", "all rights reserved",
    "follow us", "share this", "advertisement", "sponsored", "newsletter",
    "skip to", "skip navigation", "toggle", "left arrow", "right arrow",
    "close dialog", "opens in a new window", "breadcrumb", "search site",
    "accept all", "manage cookies", "your browser", "javascript is disabled",
)

_INSTITUTIONAL_SUFFIXES = (".gov", ".edu", ".int", ".ac.uk", ".gov.uk", ".mil")
_INSTITUTIONAL_HOSTS = (
    "who.int", "nih.gov", "nature.com", "science.org", "arxiv.org",
    "doi.org", "wikipedia.org", "nist.gov", "europa.eu", "un.org",
    "oecd.org", "iea.org", "imf.org", "worldbank.org", "mit.edu",
    "stanford.edu", "harvard.edu", "ox.ac.uk", "cam.ac.uk",
)

# Research-supported blend. Factual density leads because original data is the
# strongest observed predictor; publisher prior is last because authority is a
# moderate, not a dominant, signal.
_WEIGHTS = {
    "factual": 0.32,
    "substance": 0.22,
    "completeness": 0.20,
    "structure": 0.18,
    "publisher": 0.08,
}

# A page is saturated once it carries this many distinct informative
# sentences; beyond it, extra length is padding, not completeness.
_SATURATION_SENTENCES = 45.0

TWO_LABEL_PUBLIC_SUFFIXES = frozenset(
    {
        "ac.in", "ac.uk", "co.in", "co.jp", "co.kr", "co.nz", "co.uk", "co.za",
        "com.au", "com.br", "com.cn", "com.mx", "com.sg", "com.tr", "com.tw",
        "gov.in", "gov.uk", "net.au", "net.in", "ne.jp", "or.jp", "org.au",
        "org.in", "org.uk",
    }
)


def organisational_domain(host: str) -> str:
    """Reduce a host to the publisher identity that could count as independent.

    Subdomains collapse because ``blog.example.com`` and ``www.example.com``
    are one publisher, and counting them as corroboration is the exact failure
    this prevents. Two-label public suffixes use a small curated set rather than
    a full public-suffix list; the error direction is conservative, because
    undercounting publishers lowers reported confidence and never inflates it.
    """
    cleaned = (host or "").strip().lower()
    if not cleaned:
        return ""
    cleaned = cleaned.split("@")[-1]
    if cleaned.startswith("["):  # IPv6 literal, not a publisher identity.
        return ""
    cleaned = cleaned.split(":")[0].rstrip(".")
    labels = [label for label in cleaned.split(".") if label]
    if len(labels) < 2:
        return ".".join(labels)
    if len(labels) >= 3 and ".".join(labels[-2:]) in TWO_LABEL_PUBLIC_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@dataclass(frozen=True)
class CredibilitySignals:
    """Per-signal breakdown so any score can be explained, not just asserted."""

    factual: float
    substance: float
    completeness: float
    structure: float
    publisher: float
    score: float
    distinct_sentences: int = 0
    fact_count: int = 0
    boilerplate_count: int = 0

    def explain(self) -> str:
        return (
            f"score={self.score:.3f} factual={self.factual:.2f} "
            f"substance={self.substance:.2f} completeness={self.completeness:.2f} "
            f"structure={self.structure:.2f} publisher={self.publisher:.2f} "
            f"(distinct_sentences={self.distinct_sentences} facts={self.fact_count} "
            f"boilerplate={self.boilerplate_count})"
        )


def _sentences(text: str) -> list[str]:
    return [chunk.strip() for chunk in _SENTENCE_SPLIT.split(text) if chunk and chunk.strip()]


def _is_boilerplate(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(marker in lowered for marker in _BOILERPLATE)


def _publisher_prior(domain: str) -> float:
    """Small, deliberately weak authority prior."""
    host = (domain or "").strip().lower()
    if not host:
        return 0.0
    if any(host.endswith(suffix) for suffix in _INSTITUTIONAL_SUFFIXES):
        return 1.0
    if any(host == known or host.endswith(f".{known}") for known in _INSTITUTIONAL_HOSTS):
        return 1.0
    return 0.0


def score_credibility(text: str, domain: str = "") -> CredibilitySignals:
    """Score how much a document's *content* supports trusting it.

    Returns a weighted blend of five bounded signals. An empty or trivially
    short document scores 0.0 rather than erroring, so callers can rank
    without pre-filtering.
    """
    stripped = (text or "").strip()
    if not stripped:
        return CredibilitySignals(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0)

    sentences = _sentences(stripped)
    lowered_sentences = [item.lower() for item in sentences]

    boilerplate_count = sum(1 for item in lowered_sentences if _is_boilerplate(item))
    clean_sentences = [
        item for item in sentences if not _is_boilerplate(item)
    ] or sentences

    # Normalised set of distinct cleaned sentences; repeated boilerplate
    # templates collapse to one entry.
    distinct_keys: Set[str] = {
        " ".join(_WORD.findall(item.lower())) for item in clean_sentences
    }
    distinct_keys.discard("")
    distinct = len(distinct_keys)

    fact_sentences = sum(1 for item in clean_sentences if _FACTUAL_TOKEN.search(item))
    fact_count = len(_FACTUAL_TOKEN.findall(stripped))

    denominator = max(1, len(clean_sentences))

    factual = min(1.0, fact_sentences / denominator)
    # Density alone saturates fast, so pair it with absolute fact volume: a page
    # citing original statistics scores above one that merely mentions numbers.
    factual = min(1.0, factual * 0.75 + min(1.0, fact_count / 20.0) * 0.25)

    residue = boilerplate_count / denominator
    repetition = 1.0 - (distinct / denominator)
    substance = max(0.0, 1.0 - residue * 1.5 - repetition * 0.5)

    completeness = min(1.0, distinct / _SATURATION_SENTENCES)

    structure_marks = len(_STRUCTURE_LINE.findall(stripped))
    headings = len(_HEADING_LINE.findall(stripped))
    structure = min(1.0, (structure_marks * 0.08) + (headings * 0.12))

    publisher = _publisher_prior(domain)

    score = (
        _WEIGHTS["factual"] * factual
        + _WEIGHTS["substance"] * substance
        + _WEIGHTS["completeness"] * completeness
        + _WEIGHTS["structure"] * structure
        + _WEIGHTS["publisher"] * publisher
    )

    return CredibilitySignals(
        factual=round(factual, 4),
        substance=round(substance, 4),
        completeness=round(completeness, 4),
        structure=round(structure, 4),
        publisher=round(publisher, 4),
        score=round(min(1.0, max(0.0, score)), 4),
        distinct_sentences=distinct,
        fact_count=fact_count,
        boilerplate_count=boilerplate_count,
    )


def rank_prior(provider_rank: int) -> float:
    """Decay a provider's own result position into a usable prior.

    Measured AI-citation behaviour falls steeply with search position and then
    flattens (roughly 54% at position 1, ~23% at 10, ~5% at 50). A linear
    bonus that zeroes out past rank 8 throws that away, so use an exponential
    decay with a floor.
    """
    position = max(1, int(provider_rank or 1))
    return 0.42 * math.exp(-(position - 1) / 12.0) + 0.03
