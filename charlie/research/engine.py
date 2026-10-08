"""Bounded research orchestration: search, fetch, extract, rank, cite, iterate."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Set
from urllib.parse import urlparse

from charlie.research.cache import TTLCache
from charlie.research.citations import assign_citations, assign_search_citations
from charlie.research.crawler import crawl_document
from charlie.research.credibility import (
    organisational_domain as _organisational_domain,
)
from charlie.research.evidence import build_evidence, normalize_research_term
from charlie.research.facts import (
    extract_facts_from_document,
    fact_table,
    model_facts,
    parse_inr_price,
)
from charlie.research.fetch import fetch_document
from charlie.research.media import media_results
from charlie.research.models import (
    Candidate,
    EvidenceItem,
    Fact,
    ResearchBrief,
    ResearchMode,
    ResearchPlan,
    ResearchProgress,
    ResearchQuery,
    ResearchReport,
    SearchResult,
    SourceClass,
    SourceDocument,
    Subquestion,
)
from charlie.research.ranking import rank_documents, rank_search_results
from charlie.research.releases import (
    canonical_release_urls,
    is_stable_version,
    parse_version_tuple,
    pick_stable,
    stable_release_span,
)
from charlie.research.router import ResearchDecision, is_sustained_research_query, route
from charlie.research.candidates import extract_candidates
from charlie.research.search import (
    clean_query,
    build_plan,
    discovery_queries,
    is_llm_release_brief,
    parse_brief,
    search_plan,
    verification_queries,
)
from charlie.research.semantics import gather_semantic_scores
from charlie.research.shopping import extract_products, is_shopping_query
from charlie.research.sources import (
    OFFICIAL_REGISTRY,
    classify,
    citable_for,
    get_official_document_urls,
    get_official_domains,
    resolve_brand,
)

logger = logging.getLogger("charlie.research.engine")


def _release_date_key(value: str) -> Optional[datetime]:
    normalized = re.sub(r"\bSept\.?", "Sep", str(value or "").strip(), flags=re.I)
    normalized = re.sub(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.",
        r"\1",
        normalized,
        flags=re.I,
    )
    for pattern in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    return None


def _candidate_name_key(value: str) -> str:
    return re.sub(r"[^\w\s]", "", str(value or "")).casefold().strip()


def _latest_llm_release_candidates(
    candidates: List[Candidate], access: str
) -> tuple[Optional[datetime], set[str]]:
    dated = [
        (candidate, _release_date_key(candidate.release_date or ""))
        for candidate in candidates
        if candidate.access == access
    ]
    dated = [(candidate, date) for candidate, date in dated if date is not None]
    latest = max((date for _candidate, date in dated), default=None)
    return latest, {
        _candidate_name_key(candidate.name)
        for candidate, date in dated
        if date == latest
    }


def _llm_release_category_verified(
    candidates: List[Candidate], facts: List[Fact], access: str, policy: str
) -> bool:
    latest_date, latest_names = _latest_llm_release_candidates(candidates, access)
    return bool(
        latest_date is not None
        and any(
            _candidate_name_key(fact.candidate or "") in latest_names
            and fact.aspect == "release_date"
            and _release_date_key(fact.value) == latest_date
            and citable_for(fact.aspect, fact.source_class, policy)
            for fact in facts
        )
    )

ProgressCallback = Callable[[ResearchProgress], Any]
BrowserFetchCallback = Callable[[SearchResult], Awaitable[Optional[SourceDocument]]]
BriefPlannerCallback = Callable[[str, ResearchBrief], Awaitable[Dict[str, Any]]]
CandidateExtractorCallback = Callable[[ResearchBrief, List[SourceDocument]], Awaitable[List[Dict[str, Any]]]]


_CONFIDENCE_HALF_WEIGHT = 2.0
CREDIBILITY_FLOOR = 0.4


def organisational_domain(host: str) -> str:
    return _organisational_domain(host)


def compute_confidence(
    evidence: Sequence[EvidenceItem],
    sources: Iterable[SourceDocument],
) -> Optional[float]:
    documents = list(sources)
    domain_by_source: dict[str, str] = {}
    quality_by_source: dict[str, float] = {}
    for document in documents:
        host = organisational_domain(document.domain) or organisational_domain(
            urlparse(document.url).netloc
        )
        if host and document.source_id:
            domain_by_source[document.source_id] = host
            quality_by_source[document.source_id] = max(
                0.0, min(1.0, document.quality_score)
            )

    grounded = {
        domain_by_source[item.source_id]
        for item in evidence
        if domain_by_source.get(item.source_id)
    }
    if not grounded:
        return None

    count = float(len(grounded))
    diversity = count / (count + _CONFIDENCE_HALF_WEIGHT)

    contributing = [
        quality_by_source[item.source_id]
        for item in evidence
        if item.source_id in quality_by_source
    ]
    if not contributing:
        return None
    credibility = sum(contributing) / len(contributing)

    combined = diversity * (
        CREDIBILITY_FLOOR + (1.0 - CREDIBILITY_FLOOR) * credibility
    )
    return min(1.0, max(0.0, combined))


def grounded_domain_count(
    evidence: Sequence[EvidenceItem],
    sources: Iterable[SourceDocument],
) -> int:
    domain_by_source: dict[str, str] = {}
    for document in sources:
        host = organisational_domain(document.domain) or organisational_domain(
            urlparse(document.url).netloc
        )
        if host and document.source_id:
            domain_by_source[document.source_id] = host
    return len(
        {
            domain_by_source[item.source_id]
            for item in evidence
            if domain_by_source.get(item.source_id)
        }
    )


def _source_allowed(
    source: Optional[SourceDocument], policy: str, domain_filters: Optional[Sequence[str]] = None
) -> bool:
    if source is None:
        return False
    if policy == "official_required" and source.source_class not in {
        SourceClass.OFFICIAL.value,
        SourceClass.OFFICIAL_UNVERIFIED.value,
    }:
        return False
    if domain_filters:
        hosts = [
            (urlparse(url).hostname or "").casefold().rstrip(".")
            for url in (source.url, source.canonical_url)
            if url
        ]
        return bool(hosts) and all(
            any(
                host == domain.casefold().rstrip(".")
                or host.endswith("." + domain.casefold().rstrip("."))
                for domain in domain_filters
            )
            for host in hosts
        )
    return True


def _research_brief(query: str, mode: ResearchMode) -> ResearchBrief:
    brief = parse_brief(query)
    brief.original_request = query
    brief.objective = brief.topic
    brief.strategy = brief.entity_kind
    brief.depth = mode.value
    if is_llm_release_brief(brief) and brief.source_policy == "any_reputable":
        brief.source_policy = "official_preferred"
    license_question = re.search(
        r"\b(?:is|does)\s+(.+?)\s+licensed\s+under\s+([A-Za-z0-9][A-Za-z0-9.-]*)\b",
        query,
        re.I,
    )
    if license_question and brief.entity_kind == "general":
        subject = re.sub(r"\s+(?:project|software)$", "", license_question.group(1).strip(), flags=re.I)
        license_id = license_question.group(2).upper()
        brief.topic = subject
        brief.objective = subject
        brief.aspects = ["license"]
        brief.required_subquestions = [
            Subquestion(
                id="q1",
                question=f"Is {subject} licensed under {license_id}?",
                required_fields=[f"license_proposition:{license_id}", f"subject:{subject}"],
                evidence_requirements=["fetched license statement attributable to this project"],
            )
        ]
        brief.required_subquestions.extend(
            Subquestion(
                id=f"source-{index}",
                question=f"Fetch evidence from {domain}",
                required_fields=[f"source:{domain}"],
                evidence_requirements=["accepted fetched source from this domain"],
            )
            for index, domain in enumerate(brief.explicit_domains, start=2)
        )
        return brief

    def split_list(value: str) -> list[str]:
        value = re.sub(r"\b(?:projects?|libraries|tools|systems|publishers|options?)\b", "", value, flags=re.I)
        return [
            part.strip(" ,.;:")
            for part in re.split(r"\s*,\s*|\s+and\s+|\s+or\s+", value, flags=re.I)
            if part.strip(" ,.;:")
        ]

    subjects: list[str] = []
    dimensions: list[str] = []
    if brief.entity_kind == "general":
        comparison = re.search(
            r"\bcompare\s+(.+?)(?=\s+\b(?:using|by|on|across|against)\b|[?.;]|$)",
            query,
            re.I,
        )
        if comparison:
            subjects = split_list(comparison.group(1))
            subjects = [
                subject
                for subject in subjects
                if not re.match(r"^(?:their|these|those|cite|both)\b", subject, re.I)
            ]
        dimension_match = re.search(r"\bcover\s+(.+?)(?:[?.;]|$)", query, re.I)
        if not dimension_match:
            dimension_match = re.search(r"\b(?:by|on)\s+(.+?)(?:[?.;]|$)", query, re.I)
        if dimension_match:
            dimensions = split_list(dimension_match.group(1))
        if not subjects and re.search(r"\b(?:research|investigate|compare)\b", query, re.I):
            source_match = re.search(r"\b(?:from|of)\s+(.+?)(?:[?.;]|$)", query, re.I)
            if source_match:
                subjects = split_list(source_match.group(1))
            before_of = re.search(r"\b(?:research|investigate)\s+(.+?)\s+of\b", query, re.I)
            from_topic = re.search(r"\b(?:research|investigate)\s+(.+?)\s+from\b", query, re.I)
            topic_match = before_of or from_topic
            if topic_match:
                dimensions = split_list(topic_match.group(1))
        comparative_dimensions = re.search(
            r"\bcompare\s+(?:their|both|these|those|the)\s+(.+?)(?:[?.;]|$)",
            query,
            re.I,
        )
        if comparative_dimensions:
            dimension_text = re.split(
                r"\b(?:cite|sources?|publishers?|documentation|docs)\b",
                comparative_dimensions.group(1),
                maxsplit=1,
                flags=re.I,
            )[0]
            dimension_text = re.sub(r"^(?:their|both|these|those|the)\s+", "", dimension_text, flags=re.I)
            requested_dimensions = split_list(dimension_text)
            if requested_dimensions:
                dimensions = requested_dimensions
        if not dimensions and subjects and re.search(r"\bcompare\b", query, re.I):
            dimensions = ["recommendations"]

    if subjects:
        mapped_domains = [
            domain
            for subject in subjects
            if (brand := resolve_brand(subject))
            for domain in get_official_domains(brand)
        ]
        if mapped_domains:
            brief.explicit_domains = list(dict.fromkeys(brief.explicit_domains + mapped_domains))
            brief.source_policy = "official_required"

    questions: list[Subquestion] = []
    topic_lower = brief.topic.casefold()
    asks_open_llm = bool(re.search(r"\bopen[- ](?:source|weight)\b", topic_lower))
    asks_closed_llm = bool(re.search(r"\b(?:close|closed)[- ]source\b|\bproprietary\b", topic_lower))
    if is_llm_release_brief(brief) and asks_open_llm and asks_closed_llm:
        questions = [
            Subquestion(
                id="llm-closed-release",
                question=f"Latest closed-source LLM release for {brief.topic}",
                required_fields=["llm_release:closed"],
                evidence_requirements=["developer source ties a model release date to this model"],
            ),
            Subquestion(
                id="llm-open-release",
                question=f"Latest open-weight LLM release for {brief.topic}",
                required_fields=["llm_release:open"],
                evidence_requirements=["developer source ties a model release date to this model"],
            ),
        ]
    elif brief.entity_kind == "release" and brief.requested_version:
        version = brief.requested_version
        questions = [
            Subquestion(
                id="q1",
                question=f"{brief.topic}: version {version}",
                required_fields=[f"version:{version}"],
                evidence_requirements=["fetched passage tying this exact version to the release"],
            ),
            Subquestion(
                id="q2",
                question=f"{brief.topic}: release date for version {version}",
                required_fields=[f"release_date:{version}"],
                evidence_requirements=["fetched passage tying the date to this exact version"],
            ),
        ]
    elif subjects and dimensions:
        for subject in subjects:
            for dimension in dimensions:
                questions.append(
                    Subquestion(
                        id=f"q{len(questions) + 1}",
                        question=f"{subject}: {dimension}",
                        required_fields=[subject, dimension],
                        evidence_requirements=["fetched source passage from the requested subject"],
                    )
                )
    else:
        fields = brief.aspects or ["answer"]
        questions = [
            Subquestion(
                id=f"q{index}",
                question=query if aspect == "answer" else f"{brief.topic}: {aspect}",
                required_fields=[aspect],
                evidence_requirements=["fetched source passage"],
            )
            for index, aspect in enumerate(fields, start=1)
        ]

    for domain in brief.explicit_domains:
        questions.append(
            Subquestion(
                id=f"source-{len(questions) + 1}",
                question=f"Fetch evidence from {domain}",
                required_fields=[f"source:{domain}"],
                evidence_requirements=["accepted fetched source from this domain"],
            )
        )

    source_count = re.search(
        r"\b(one|two|three|four|five|\d+)\s+(?:independent\s+)?(?:sources?|publishers?)\b",
        query,
        re.I,
    )
    if source_count:
        number = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.get(
            source_count.group(1).casefold(), int(source_count.group(1)) if source_count.group(1).isdigit() else 1
        )
        questions.append(
            Subquestion(
                id=f"source-count-{len(questions) + 1}",
                question=f"Use {number} independent publishers",
                required_fields=[f"independent_sources:{number}"],
                evidence_requirements=["fetched sources from distinct publishers"],
            )
        )
    brief.required_subquestions = questions
    return brief


def _official_document_search_results(brief: ResearchBrief) -> List[SearchResult]:
    if brief.source_policy != "official_required":
        return []
    subjects = {
        question.required_fields[0]
        for question in brief.required_subquestions
        if len(question.required_fields) >= 2
    }
    results: List[SearchResult] = []
    seen: set[str] = set()
    for subject in subjects:
        brand = resolve_brand(subject)
        for url in get_official_document_urls(brand or ""):
            if url not in seen:
                results.append(
                    SearchResult(
                        title=f"{subject} official documentation",
                        url=url,
                        provider="canonical",
                        rank=0,
                    )
                )
                seen.add(url)
    return results


def _official_coverage_queries(brief: ResearchBrief) -> List[ResearchQuery]:
    if brief.entity_kind != "general" or brief.source_policy != "official_required":
        return []
    questions = [
        item
        for item in brief.required_subquestions
        if len(item.required_fields) >= 2 and not item.required_fields[0].startswith("source:")
    ]
    questions.sort(
        key=lambda item: 0 if re.search(r"\blimit|constraint|trade.?off|drawback", item.required_fields[1], re.I) else 1
    )
    queries = []
    for item in questions:
        subject, dimension = item.required_fields[:2]
        brand = resolve_brand(subject)
        domains = get_official_domains(brand) if brand else []
        if not domains:
            continue
        dimension = re.sub(r"^the\s+", "", dimension, flags=re.I)
        query = f"{subject} {dimension}"
        if re.search(r"\blimit|constraint|trade.?off|drawback", dimension, re.I):
            query += " constraints tradeoffs drawbacks requirements prerequisites dependencies unsupported restrictions installation"
        queries.append(ResearchQuery(f"{query} official documentation", "evidence coverage", domains))
    return queries


def _bind_stable_release_passages(report: ResearchReport) -> bool:
    if report.brief is None or report.brief.entity_kind != "release":
        return False
    sources_by_id = {source.source_id: source for source in report.sources}
    facts_by_release = {}
    for fact in report.facts:
        if fact.extractor == "releases:pick_stable":
            facts_by_release.setdefault((fact.source_id, fact.candidate), {})[fact.aspect] = fact

    added = False
    for (source_id, _candidate), release_facts in facts_by_release.items():
        version_fact = release_facts.get("version")
        if version_fact is None or not is_stable_version(version_fact.value):
            continue
        source = sources_by_id.get(source_id)
        if source is None or not citable_for("version", source.source_class, report.brief.source_policy):
            continue
        date_fact = release_facts.get("release_date")
        released = date_fact.value if date_fact is not None else ""
        span = stable_release_span(source, version_fact.value, released)
        if span is None:
            continue
        start, end = span
        if any(
            item.source_id == source_id and item.start_offset == start and item.end_offset == end
            for item in report.evidence
        ):
            continue
        statement = source.content[start:end]
        passage_id = hashlib.sha256(
            f"{source.document_id}\0{source.content_hash}\0{start}\0{end}".encode("utf-8")
        ).hexdigest()[:24]
        report.evidence.append(
            EvidenceItem(
                source_id,
                statement,
                relevance=1.0,
                confidence=source.quality_score,
                passage_id=passage_id,
                start_offset=start,
                end_offset=end,
                document_hash=source.content_hash,
            )
        )
        added = True
    if added:
        report.bind_passages()
    return added


def _bind_llm_release_fact_passages(report: ResearchReport) -> bool:
    if report.brief is None or not is_llm_release_brief(report.brief):
        return False
    sources_by_id = {source.source_id: source for source in report.sources}
    added = False
    for fact in report.facts:
        if fact.aspect != "release_date" or not fact.source_id:
            continue
        source = sources_by_id.get(fact.source_id)
        if source is None or not citable_for(fact.aspect, source.source_class, report.brief.source_policy):
            continue
        start = source.content.find(fact.quote) if fact.quote else -1
        span = fact.quote
        if start < 0 and fact.value:
            start = source.content.find(fact.value)
            span = fact.value
        if start < 0 or not span:
            continue
        end = start + len(span)
        if any(
            item.source_id == fact.source_id and item.start_offset == start and item.end_offset == end
            for item in report.evidence
        ):
            continue
        statement = source.content[start:end]
        passage_id = hashlib.sha256(
            f"{source.document_id}\0{source.content_hash}\0{start}\0{end}".encode("utf-8")
        ).hexdigest()[:24]
        report.evidence.append(
            EvidenceItem(
                fact.source_id,
                statement,
                relevance=1.0,
                confidence=source.quality_score,
                passage_id=passage_id,
                start_offset=start,
                end_offset=end,
                document_hash=source.content_hash,
            )
        )
        added = True
    if added:
        report.bind_passages()
    return added


def _update_report_coverage(report: ResearchReport) -> bool:
    brief = report.brief
    if brief is None or not report.coverage:
        return False
    _bind_stable_release_passages(report)
    _bind_llm_release_fact_passages(report)
    stopwords = {
        "about", "across", "and", "are", "by", "compare", "cover", "cite", "documentation",
        "for", "from", "how", "is", "it", "of", "official", "project", "research", "source",
        "sources", "the", "their", "this", "to", "what", "with", "use", "using",
    }

    def terms(value: str) -> set[str]:
        return {
            normalize_research_term(token)
            for token in re.findall(r"[A-Za-z0-9]+", value)
            if normalize_research_term(token) not in stopwords
        }

    def dimension_matches(dimension: str, passage_terms: set[str]) -> bool:
        expected = terms(dimension)
        if "recommendation" in expected:
            return bool(
                {
                    "recommendation", "recommend", "guidance", "prevent", "prevention",
                    "mitigation", "mitigate", "allow", "deny", "block", "restrict",
                    "validate", "validation",
                }
                & passage_terms
            )
        if "architecture" in expected:
            return bool(
                {
                    "architecture", "architectural", "structure", "component", "service",
                    "engine", "crawler", "scraper", "fetcher", "spider", "pipeline",
                    "browser", "api", "metasearch", "aggregate",
                }
                & passage_terms
            )
        if "advantage" in expected:
            return bool(
                {
                    "advantage", "benefit", "feature", "privacy", "private", "tracked",
                    "profiled", "free", "open", "performance", "fast", "blocking", "block",
                }
                & passage_terms
            )
        if "limitation" in expected:
            return bool(
                {
                    "limitation", "limit", "constraint", "tradeoff", "drawback", "caveat",
                    "unsupported", "cannot", "require", "requires", "need", "install",
                    "dependency", "prerequisite", "missing", "unavailable",
                }
                & passage_terms
            )
        if {"redirect", "handling"}.issubset(expected):
            return "redirect" in passage_terms and bool(
                {"follow", "followed", "allow", "default", "history"} & passage_terms
            )
        if {"client", "lifecycle"}.issubset(expected):
            return bool({"client", "session", "clientsession"} & passage_terms) and bool(
                {"context", "manager", "managed", "close", "cleanup", "persistent", "session"}
                & passage_terms
            )
        return bool(expected & passage_terms)

    sources_by_id = {source.source_id: source for source in report.sources}

    def source_eligible(source: Optional[SourceDocument]) -> bool:
        return _source_allowed(source, brief.source_policy, brief.explicit_domains)

    unique_publishers = {
        organisational_domain(source.domain or urlparse(source.url).netloc)
        for source in report.sources
        if source_eligible(source)
    } - {""}

    def passage_ids_for_facts(facts: list[Fact]) -> list[str]:
        passage_ids: list[str] = []
        for passage in report.passages:
            for fact in facts:
                if passage.source_id != fact.source_id:
                    continue
                values = [fact.value, fact.quote]
                if fact.aspect != "release_date":
                    values.append(fact.candidate)
                if any(
                    value and str(value).casefold() in passage.text.casefold()
                    for value in values
                ):
                    passage_ids.append(passage.passage_id)
                    break
        return list(dict.fromkeys(passage_ids))

    def facts_conflict(facts: list[Fact]) -> bool:
        values = {" ".join(str(fact.value).split()).casefold() for fact in facts if fact.value}
        return len(values) > 1 and len({fact.source_id for fact in facts if fact.source_id}) > 1

    def normalize_license(value: str) -> str:
        compact = re.sub(r"[^a-z0-9.]", "", value.casefold())
        if compact == "mit":
            return "MIT"
        match = re.match(r"(agpl|lgpl|gpl|apache|bsd|mpl|epl)(\d+(?:\.\d+)?)", compact)
        if match:
            family, version = match.groups()
            return f"{family.upper()}-{version}"
        return compact.upper()

    def licenses_in(text: str) -> set[str]:
        matches = re.findall(
            r"\b(?:MIT|(?:A|L)?GPL(?:[- ]?v?[- ]?\d+(?:\.\d+)?(?:[- ](?:only|or-later))?)?|"
            r"Apache[- ]?2(?:\.0)?|BSD[- ]?[23](?:-Clause)?|ISC|MPL[- ]?2(?:\.0)?|EPL[- ]?2(?:\.0)?)\b",
            text,
            re.I,
        )
        natural_gpl = re.findall(
            r"\bGNU\s+General\s+Public\s+License(?:\s+(?:version\s*)?)(\d+(?:\.\d+)?)\b",
            text,
            re.I,
        )
        return {normalize_license(value) for value in matches} | {
            f"GPL-{version}" for version in natural_gpl
        }

    gaps: list[str] = []
    for question in report.coverage:
        fields = question.required_fields
        supporting: list[str] = []
        refuting: list[str] = []
        conflict = False
        conflict_message = ""
        missing_message = ""
        if fields and fields[0].startswith("license_proposition:"):
            expected = normalize_license(fields[0].split(":", 1)[1])
            subject = fields[1].split(":", 1)[1] if len(fields) > 1 and fields[1].startswith("subject:") else ""
            subject_terms = terms(subject)
            observed: dict[str, list[str]] = {}
            for passage in report.passages:
                source = sources_by_id.get(passage.source_id)
                if not source_eligible(source):
                    continue
                source_terms = terms(f"{source.title} {source.url} {source.content[:5000]} {passage.text}")
                if not subject_terms or not subject_terms.intersection(source_terms):
                    continue
                if not re.search(r"\blicen[cs](?:e|ed|ing)\b", passage.text, re.I):
                    continue
                for license_id in licenses_in(passage.text):
                    observed.setdefault(license_id, []).append(passage.passage_id)
            supporting = list(dict.fromkeys(observed.get(expected, [])))
            refuting = list(dict.fromkeys(
                passage_id
                for license_id, passage_ids in observed.items()
                if license_id != expected
                for passage_id in passage_ids
            ))
            conflict = bool(supporting and refuting) or len(observed) > 1
            if conflict:
                conflict_message = "Fetched official sources disagree about the project's license"
        elif fields and fields[0].startswith("llm_release:"):
            access = fields[0].split(":", 1)[1]
            latest_candidate_date, latest_candidate_names = _latest_llm_release_candidates(
                report.candidates, access
            )
            matching_facts = [
                fact
                for fact in report.facts
                if _candidate_name_key(fact.candidate or "") in latest_candidate_names
                and fact.aspect == "release_date"
                and citable_for(fact.aspect, fact.source_class, brief.source_policy)
                and _release_date_key(fact.value) == latest_candidate_date
            ]
            supporting = passage_ids_for_facts(matching_facts)
            conflict = facts_conflict(matching_facts)
            if conflict:
                conflict_message = f"Developer sources disagree about the {access}-weight model release date"
            elif latest_candidate_date is None:
                missing_message = "No dated candidate was found for this access category"
            elif not matching_facts:
                missing_message = "The newest dated candidate lacks a matching developer-source release date"
            elif not supporting:
                missing_message = "The developer release date was not retained in a supporting evidence passage"
        elif fields and fields[0].startswith(("version:", "release_date:")):
            aspect, requested_version = fields[0].split(":", 1)
            requested = parse_version_tuple(requested_version)
            matching_facts = []
            for fact in report.facts:
                if fact.aspect != aspect:
                    continue
                if aspect == "version":
                    candidate_version = parse_version_tuple(fact.value)
                    if requested and candidate_version and candidate_version[:3] == requested[:3]:
                        matching_facts.append(fact)
                elif re.search(
                    rf"(?<![A-Za-z0-9.]){re.escape(requested_version)}(?![A-Za-z0-9.])",
                    f"{fact.candidate or ''} {fact.quote}",
                    re.I,
                ):
                    matching_facts.append(fact)
            supporting = passage_ids_for_facts(matching_facts)
            conflict = aspect == "release_date" and facts_conflict(matching_facts)
            if conflict:
                conflict_message = "Credible sources report different dates for this exact version"
        elif fields and fields[0].startswith("source:"):
            domain = fields[0].split(":", 1)[1].casefold()
            matching_ids = {
                source.source_id
                for source in report.sources
                if source_eligible(source) and (
                    (source.domain or urlparse(source.url).netloc).casefold().split(":")[0] == domain
                    or (source.domain or urlparse(source.url).netloc).casefold().split(":")[0].endswith(f".{domain}")
                )
            }
            supporting = [item.passage_id for item in report.passages if item.source_id in matching_ids]
        elif fields and fields[0].startswith("independent_sources:"):
            required = int(fields[0].split(":", 1)[1])
            if len(unique_publishers) >= required:
                supporting = [item.passage_id for item in report.passages]
        elif len(fields) >= 2:
            subject_terms = terms(fields[0])
            for passage in report.passages:
                source = sources_by_id.get(passage.source_id)
                if not source_eligible(source):
                    continue
                subject_found = subject_terms.intersection(
                    terms(f"{source.title} {source.url} {source.content[:5000]}")
                )
                if subject_found and dimension_matches(fields[1], terms(passage.text)):
                    supporting.append(passage.passage_id)
        elif fields and fields[0] not in {"answer", ""}:
            required = fields[0].casefold()
            matching_facts = [
                fact for fact in report.facts if fact.aspect.casefold() == required
            ]
            if brief.entity_kind == "release" and required in {"version", "release_date"}:
                if is_llm_release_brief(brief):
                    matching_facts = [
                        fact
                        for fact in matching_facts
                        if fact.candidate
                        and citable_for(fact.aspect, fact.source_class, brief.source_policy)
                    ]
                else:
                    matching_facts = [
                        fact
                        for fact in matching_facts
                        if fact.extractor == "releases:pick_stable"
                    ]
            supporting = passage_ids_for_facts(matching_facts)
            conflict = facts_conflict(matching_facts)
            if conflict:
                conflict_message = f"Fetched sources disagree about {required}"
            if not supporting:
                supporting = [
                    item.passage_id for item in report.passages if required in terms(item.text)
                ]
        else:
            required_terms = terms(question.question)
            for passage in report.passages:
                overlap = required_terms.intersection(terms(passage.text))
                if overlap and len(overlap) >= min(2, len(required_terms)):
                    supporting.append(passage.passage_id)

        question.supporting_claim_ids = list(dict.fromkeys(supporting))
        if len(fields) >= 2 and supporting:
            relevance_by_passage = {item.passage_id: item.relevance for item in report.evidence}
            question.supporting_claim_ids = sorted(
                question.supporting_claim_ids,
                key=lambda passage_id: relevance_by_passage.get(passage_id, 0.0),
                reverse=True,
            )[:2]
        question.refuting_claim_ids = list(dict.fromkeys(refuting))
        question.conflict = conflict
        if conflict:
            question.status = "unresolved"
            question.missing_evidence = [conflict_message or "Credible sources disagree on this requirement"]
            gaps.append(f"Unresolved: {question.question} (conflicting evidence)")
        elif supporting:
            question.status = "supported"
            question.missing_evidence = []
        elif refuting:
            question.status = "contradicted"
            question.missing_evidence = []
        else:
            question.status = "unresolved"
            question.missing_evidence = [missing_message or "No fetched passage resolved this requirement"]
            gaps.append(f"Unresolved: {question.question}")

    other_gaps = [
        gap for gap in report.gaps
        if not str(gap).startswith("Unresolved: ")
    ]
    report.gaps = list(dict.fromkeys(other_gaps + gaps))
    return all(question.status in {"supported", "contradicted"} for question in report.coverage)


_ENTITY_RE = re.compile(r"&(?:nbsp|ensp|emsp|thinsp|#160|#8194|#8195|#8201);", re.I)
_ZERO_WIDTH_RE = re.compile("[\u00ad\u200b-\u200f\u2060\ufeff]")
_TEMPLATE_RE = re.compile(r"\{[{%][^}%]{0,400}[}%]\\}")
_RAW_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,400}>")
_OBJECT_RE = re.compile(r"\[object\s+Object\]", re.I)
_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_ALNUM_RE = re.compile(r"[A-Za-z0-9\u00c0-\u024f]")

_NAN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:NaN|[+-]?Infinity)(?![A-Za-z0-9_])")
_UNDEFINED_RE = re.compile(r"(?<![A-Za-z0-9_])undefined(?![A-Za-z0-9_])", re.I)
_UNDEFINED_LEGIT_RE = re.compile(r"undefined\s+(?:behaviour|behaviors|behavior)s?", re.I)
_NULL_JUNK_RE = re.compile(r"(?:=|:|\(|\[)\s?null\b|\bnull\s?(?:\)|;|\])", re.I)
_LOADING_ONLY_RE = re.compile(
    r"^(?:please\s+)?(?:loading|please\s+wait|wait|working|fetching|one\s+moment)"
    r"(?:[\s.\u2026!]*|please\b.*)$",
    re.I,
)
_JS_DISABLED_RE = re.compile(
    r"javascript\s+(?:is|are)\s+(?:disabled|not\s+enabled|required)", re.I
)


def _is_junk_segment(segment: str) -> bool:
    text = segment.strip()
    if not text:
        return True
    if not _ALNUM_RE.search(text):
        return True
    if _NAN_RE.search(text):
        return True
    if _UNDEFINED_RE.search(_UNDEFINED_LEGIT_RE.sub(" ", text)):
        return True
    if _NULL_JUNK_RE.search(text):
        return True
    if _OBJECT_RE.search(text):
        return True
    if _JS_DISABLED_RE.search(text):
        return True
    if len(text) <= 80 and _LOADING_ONLY_RE.match(text):
        return True
    return False


def sanitize_source_text(content: str) -> str:
    if not content:
        return ""
    cleaned = _ENTITY_RE.sub(" ", content)
    cleaned = _ZERO_WIDTH_RE.sub(" ", cleaned)
    cleaned = _TEMPLATE_RE.sub(" ", cleaned)
    cleaned = _RAW_TAG_RE.sub(" ", cleaned)
    kept = [
        segment.strip()
        for segment in _SEGMENT_SPLIT_RE.split(cleaned)
        if not _is_junk_segment(segment)
    ]
    return "\n".join(kept)


def _matches_candidate_variant(candidate_name: str, doc_title: str, doc_url: str, text_span: str) -> bool:
    """Product-variant guard: facts are bound only to the exact model variant."""
    # Extract distinct model alphanumeric tokens like 15IAX9, FA507NV, G15-5530, etc.
    code_tokens = re.findall(
        r"\b\d{2}[A-Za-z]{3}\d\b|\b[A-Za-z]{2}\d{3}[A-Za-z]{2}\b|\b[A-Za-z0-9]+-[A-Za-z0-9]+\b",
        candidate_name,
    )
    if not code_tokens:
        # Check brand + main model token
        name_lower = candidate_name.lower()
        return (
            name_lower in doc_title.lower()
            or name_lower in doc_url.lower()
            or name_lower in text_span.lower()
        )

    for tok in code_tokens:
        tok_lower = tok.lower()
        # If candidate code appears in page or span, it matches
        span_codes = re.findall(
            r"\b\d{2}[A-Za-z]{3}\d\b|\b[A-Za-z]{2}\d{3,}[A-Za-z]{2}\b|\b[A-Za-z]\d{2}-\d{4}\b",
            text_span,
        )
        variant_parts = set(tok_lower.split("-")) | {tok_lower}
        if span_codes and any(oc.lower() not in variant_parts for oc in span_codes):
            return False
        if tok_lower in doc_title.lower() or tok_lower in doc_url.lower() or tok_lower in text_span.lower():
            return True
        # If a different competing code is present on the page, reject
        other_codes = re.findall(
            r"\b\d{2}[A-Za-z]{3}\d\b|\b[A-Za-z]{2}\d{3}[A-Za-z]{2}\b",
            doc_title + " " + doc_url,
        )
        if other_codes and any(oc.lower() != tok_lower for oc in other_codes):
            return False

    return False


def _is_candidate_complete(candidate: Candidate, facts: List[Fact], brief: ResearchBrief) -> bool:
    """Check if a candidate has citable official specs for required aspects and price <= budget."""
    cand_name_clean = re.sub(r"[^\w\s]", "", candidate.name).lower().strip()
    cand_facts = []
    for f in facts:
        if not f.candidate:
            continue
        fc_clean = re.sub(r"[^\w\s]", "", f.candidate).lower().strip()
        if fc_clean == cand_name_clean and citable_for(f.aspect, f.source_class, brief.source_policy):
            cand_facts.append(f)

    if not cand_facts:
        return False

    aspects_covered = {f.aspect for f in cand_facts}

    # Must have price
    price_facts = [f for f in cand_facts if f.aspect == "price"]
    if not price_facts and brief.budget:
        return False
    if brief.budget and price_facts:
        val = parse_inr_price(price_facts[0].value)
        if val is None or val > brief.budget:
            return False

    # Check key hardware aspects if required
    for req in ("gpu", "vram", "ram"):
        if req in brief.aspects and req not in aspects_covered:
            return False

    return True


class PhaseBudget:
    def __init__(
        self,
        plan_s: float = 15.0,
        discover_s: float = 40.0,
        verify_s: float = 70.0,
        synth_s: float = 45.0,
    ) -> None:
        self.plan_s = plan_s
        self.discover_s = discover_s
        self.verify_s = verify_s
        self.synth_s = synth_s
        self.start = time.perf_counter()
        self.leftover = 0.0

    def deadline_for(self, phase_budget: float) -> float:
        allowed = phase_budget + self.leftover
        self.leftover = 0.0
        return allowed

    def record_used(self, allowed: float, used: float) -> None:
        if used < allowed:
            self.leftover += (allowed - used)


class ResearchEngine:
    def __init__(
        self,
        config: Any,
        *,
        progress: Optional[ProgressCallback] = None,
        browser_fetch: Optional[BrowserFetchCallback] = None,
        query_planner: Optional[Callable[[str], Awaitable[List[str]]]] = None,
        brief_planner: Optional[BriefPlannerCallback] = None,
        candidate_extractor: Optional[CandidateExtractorCallback] = None,
    ) -> None:
        self.config = config
        self.progress = progress
        self.browser_fetch = browser_fetch
        self.query_planner = query_planner
        self.brief_planner = brief_planner
        self.candidate_extractor = candidate_extractor
        self.search_cache: TTLCache[List[SearchResult]] = TTLCache(256)
        self.document_cache: TTLCache[SourceDocument] = TTLCache(128)
        self._active_report: Optional[ResearchReport] = None
        self._acquisition_deadline: Optional[float] = None
        self._query_limit: Optional[int] = None
        self._queries_used = 0
        self._source_limit: Optional[int] = None
        self._source_attempts = 0
        self._concurrency_limit: Optional[int] = None
        self._iterations_limit = 1
        self._llm_call_limit: Optional[int] = None
        self._llm_calls_used = 0
        self._source_timeout_s: Optional[float] = None
        self._is_background_sustained = False

    def _configure_budget(self, mode: ResearchMode, *, sustained: bool) -> float:
        scope = "sustained" if sustained else mode.value
        limits = {
            "quick": (20.0, 4.0, 2, 4, 2, 1, 8.0, 1),
            "standard": (75.0, 12.0, 6, 12, 4, 2, 12.0, 3),
            "deep": (150.0, 20.0, 12, 24, 5, 4, 15.0, 6),
            "sustained": (300.0, 30.0, 24, 40, 6, 6, 20.0, 10),
        }
        total_s, reserve_s, queries, sources, concurrency, iterations, per_source_s, llm_calls = limits[scope]
        timeout_name = f"research_total_timeout_{scope}_s"
        total_s = float(getattr(self.config, timeout_name, total_s))
        reserve_s = float(
            getattr(self.config, f"research_synthesis_reserve_{scope}_s", reserve_s)
        )
        reserve_s = min(reserve_s, max(0.1, total_s * 0.25))
        self._acquisition_deadline = time.perf_counter() + max(0.0, total_s - reserve_s)
        self._query_limit = min(
            max(1, int(getattr(self.config, "research_max_search_queries", queries))), queries
        )
        self._queries_used = 0
        self._source_limit = min(
            max(1, int(getattr(self.config, "research_max_sources", sources))), sources
        )
        self._source_attempts = 0
        self._concurrency_limit = min(
            max(1, int(getattr(self.config, "research_max_concurrency", concurrency))), concurrency
        )
        self._iterations_limit = min(
            iterations,
            max(1, int(getattr(self.config, "research_max_iterations", iterations))),
        )
        self._llm_call_limit = min(
            llm_calls,
            max(1, int(getattr(self.config, "research_max_llm_calls", llm_calls))),
        )
        self._llm_calls_used = 0
        self._source_timeout_s = min(
            max(0.1, float(getattr(self.config, "research_fetch_timeout_s", per_source_s))),
            per_source_s,
        )
        self._is_background_sustained = sustained
        return max(0.0, total_s - reserve_s)

    def _consume_llm_call(self) -> bool:
        if self._llm_call_limit is not None and self._llm_calls_used >= self._llm_call_limit:
            return False
        self._llm_calls_used += 1
        return True

    def _remaining_acquisition_s(self) -> Optional[float]:
        if self._acquisition_deadline is None:
            return None
        return max(0.0, self._acquisition_deadline - time.perf_counter())

    async def _notify(self, progress: ResearchProgress) -> None:
        if self.progress is None:
            return
        result = self.progress(progress)
        if inspect.isawaitable(result):
            await result

    def _providers(self):
        from charlie.research.providers import BingProvider, DuckDuckGoProvider, SearXNGProvider, YaCyProvider

        provider_timeout = self._source_timeout_s or float(
            getattr(self.config, "research_fetch_timeout_s", 20)
        )
        providers = []
        if getattr(self.config, "searxng_url", ""):
            providers.append(
                SearXNGProvider(
                    self.config.searxng_url,
                    engines=getattr(self.config, "searxng_engines", ""),
                    fallback_engines=getattr(self.config, "searxng_fallback_engines", ""),
                )
            )
        if getattr(self.config, "research_ddg_enabled", True):
            providers.append(
                DuckDuckGoProvider(
                    timeout_s=provider_timeout,
                    endpoint=getattr(self.config, "research_ddg_endpoint", ""),
                )
            )
        if getattr(self.config, "research_bing_enabled", False):
            providers.append(
                BingProvider(
                    endpoint=getattr(self.config, "research_bing_endpoint", ""),
                    timeout_s=provider_timeout,
                )
            )
        if getattr(self.config, "research_yacy_enabled", False) and getattr(self.config, "research_yacy_url", ""):
            providers.append(
                YaCyProvider(
                    base_url=self.config.research_yacy_url,
                    timeout_s=provider_timeout,
                    resource=getattr(self.config, "research_yacy_resource", "local"),
                    verify=getattr(self.config, "research_yacy_verify", "cacheonly"),
                )
            )
        configured_order = [
            item.strip().lower()
            for item in str(getattr(self.config, "research_provider_order", "")).split(",")
            if item.strip()
        ]
        if configured_order:
            priority = {name: index for index, name in enumerate(configured_order)}
            providers.sort(key=lambda provider: priority.get(provider.name.lower(), len(priority)))
        return providers

    def decide(self, query: str, requested_mode: str = "auto") -> ResearchDecision:
        if not getattr(self.config, "research_enabled", True):
            return ResearchDecision(False, None, "research disabled")
        return route(query, requested_mode)

    def plan(
        self,
        query: str,
        mode: ResearchMode,
        domain_filters: Optional[List[str]] = None,
    ) -> ResearchPlan:
        return build_plan(
            query,
            mode,
            max_queries=min(
                int(getattr(self.config, "research_max_search_queries", 24)),
                self._query_limit
                or {ResearchMode.QUICK: 2, ResearchMode.STANDARD: 6, ResearchMode.DEEP: 12}[mode],
            ),
            market=str(getattr(self.config, "research_market", "IN")),
            locale=str(getattr(self.config, "research_locale", "en-IN")),
            domain_filters=domain_filters,
        )

    def _extract_evidence(
        self,
        sources: List[SourceDocument],
        query: str,
        policy: str = "official_required",
    ) -> List[EvidenceItem]:
        per_source = max(1, int(getattr(self.config, "research_evidence_per_source", 8)))
        ceiling = per_source * max(1, len(sources))
        return build_evidence(
            sources,
            query,
            per_source_max=per_source,
            max_items=ceiling,
            policy=policy,
        )

    async def _search(self, plan: ResearchPlan) -> List[SearchResult]:
        providers = self._providers()
        if not providers:
            if self._active_report is not None:
                self._active_report.provider_outcomes.append(
                    {
                        "provider": "configured",
                        "status": "unavailable",
                        "result_count": 0,
                        "error_class": "no_provider_configured",
                    }
                )
            return []
        if self._query_limit is not None:
            available_queries = max(0, self._query_limit - self._queries_used)
            plan.queries = plan.queries[:available_queries]
            if not plan.queries:
                return []
            self._queries_used += len(plan.queries)
        source_cap = self._source_limit or int(getattr(self.config, "research_max_sources", 40))
        limit = max(1, min(int(getattr(self.config, "research_max_sources", 40)), source_cap))
        cache_key = repr(
            (
                tuple(plan.domain_filters),
                tuple((item.text, tuple(item.domain_filters)) for item in plan.queries),
            )
        )
        cached = self.search_cache.get(cache_key)
        if cached is not None:
            return cached
        provider_outcomes: List[dict[str, object]] = []
        results = await search_plan(
            plan,
            providers,
            limit=limit,
            max_concurrency=min(
                2,
                self._concurrency_limit or int(getattr(self.config, "research_max_concurrency", 6)),
            ),
            progress=lambda current, total: logger.debug("Research search progress %d/%d", current, total),
            provider_outcomes=provider_outcomes,
        )
        if self._active_report is not None:
            self._active_report.provider_outcomes.extend(provider_outcomes)
        ranked = rank_search_results(results, plan, limit)
        ttl = 60.0 if plan.required_freshness == "current" else 900.0
        self.search_cache.set(cache_key, ranked, ttl)
        return ranked

    async def _fetch_one(
        self,
        result: SearchResult,
        mode: ResearchMode,
        max_chars: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> Optional[SourceDocument]:
        cache_key = result.canonical_url or result.url
        cached = self.document_cache.get(cache_key)
        if cached is not None and len(cached.content) >= (max_chars or 14000) * 0.8:
            return cached
        status_out: list = []
        try:
            document = await fetch_document(
                result,
                timeout_s=timeout_s or self._source_timeout_s or float(
                    getattr(self.config, "research_fetch_timeout_s", 20)
                ),
                status_out=status_out,
                max_chars=max_chars or 14000,
            )
        except ValueError:
            logger.info("Research URL rejected: %s", result.url)
            document = None
        # ponytail: dynamic fetch stays disabled until its entire connection path
        # can enforce the same destination policy as the pinned HTTP transport.
        if document is None and mode is not ResearchMode.QUICK and (
            getattr(self.config, "research_crawl_enabled", False) or self.browser_fetch is not None
        ):
            logger.debug("Research dynamic fallback unavailable without connection-level egress isolation")
        if status_out:
            logger.debug("Research source returned HTTP %s: %s", status_out[-1], result.url)

        if document is not None:
            document.content = sanitize_source_text(document.content)
            if not document.content.strip():
                return None
            document.refresh_identity()
            self.document_cache.set(cache_key, document, 300.0 if mode is ResearchMode.DEEP else 900.0)
        return document

    async def _rank_sources(
        self,
        documents: Sequence[SourceDocument],
        plan: ResearchPlan,
        mode: ResearchMode,
    ) -> List[SourceDocument]:
        limit = int(getattr(self.config, "research_max_sources", 12))
        if not documents or mode is ResearchMode.QUICK:
            return await rank_documents(documents, plan, limit)

        pairs = [
            (
                document.document_id or document.source_id or document.canonical_url or document.url,
                f"{document.title}. {document.content[:600]}",
            )
            for document in documents
            if document.document_id or document.source_id or document.canonical_url or document.url
        ]
        semantic = await gather_semantic_scores(
            plan.goal,
            pairs,
            base_url=getattr(self.config, "memory_embedding_url", ""),
            model=getattr(self.config, "memory_embedding_model", ""),
        )
        return await rank_documents(documents, plan, limit, semantic=semantic)

    async def _fetch_sources(
        self,
        results: List[SearchResult],
        mode: ResearchMode,
        max_chars: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> List[SourceDocument]:
        configured_max = int(getattr(self.config, "research_max_sources", 40))
        remaining_attempts = (
            max(0, self._source_limit - self._source_attempts)
            if self._source_limit is not None
            else configured_max
        )
        max_sources = min(configured_max, remaining_attempts)
        max_per_domain = max(1, int(getattr(self.config, "research_max_pages_per_domain", 4)))
        concurrency = min(
            int(getattr(self.config, "research_max_concurrency", 6)),
            self._concurrency_limit or int(getattr(self.config, "research_max_concurrency", 6)),
        )
        semaphore = asyncio.Semaphore(max(1, concurrency))
        completed = 0

        selected: List[SearchResult] = []
        domain_counts: dict[str, int] = {}
        for result in results:
            domain = result.domain or "unknown"
            if domain_counts.get(domain, 0) >= max_per_domain:
                continue
            selected.append(result)
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            if len(selected) >= max_sources:
                break
        if max_sources <= 0 or not selected:
            return []
        self._source_attempts += len(selected)
        source_timeout = self._source_timeout_s or float(
            getattr(self.config, "research_fetch_timeout_s", 20)
        )
        remaining = self._remaining_acquisition_s()
        if remaining is not None:
            timeout_s = remaining if timeout_s is None else min(timeout_s, remaining)

        async def fetch_one(result: SearchResult) -> Optional[SourceDocument]:
            nonlocal completed
            try:
                async with semaphore:
                    document = await self._fetch_one(
                        result,
                        mode,
                        **({"max_chars": max_chars} if max_chars is not None else {}),
                        timeout_s=source_timeout,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Research source fetch failed: %s", result.url, exc_info=True)
                document = None
            completed += 1
            try:
                await self._notify(
                    ResearchProgress(
                        "reading",
                        f"Reading source {completed}/{len(selected)}",
                        completed,
                        len(selected),
                        mode,
                    )
                )
            except Exception:
                logger.debug("Research progress callback failed", exc_info=True)
            return document

        tasks = [asyncio.create_task(fetch_one(item)) for item in selected]
        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout_s)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        documents: List[SourceDocument] = []
        for task in tasks:
            if task not in done or task.cancelled():
                continue
            try:
                document = task.result()
            except Exception:
                logger.warning("Research source worker failed", exc_info=True)
                continue
            if document is not None:
                documents.append(document)
        return documents

    async def _run_sustained(
        self,
        query: str,
        cancel_event: Optional[asyncio.Event] = None,
        domain_filters: Optional[List[str]] = None,
    ) -> ResearchReport:
        """Bounded discover -> verify loop (Decision 1 & Architecture)."""
        started = time.perf_counter()
        acquisition_budget = self._remaining_acquisition_s()
        if acquisition_budget is None:
            acquisition_budget = 170.0
        acquisition_budget = max(0.0, acquisition_budget)
        budget = PhaseBudget(
            plan_s=acquisition_budget * 0.1,
            discover_s=acquisition_budget * 0.45,
            verify_s=acquisition_budget * 0.45,
            synth_s=0.0,
        )
        phase_timings: Dict[str, float] = {}
        report = ResearchReport(query=query, mode=ResearchMode.DEEP)
        self._active_report = report

        # ---------------------------------------------------------
        # Phase 0: Brief (deterministic parse + optional model fill)
        # ---------------------------------------------------------
        p0_start = time.perf_counter()
        await self._notify(ResearchProgress("planning", "Structuring research requirements", mode=ResearchMode.DEEP))
        brief = _research_brief(query, ResearchMode.DEEP)
        if domain_filters:
            brief.explicit_domains = list(dict.fromkeys(brief.explicit_domains + domain_filters))
        report.brief = brief
        report.coverage = brief.required_subquestions

        plan_deadline = budget.deadline_for(budget.plan_s)
        if self.brief_planner is not None and self._consume_llm_call():
            try:
                extra = await asyncio.wait_for(self.brief_planner(query, brief), timeout=plan_deadline)
                if isinstance(extra, dict):
                    if "aspects" in extra and isinstance(extra["aspects"], list):
                        brief.aspects = list(dict.fromkeys(brief.aspects + extra["aspects"]))
                    if "priority" in extra and isinstance(extra["priority"], list):
                        brief.priority = extra["priority"]
            except Exception as e:
                logger.info("Brief planner finished or skipped: %s", e)

        p0_elapsed = time.perf_counter() - p0_start
        budget.record_used(plan_deadline, p0_elapsed)
        phase_timings["phase_0_brief_ms"] = p0_elapsed * 1000

        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        # ---------------------------------------------------------
        # Phase 1: Discover (queries -> search -> fetch -> candidates)
        # ---------------------------------------------------------
        p1_start = time.perf_counter()
        await self._notify(ResearchProgress("searching", "Finding candidate options", mode=ResearchMode.DEEP))
        disc_queries = discovery_queries(brief)
        disc_plan = ResearchPlan(
            goal=query,
            mode=ResearchMode.DEEP,
            queries=_official_coverage_queries(brief)
            + [ResearchQuery(q, "discovery", list(brief.explicit_domains)) for q in disc_queries],
            domain_filters=list(brief.explicit_domains),
        )
        report.plan = disc_plan

        disc_deadline = budget.deadline_for(budget.discover_s)
        try:
            disc_search_results = await asyncio.wait_for(self._search(disc_plan), timeout=disc_deadline * 0.45)
        except Exception:
            disc_search_results = []

        official_seeds = _official_document_search_results(brief)
        seeded_urls = {result.canonical_url or result.url for result in official_seeds}
        disc_search_results = official_seeds + [
            result for result in disc_search_results
            if (result.canonical_url or result.url) not in seeded_urls
        ]

        report.search_results.extend(disc_search_results)

        # Fetch discovery sources (14,000 chars)
        time_left_p1 = max(2.0, disc_deadline - (time.perf_counter() - p1_start))
        disc_docs = await self._fetch_sources(
            disc_search_results[:10],
            ResearchMode.DEEP,
            max_chars=14000,
            timeout_s=time_left_p1,
        )

        # Classify discovery docs and assign IDs
        for idx, doc in enumerate(disc_docs, start=1):
            doc.source_id = f"D{idx}"
            doc.source_class = classify(doc.url, brief=brief).value

        # Extract candidates
        model_proposals = None
        if self.candidate_extractor is not None and self._consume_llm_call():
            try:
                model_proposals = await asyncio.wait_for(self.candidate_extractor(brief, disc_docs), timeout=5.0)
            except Exception:
                model_proposals = None

        candidates = extract_candidates(disc_docs, brief, model_proposals)
        report.candidates = candidates

        p1_elapsed = time.perf_counter() - p1_start
        budget.record_used(disc_deadline, p1_elapsed)
        phase_timings["phase_1_discover_ms"] = p1_elapsed * 1000

        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        # ---------------------------------------------------------
        # Phase 2: Verify per candidate (registry queries, fetch, facts)
        # ---------------------------------------------------------
        p2_start = time.perf_counter()
        verify_deadline = budget.deadline_for(budget.verify_s)
        verified_sources: List[SourceDocument] = []
        verified_facts: List[Fact] = []
        max_verify_chars = int(getattr(self.config, "research_verify_fetch_chars", 60000))
        max_rounds = int(getattr(self.config, "research_max_verify_rounds", 2))

        # If release entity kind: verify official canonical release pages
        if brief.entity_kind == "release" and not is_llm_release_brief(brief):
            await self._notify(ResearchProgress("reading", "Verifying official release documentation", mode=ResearchMode.DEEP))
            rel_plan = ResearchPlan(
                goal=f"{brief.topic} official downloads",
                mode=ResearchMode.DEEP,
                queries=[ResearchQuery(q, "release", list(brief.explicit_domains)) for q in discovery_queries(brief)],
                domain_filters=list(brief.explicit_domains),
            )
            rel_results = await self._search(rel_plan)
            # Ensure canonical official release endpoints are always included
            canonical_urls = canonical_release_urls(brief)
            seen_r_urls = {r.canonical_url or r.url for r in rel_results}
            for cu in dict.fromkeys(canonical_urls):
                if cu not in seen_r_urls:
                    rel_results.insert(
                        0,
                        SearchResult(
                            title=f"{brief.topic} Official Downloads",
                            url=cu,
                            provider="canonical",
                            rank=0,
                        ),
                    )
            rel_docs = await self._fetch_sources(
                rel_results[:6],
                ResearchMode.DEEP,
                max_chars=max_verify_chars,
                timeout_s=max(0.0, verify_deadline - (time.perf_counter() - p2_start)),
            )
            for idx, doc in enumerate(rel_docs, start=1):
                doc.source_id = f"S{idx}"
                doc.source_class = classify(doc.url, brief=brief).value
                verified_sources.append(doc)
                facts = extract_facts_from_document(doc, policy=brief.source_policy)
                verified_facts.extend(facts)

            from charlie.research.releases import is_stable_version, pick_stable
            stable_res = pick_stable(rel_docs, brief=brief)
            if stable_res:
                ver, dt, sid = stable_res
                subject_name = "Python" if "python" in brief.topic.lower() else brief.topic.title()
                cand_name = f"{subject_name} {ver}"
                report.candidates = [Candidate(name=cand_name, brand=subject_name, quote=f"{subject_name} {ver} - {dt}", source_id=sid)]
                primary_url = next((d.url for d in rel_docs if d.source_id == sid), "https://www.python.org/downloads/")
                stable_facts = [
                    Fact(candidate=cand_name, aspect="version", value=ver, quote=ver, source_id=sid, source_class=SourceClass.OFFICIAL.value, extractor="releases:pick_stable", url=primary_url),
                ]
                if dt:
                    stable_facts.append(
                        Fact(candidate=cand_name, aspect="release_date", value=dt, quote=dt, source_id=sid, source_class=SourceClass.OFFICIAL.value, extractor="releases:pick_stable", url=primary_url)
                    )
                verified_facts = stable_facts + [
                    f for f in verified_facts
                    if (f.aspect not in ("version", "release_date") or is_stable_version(f.value))
                    and f.value != ver
                ]

        else:
            # Candidate verification loop
            for candidate in candidates:
                for doc in disc_docs:
                    if doc.source_id == candidate.source_id or _matches_candidate_variant(candidate.name, doc.title, doc.url, doc.content[:1000]):
                        disc_facts = extract_facts_from_document(
                            doc,
                            candidate_name=candidate.name,
                            candidate_brand=candidate.brand,
                            policy=brief.source_policy,
                        )
                        for f in disc_facts:
                            if _matches_candidate_variant(candidate.name, doc.title, doc.url, f.quote):
                                verified_facts.append(f)

            def _candidate_coverage(candidate: Candidate) -> tuple[int, int, int]:
                candidate_key = re.sub(r"[^\w\s]", "", candidate.name).lower().strip()
                covered = {
                    fact.aspect
                    for fact in verified_facts
                    if fact.candidate
                    and re.sub(r"[^\w\s]", "", fact.candidate).lower().strip() == candidate_key
                    and fact.aspect in brief.aspects
                    and citable_for(fact.aspect, fact.source_class, brief.source_policy)
                }
                return (len(covered), len([fact for fact in verified_facts if fact.candidate and re.sub(r"[^\w\s]", "", fact.candidate).lower().strip() == candidate_key]), candidate.mentions)

            candidates = sorted(candidates, key=_candidate_coverage, reverse=True)
            report.candidates = candidates
            slots_to_verify = candidates[: (brief.option_count or 3) + 2]
            round_num = 0

            while round_num < max_rounds and slots_to_verify:
                round_num += 1
                for cand_idx, candidate in enumerate(slots_to_verify, start=1):
                    if (time.perf_counter() - p2_start) >= verify_deadline or (cancel_event and cancel_event.is_set()):
                        break

                    cand_brand = candidate.brand or ""
                    brand_host = OFFICIAL_REGISTRY.get(cand_brand.lower(), {}).get("domains", {"official"})
                    primary_host = next(iter(brand_host), "official")
                    await self._notify(
                        ResearchProgress(
                            "reading",
                            f"Verifying {candidate.name} on {primary_host} ({cand_idx}/{len(slots_to_verify)})",
                            cand_idx,
                            len(slots_to_verify),
                            ResearchMode.DEEP,
                        )
                    )

                    v_queries = verification_queries(candidate, brief)
                    v_plan = ResearchPlan(
                        goal=candidate.name,
                        mode=ResearchMode.DEEP,
                        queries=[ResearchQuery(q, "verification", list(brief.explicit_domains)) for q in v_queries],
                    )
                    v_results = await self._search(v_plan)

                    # Fetch official and store verification documents (60,000 chars)
                    v_docs = await self._fetch_sources(
                        v_results[:4],
                        ResearchMode.DEEP,
                        max_chars=max_verify_chars,
                        timeout_s=max(0.0, verify_deadline - (time.perf_counter() - p2_start)),
                    )
                    for doc in v_docs:
                        doc.source_class = classify(doc.url, brief=brief, candidate_brand=candidate.brand).value
                        # Filter out forums/social if official required
                        if brief.source_policy == "official_required" and doc.source_class == SourceClass.FORUM_SOCIAL.value:
                            continue

                        # Product-variant guard: only associate if page matches candidate variant
                        if not _matches_candidate_variant(candidate.name, doc.title, doc.url, doc.content[:1000]):
                            continue

                        verified_sources.append(doc)
                        extracted = extract_facts_from_document(
                            doc,
                            candidate_name=candidate.name,
                            candidate_brand=candidate.brand,
                            policy=brief.source_policy,
                        )
                        # Filter extracted facts with variant guard on quote
                        for f in extracted:
                            if _matches_candidate_variant(candidate.name, doc.title, doc.url, f.quote):
                                verified_facts.append(f)

                # Coverage check
                complete_cands = [c for c in candidates if _is_candidate_complete(c, verified_facts, brief)]
                if len(complete_cands) >= (brief.option_count or 3):
                    break  # Coverage reached!
                # If gap exists and time remains, promote any unverified candidate slots
                verified_names = {c.name for c in slots_to_verify}
                remaining_candidates = [c for c in candidates if c.name not in verified_names]
                if not remaining_candidates or (time.perf_counter() - p2_start) >= verify_deadline - 10.0:
                    break
                slots_to_verify = remaining_candidates[:2]

        p2_elapsed = time.perf_counter() - p2_start
        phase_timings["phase_2_verify_ms"] = p2_elapsed * 1000

        # Assign stable source IDs (S1, S2, ...)
        source_documents = verified_sources + disc_docs
        if brief.source_policy == "official_required":
            eligible_documents = [
                doc
                for doc in source_documents
                if _source_allowed(doc, brief.source_policy, brief.explicit_domains)
            ]
            eligible_facts = {
                (doc.source_id, doc.url, doc.canonical_url)
                for doc in eligible_documents
            }
            verified_facts = [
                fact
                for fact in verified_facts
                if fact.source_class in {
                    SourceClass.OFFICIAL.value,
                    SourceClass.OFFICIAL_UNVERIFIED.value,
                }
                and any(
                    fact.source_id == source_id and fact.url in {url, canonical_url}
                    for source_id, url, canonical_url in eligible_facts
                )
            ]
            source_documents = eligible_documents

        unique_sources: List[SourceDocument] = []
        seen_urls: Set[str] = set()
        for doc in source_documents:
            norm_url = doc.canonical_url or doc.url
            if norm_url not in seen_urls:
                seen_urls.add(norm_url)
                unique_sources.append(doc)

        old_doc_ids = {id(doc): getattr(doc, "source_id", None) for doc in unique_sources}

        report.sources = unique_sources
        report.citations = assign_citations(report.sources)
        report.facts = verified_facts

        # Re-map source_id on facts to match citations
        url_to_source_id = {doc.url: doc.source_id for doc in report.sources}
        for doc in report.sources:
            if getattr(doc, "canonical_url", None):
                url_to_source_id[doc.canonical_url] = doc.source_id
        old_id_to_new_id = {}
        for doc in report.sources:
            old_sid = old_doc_ids.get(id(doc))
            if old_sid:
                old_id_to_new_id[old_sid] = doc.source_id
            if hasattr(doc, "source_id") and doc.source_id:
                old_id_to_new_id[doc.source_id] = doc.source_id
        updated_facts: List[Fact] = []
        for f in report.facts:
            mapped_id = url_to_source_id.get(f.url) or old_id_to_new_id.get(f.source_id) or f.source_id
            updated_facts.append(
                Fact(
                    candidate=f.candidate,
                    aspect=f.aspect,
                    value=f.value,
                    quote=f.quote,
                    source_id=mapped_id,
                    source_class=f.source_class,
                    extractor=f.extractor,
                    url=f.url,
                    fetched_at=f.fetched_at,
                )
            )
        report.facts = list({(f.candidate, f.aspect, f.value, f.source_id): f for f in updated_facts}.values())

        # Remap candidate source_ids to valid citation IDs
        if getattr(report, "candidates", None):
            updated_candidates: List[Candidate] = []
            for c in report.candidates:
                c_mapped_id = old_id_to_new_id.get(c.source_id) or c.source_id
                updated_candidates.append(
                    Candidate(
                        name=c.name,
                        brand=c.brand,
                        quote=c.quote,
                        source_id=c_mapped_id,
                        mentions=getattr(c, "mentions", 1),
                        access=getattr(c, "access", None),
                        release_date=getattr(c, "release_date", None),
                    )
                )
            report.candidates = updated_candidates

        # Build evidence from verified sources
        report.evidence = self._extract_evidence(report.sources, query, policy=brief.source_policy)
        report.bind_passages()

        # Coverage evaluation
        complete_count = None
        target_count = None
        if brief.entity_kind == "product":
            complete_count = len([c for c in candidates if _is_candidate_complete(c, report.facts, brief)])
            target_count = brief.option_count or 3
            if complete_count < target_count:
                report.gaps.append(
                    f"Found {complete_count} of {target_count} requested options meeting all official specifications and budget."
                )
                report.partial = True

        total_elapsed = (time.perf_counter() - started) * 1000
        report.duration_ms = total_elapsed
        report.phase_ms = phase_timings
        coverage_complete = _update_report_coverage(report)

        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
        elif brief.entity_kind == "product" and (
            not report.facts or (complete_count is not None and complete_count < (target_count or 1))
        ):
            report.stop_reason = "partial-evidence" if (report.facts or report.evidence) else "insufficient-evidence"
        elif not report.facts and not report.evidence:
            report.stop_reason = "insufficient-evidence"
        elif not coverage_complete:
            report.stop_reason = "partial-evidence"
            report.partial = True
        else:
            report.stop_reason = "evidence-sufficient"

        # Structured log line for acceptance evidence
        official_sources_count = len(
            [s for s in report.sources if s.source_class in (SourceClass.OFFICIAL.value, SourceClass.OFFICIAL_STORE.value)]
        )
        logger.info(
            "Research sustained complete | topic=%r | candidates=%d | complete=%d | "
            "official_sources=%d | facts=%d | stop=%s | partial=%s | duration_ms=%.0f",
            brief.topic,
            len(report.candidates),
            len([c for c in report.candidates if _is_candidate_complete(c, report.facts, brief)]),
            official_sources_count,
            len(report.facts),
            report.stop_reason,
            report.partial,
            report.duration_ms,
        )
        return report

    async def _run_inner(
        self,
        query: str,
        mode: ResearchMode,
        cancel_event: Optional[asyncio.Event],
        domain_filters: Optional[List[str]] = None,
    ) -> ResearchReport:
        started = time.perf_counter()
        brief = _research_brief(query, mode)
        if domain_filters:
            brief.explicit_domains = list(dict.fromkeys(brief.explicit_domains + domain_filters))
        plan = self.plan(query, mode, domain_filters=brief.explicit_domains)
        report = ResearchReport(query=query, mode=mode, plan=plan)
        self._active_report = report
        report.brief = brief
        report.coverage = brief.required_subquestions

        await self._notify(ResearchProgress("planning", f"Planning {mode.value} research", mode=mode))
        if brief.entity_kind == "release" and not is_llm_release_brief(brief):
            plan.queries = [ResearchQuery(text, "official release", list(plan.domain_filters))
                            for text in discovery_queries(brief)]
        elif mode is not ResearchMode.QUICK:
            coverage_queries = _official_coverage_queries(brief)
            planned_queries = []
            if self.query_planner is not None and self._consume_llm_call():
                queries = await self.query_planner(query)
                planned_queries = [
                    ResearchQuery(text, "targeted evidence", list(plan.domain_filters))
                    for text in queries[:max(1, int(getattr(self.config, "research_max_search_queries", 6)))]
                ]
            plan.queries = coverage_queries + (planned_queries or plan.queries)
        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        await self._notify(ResearchProgress("searching", "Searching current sources", mode=mode))
        report.search_results = await self._search(plan)
        if brief.entity_kind == "release" and mode is not ResearchMode.QUICK:
            seeds = [SearchResult(f"{brief.topic} official releases", url, provider="canonical", rank=0)
                     for url in canonical_release_urls(brief)]
            report.search_results = seeds + report.search_results
        elif mode is not ResearchMode.QUICK:
            seeds = _official_document_search_results(brief)
            seeded_urls = {result.canonical_url or result.url for result in seeds}
            report.search_results = seeds + [
                result for result in report.search_results
                if (result.canonical_url or result.url) not in seeded_urls
            ]
        if not report.search_results:
            report.errors.append("No configured research provider returned results")
            outcomes = report.provider_outcomes
            report.stop_reason = (
                "provider-exhausted"
                if outcomes and all(item.get("status") in {"failed", "unavailable"} for item in outcomes)
                else "no-results"
            )
            report.duration_ms = (time.perf_counter() - started) * 1000
            return report

        await self._notify(
            ResearchProgress(
                "found",
                f"Found {len(report.search_results)} search results",
                len(report.search_results),
                len(report.search_results),
                mode,
            )
        )
        if cancel_event and cancel_event.is_set():
            report.stop_reason = "cancelled"
            return report

        if mode is not ResearchMode.QUICK:
            await self._notify(ResearchProgress("reading", "Reading selected sources", mode=mode))
            documents = await self._fetch_sources(
                report.search_results,
                mode,
                timeout_s=self._remaining_acquisition_s(),
            )
            for doc in documents:
                doc.source_class = classify(doc.url, brief=brief).value
            if brief.source_policy == "official_required":
                documents = [
                    doc for doc in documents
                    if _source_allowed(doc, brief.source_policy, brief.explicit_domains)
                ]
            report.sources = documents
            report.citations = assign_citations(report.sources)
            report.evidence = self._extract_evidence(report.sources, query, policy=brief.source_policy)
            report.bind_passages()
            for doc in report.sources:
                report.facts.extend(extract_facts_from_document(doc, policy=brief.source_policy))
            report.sources = await self._rank_sources(documents, plan, mode)

        report.citations = (
            assign_citations(report.sources)
            if report.sources
            else assign_search_citations(report.search_results)
        )
        report.evidence = self._extract_evidence(report.sources, query, policy=brief.source_policy)
        report.bind_passages()

        # Extract deterministic facts on inline path
        if report.sources:
            report.facts = []
            for doc in report.sources:
                facts = extract_facts_from_document(doc, policy=brief.source_policy)
                report.facts.extend(facts)

        if report.sources:
            grounded_ids = {item.source_id for item in report.evidence}
            report.sources = [item for item in report.sources if item.source_id in grounded_ids]
            report.citations = assign_citations(report.sources)
            report.evidence = self._extract_evidence(report.sources, query, policy=brief.source_policy)
            report.bind_passages()
        elif mode is not ResearchMode.QUICK:
            report.citations = []

        if brief.entity_kind == "release" and not is_llm_release_brief(brief) and report.sources:
            stable = pick_stable(report.sources, brief=brief)
            if stable:
                version, released, source_id = stable
                source = next(doc for doc in report.sources if doc.source_id == source_id)
                report.facts = [Fact(candidate=brief.topic, aspect="version", value=version,
                    quote=version, source_id=source_id, source_class=source.source_class,
                    extractor="releases:pick_stable", url=source.url)]
                if released:
                    report.facts.append(Fact(candidate=brief.topic, aspect="release_date", value=released,
                        quote=released, source_id=source_id, source_class=source.source_class,
                        extractor="releases:pick_stable", url=source.url))

        if mode is ResearchMode.DEEP and brief.entity_kind == "general":
            seen_queries = {item.text.casefold() for item in plan.queries}
            seen_urls = {result.canonical_url or result.url for result in report.search_results}
            for _iteration in range(max(0, self._iterations_limit - 1)):
                if _update_report_coverage(report):
                    break
                unresolved = [item for item in report.coverage if item.status == "unresolved"]
                followup_queries = []
                for item in unresolved:
                    if item.required_fields and item.required_fields[0].startswith("source:"):
                        domain = item.required_fields[0].split(":", 1)[1]
                        query_text = f"{item.question} {domain}"
                    elif item.required_fields and item.required_fields[0].startswith("independent_sources:"):
                        query_text = f"{clean_query(item.question)} distinct primary sources"
                    else:
                        query_text = f"{clean_query(item.question)} primary documentation"
                    if query_text.casefold() not in seen_queries:
                        followup_queries.append(
                            ResearchQuery(query_text, "evidence gap", list(plan.domain_filters))
                        )
                        seen_queries.add(query_text.casefold())
                    if len(followup_queries) >= 2:
                        break
                if not followup_queries:
                    break
                before = sum(item.status != "unresolved" for item in report.coverage)
                followup = ResearchPlan(
                    goal=query,
                    mode=mode,
                    queries=followup_queries,
                    domain_filters=list(plan.domain_filters),
                )
                extra = await self._search(followup)
                extra = [
                    result for result in extra
                    if (result.canonical_url or result.url) not in seen_urls
                ]
                if not extra:
                    break
                for result in extra:
                    seen_urls.add(result.canonical_url or result.url)
                report.search_results.extend(extra)
                if report.plan is not None:
                    report.plan.queries.extend(followup.queries)
                new_documents = await self._fetch_sources(
                    extra,
                    mode,
                    timeout_s=self._remaining_acquisition_s(),
                )
                if not new_documents:
                    break
                for document in new_documents:
                    document.source_class = classify(document.url, brief=brief).value
                unique_documents = {
                    document.canonical_url or document.url: document
                    for document in report.sources + new_documents
                }
                report.sources = await self._rank_sources(
                    list(unique_documents.values()), plan, mode
                )
                report.citations = assign_citations(report.sources)
                report.evidence = self._extract_evidence(
                    report.sources, query, policy=brief.source_policy
                )
                report.bind_passages()
                report.facts = [
                    fact
                    for document in report.sources
                    for fact in extract_facts_from_document(document, policy=brief.source_policy)
                ]
                _update_report_coverage(report)
                after = sum(item.status != "unresolved" for item in report.coverage)
                if after <= before:
                    break

        report.products = []
        if is_shopping_query(query, plan):
            try:
                report.products = extract_products(
                    report.sources,
                    query,
                    str(getattr(self.config, "research_currency", "INR")),
                )
            except Exception as exc:
                logger.warning("Product enrichment failed: query=%r error=%s", query, exc, exc_info=True)
                report.products = []
        report.media = media_results(report.search_results)
        report.confidence = compute_confidence(report.evidence, report.sources)
        coverage_complete = _update_report_coverage(report)
        if report.evidence and coverage_complete:
            report.stop_reason = "evidence-sufficient"
        elif report.evidence:
            report.stop_reason = "partial-evidence"
            report.partial = True
        elif mode is ResearchMode.QUICK and report.search_results:
            report.stop_reason = "search-snippets-only"
        else:
            report.stop_reason = "insufficient-evidence"
        report.duration_ms = (time.perf_counter() - started) * 1000
        await self._notify(ResearchProgress("done", "Research evidence ready", mode=mode))
        domains = grounded_domain_count(report.evidence, report.sources)
        logger.info(
            "Research complete: mode=%s queries=%d results=%d sources=%d citations=%d "
            "grounded_domains=%d source_diversity_confidence=%s stop=%s duration_ms=%.0f",
            mode.value,
            len(plan.queries),
            len(report.search_results),
            len(report.sources),
            len(report.citations),
            domains,
            "not-measurable" if report.confidence is None else f"{report.confidence:.2f}",
            report.stop_reason,
            report.duration_ms,
        )
        return report

    async def run(
        self,
        query: str,
        mode: str = "auto",
        *,
        cancel_event: Optional[asyncio.Event] = None,
        domain_filters: Optional[List[str]] = None,
        sustained: bool = False,
    ) -> ResearchReport:
        decision = self.decide(query, mode)
        if not decision.should_research or decision.mode is None:
            report = ResearchReport(query=query, mode=ResearchMode.QUICK, stop_reason=decision.reason)
            report.finalize_outcome()
            return report

        brief_kind = parse_brief(query).entity_kind
        is_sustained = (
            sustained
            or is_sustained_research_query(query, decision)
            or brief_kind == "product"
        )
        selected_mode = ResearchMode.DEEP if is_sustained else decision.mode
        timeout = self._configure_budget(selected_mode, sustained=sustained)
        self._active_report = None
        try:
            if is_sustained and brief_kind != "general":
                operation = self._run_sustained(
                    query,
                    cancel_event=cancel_event,
                    domain_filters=domain_filters,
                )
            else:
                operation = self._run_inner(
                    query,
                    selected_mode,
                    cancel_event,
                    domain_filters=domain_filters,
                )
            report = await asyncio.wait_for(operation, timeout=max(0.01, timeout))
        except asyncio.TimeoutError:
            logger.warning("Research acquisition budget expired: mode=%s query=%r", selected_mode.value, query)
            report = self._active_report or ResearchReport(query=query, mode=selected_mode)
            report.stop_reason = "timeout"
            report.partial = bool(report.evidence or report.facts)
            report.errors.append("Research acquisition budget exhausted")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Research failed: mode=%s query=%r", selected_mode.value, query, exc_info=True)
            report = self._active_report or ResearchReport(query=query, mode=selected_mode)
            report.stop_reason = "error"
            report.errors.append(type(exc).__name__)
        report.finalize_outcome()
        report.llm_calls_used = self._llm_calls_used
        report.llm_call_limit = self._llm_call_limit
        return report

    def run_sync(
        self,
        query: str,
        mode: str = "auto",
        *,
        domain_filters: Optional[List[str]] = None,
        sustained: bool = False,
    ) -> ResearchReport:
        return asyncio.run(self.run(query, mode, domain_filters=domain_filters, sustained=sustained))
