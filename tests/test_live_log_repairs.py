"""Regressions from the 2026-10-06 native log; these checks are TEST/MOCK."""
import queue
from types import SimpleNamespace

import numpy as np
import pytest

from charlie.asr_worker import _build_transcribe_kwargs
from charlie.background_task import assemble_answer
from charlie.research.engine import _matches_candidate_variant
from charlie.research.models import Candidate, Fact, ResearchBrief, ResearchMode, ResearchReport
from charlie.router import match_close_app
from charlie.voice import VoiceEngine


def test_rms_capture_threshold_does_not_override_silero_probability():
    kwargs = _build_transcribe_kwargs(False, {"capture": {"vad_threshold": 0.01}}, "en", {"vad_threshold": 0.45})
    assert kwargs["vad_parameters"]["threshold"] == 0.45


def test_ptt_audio_is_not_trimmed_again_by_silero():
    kwargs = _build_transcribe_kwargs(False, {"capture": {"capture_mode": "ptt"}}, "en", {})
    assert kwargs["vad_filter"] is False


def test_soft_words_continue_an_existing_phrase():
    assert VoiceEngine._speech_continues(0.006, 0.01, 0.0002)
    assert not VoiceEngine._speech_continues(0.0004, 0.01, 0.0002)


def test_addressed_closed_command_recovers_without_changing_statements():
    assert match_close_app("Charlie closed calculator.")[0] == ["calculator"]
    assert match_close_app("I closed calculator.") is None
    assert match_close_app("Charlie closed calculator yesterday.") is None


def test_candidate_variant_requires_identity_and_rejects_other_product_row():
    name = "HP Victus 15-FA2381TX Gaming"
    assert not _matches_candidate_variant(name, "HP laptops", "https://hp.com/laptops", "Save ₹36,990")
    assert not _matches_candidate_variant(name, name, "https://hp.com/laptops", "HP Victus FB3120AX ₹36,990")
    assert _matches_candidate_variant(name, name, "https://hp.com/product", "RTX 4050 6 GB")


def test_incomplete_options_never_become_recommendations():
    candidate = Candidate(name="HP Victus 15-FA2381TX Gaming", brand="HP", quote="HP Victus", source_id="S1")
    report = ResearchReport(query="best laptop under 1 lakh", mode=ResearchMode.DEEP,
        brief=ResearchBrief(topic="laptop", entity_kind="product", budget=100000, aspects=["gpu", "vram", "ram"]),
        candidates=[candidate], facts=[Fact(candidate=candidate.name, aspect="gpu", value="RTX 4050",
            quote="RTX 4050", source_id="S1", source_class="official", extractor="regex")])
    assert "**Recommendation**" not in assemble_answer(report)


def test_product_report_hides_candidates_without_fetched_facts():
    candidate = Candidate(name="Verified Laptop", brand="Dell", quote="Verified Laptop", source_id="S1")
    noise = Candidate(name="Search Noise", brand="HP", quote="Search Noise", source_id="S2")
    report = ResearchReport(
        query="best laptops under 1 lakh",
        mode=ResearchMode.DEEP,
        brief=ResearchBrief(topic="laptops under 1 lakh", entity_kind="product", budget=100000,
            aspects=["gpu", "vram", "ram"], priority=["vram", "gpu", "ram"]),
        candidates=[candidate, noise],
        facts=[
            Fact(candidate=candidate.name, aspect="gpu", value="RTX 3050", quote="RTX 3050", source_id="S1", source_class="retailer", extractor="test"),
            Fact(candidate=candidate.name, aspect="vram", value="6 GB", quote="6 GB", source_id="S1", source_class="retailer", extractor="test"),
            Fact(candidate=candidate.name, aspect="ram", value="16 GB", quote="16 GB", source_id="S1", source_class="retailer", extractor="test"),
            Fact(candidate=candidate.name, aspect="price", value="₹99,999", quote="₹99,999", source_id="S1", source_class="retailer", extractor="test"),
        ],
    )
    answer = assemble_answer(report)
    assert "Verified Laptop" in answer
    assert "Search Noise" not in answer


def test_history_window_counts_user_turns_instead_of_tool_rows():
    from charlie.core import _recent_turn_history

    messages = [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Research laptops"},
        *[{"role": "tool", "content": f"tool result {index}"} for index in range(8)],
        {"role": "assistant", "content": "Research is partial"},
        {"role": "user", "content": "What did you just research?"},
    ]
    kept = _recent_turn_history(messages, 2)
    assert kept[0]["content"] == "Research laptops"
    assert kept[-1]["content"] == "What did you just research?"
    assert sum(message["role"] == "tool" for message in kept) == 8


def test_partial_asr_is_bounded_and_never_delivered_as_a_command():
    voice = VoiceEngine.__new__(VoiceEngine)
    voice.asr_input_queue = queue.Queue()
    voice._partial_pending = False
    voice._partial_utterance_id = "u1"
    voice._partial_last_submit = 0.0
    voice._asr_readiness_lock = __import__("threading").Lock()
    voice._asr_readiness_status = "ready"
    voice._schedule_event_emit = lambda kind, payload: events.append((kind, payload))
    events = []
    assert voice._submit_partial_asr(np.ones(32000, dtype=np.float32), 16000, "u1")
    assert not voice._submit_partial_asr(np.ones(32000, dtype=np.float32), 16000, "u1")
    voice._handle_partial_asr(("hello", 1.0, {"is_partial": True, "utterance_id": "u1"}))
    assert events == [("transcript", {"text": "hello", "partial": True, "utterance_id": "u1"})]
    voice._partial_utterance_id = None
    voice._handle_partial_asr(("stale", 1.0, {"is_partial": True, "utterance_id": "u1"}))
    assert len(events) == 1


def test_speech_cleanup_keeps_versions_and_drops_table_separators():
    cleaned = VoiceEngine._humanize_text("Python 3.14.0 [S1, S2].\n| Name | Price |\n| :--- | :--- |\n| A | ₹78,584 |")
    assert "3.14.0" in cleaned
    assert "S1" not in cleaned and "S2" not in cleaned
    assert "---" not in cleaned and "|" not in cleaned


@pytest.mark.asyncio
async def test_suspended_selected_engine_retries_working_search_engine(monkeypatch):
    from charlie.research.providers import SearXNGProvider
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, params):
            calls.append(dict(params))
            payload = ({"results": [], "unresponsive_engines": [["google cse", "too many requests"]]}
                if params.get("engines") == "google cse" else {"results": [{"title": "Official", "url": "https://example.org/a"}]})
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr("charlie.research.providers.httpx.AsyncClient", Client)
    results = await SearXNGProvider(
        "http://localhost:8081", engines="google cse", fallback_engines="duckduckgo"
    ).search("q", limit=3)
    assert len(results) == 1 and len(calls) == 2
    assert calls[1]["engines"] == "duckduckgo"


@pytest.mark.asyncio
async def test_curated_searx_pool_does_not_fall_back_to_instance_defaults(monkeypatch):
    from charlie.research.providers import SearXNGProvider
    calls = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, params):
            calls.append(dict(params))
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"results": [{"title": "Unrelated", "url": "https://example.org/a"}],
                    "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]},
            )

    monkeypatch.setattr("charlie.research.providers.httpx.AsyncClient", Client)
    results = await SearXNGProvider(
        "http://localhost:8081", engines="brave,duckduckgo,qwant"
    ).search("best laptops for local AI models RTX India", limit=3)
    assert results == []
    assert len(calls) == 1


def test_search_ranking_rejects_dictionary_and_dealer_false_positives():
    from charlie.research.models import ResearchPlan, ResearchQuery, SearchResult
    from charlie.research.ranking import rank_search_results
    query = "best laptops for local AI models RTX under 100000 INR India"
    plan = ResearchPlan(goal=query, mode=ResearchMode.STANDARD, queries=[ResearchQuery(query)])
    hits = [
        SearchResult("BEST Definition", "https://merriam-webster.com/best", "Best meaning", rank=1),
        SearchResult("Triumph India", "https://triumphmotorcycles.in", "Motorcycle dealers in India", rank=2),
        SearchResult("RTX 4060 laptops", "https://example.com/laptops", "India laptop models with RTX 4060 GPU", rank=3),
    ]
    assert [result.title for result in rank_search_results(hits, plan, 5)] == ["RTX 4060 laptops"]


def test_no_research_evidence_is_a_failed_tool_result():
    from charlie.core import _legacy_tool_result_status
    from charlie.tools import ToolExecutionResult
    report = ResearchReport(query="q", mode=ResearchMode.STANDARD, stop_reason="no-results")
    assert _legacy_tool_result_status(ToolExecutionResult(report.legacy_text(), report, "research_report")) == "failed"


@pytest.mark.asyncio
async def test_browser_extraction_reobserves_once_when_navigation_changes(monkeypatch):
    from charlie import core
    from charlie.browser import controller
    from charlie.research.models import SearchResult
    brain = core.Brain.__new__(core.Brain)
    brain.config = SimpleNamespace(browser_enabled=True)
    monkeypatch.setattr(core, "_BROWSER_AVAILABLE", True)
    monkeypatch.setattr("charlie.research.fetch.validate_public_url", lambda url: url)
    class Page:
        calls = 0
        waits = 0
        def goto(self, *args, **kwargs): pass
        def content(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Page.content: Unable to retrieve content because the page is navigating and changing the content.")
            return "<html><body><p>Grounded source text with sufficient detail. " + "Public evidence. " * 30 + "</p></body></html>"
        def wait_for_load_state(self, *args, **kwargs): self.waits += 1
    page = Page()
    monkeypatch.setattr(controller, "run", lambda fn, **kwargs: fn(page))
    doc = await brain._research_browser_fetch(SearchResult("Source", "https://example.org/a", ""))
    assert doc is not None and page.calls == 2 and page.waits == 1


@pytest.mark.asyncio
async def test_inline_release_reads_canonical_pages_when_search_is_empty(monkeypatch):
    from charlie.research.engine import ResearchEngine
    from charlie.research.models import SourceDocument
    engine = ResearchEngine(SimpleNamespace(research_enabled=True, research_max_sources=3))
    seen = []
    async def empty_search(plan): return []
    async def fetch_sources(results, mode):
        seen.extend(result.url for result in results)
        return [SourceDocument(url="https://www.python.org/downloads/", source_id="S1", title="Python downloads",
            domain="python.org", content="Python 3.14.0 - Oct. 7, 2026\nPython 3.15.0a1 - Oct. 8, 2026")]
    async def rank_sources(docs, plan, mode): return docs
    monkeypatch.setattr(engine, "_search", empty_search)
    monkeypatch.setattr(engine, "_fetch_sources", fetch_sources)
    monkeypatch.setattr(engine, "_rank_sources", rank_sources)
    report = await engine._run_inner("latest stable Python release from python.org", ResearchMode.STANDARD, None)
    assert "https://www.python.org/downloads/" in seen
    assert report.stop_reason == "evidence-sufficient"
    assert report.facts and report.citations


def test_release_dates_are_bound_to_the_selected_version():
    from charlie.research.releases import pick_stable
    from charlie.research.models import SourceDocument
    doc = SourceDocument(url="https://python.org/downloads/", source_id="S1",
        content="Python 3.13.9 details. Python 3.14.0 details. Release date: Oct. 7, 2026")
    assert pick_stable([doc]) == ("3.14.0", "", "S1")


def test_app_reply_keeps_verification_details_out_of_user_text(monkeypatch):
    from charlie import tools
    detail = "Opened the app and confirmed its exact window through bounded Cua."
    monkeypatch.setattr(tools, "_desktop_ready", lambda: True)
    monkeypatch.setattr("charlie.computer.backend.get_backend",
        lambda: SimpleNamespace(open_app=lambda *args: detail))
    result = tools.desktop_open_app(["calculator"])
    assert result.model_text == "Calculator is open."
    assert result.structured_data["verification_detail"] == detail
    assert result.structured_data["verified"] is True


def test_text_and_telegram_replies_do_not_enqueue_speech():
    from main import _safe_speak
    calls = []
    voice = SimpleNamespace(speak=lambda *args: calls.append(args))
    for channel in ("web", "telegram", "console"):
        _safe_speak(voice, "Answer", "neutral", channel=channel)
    assert calls == []
    _safe_speak(voice, "Answer", "neutral", channel="voice")
    assert calls == [("Answer", "neutral")]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["voice", "telegram", "web"])
async def test_background_delivery_stays_with_origin(channel, tmp_path):
    from main import _deliver_background_result
    from charlie.results import ResultsStore
    path = str(tmp_path / "results.db")
    store = ResultsStore(path)
    store.store("task", "Summary", "Findings", 2)
    store.close()
    sent, spoken = [], []
    async def send(*args): sent.append(args)
    await _deliver_background_result("task", "Summary", db_path=path,
        telegram_bot=SimpleNamespace(send_message=send), telegram_user_id=42,
        voice=SimpleNamespace(is_ready=True, speak=lambda *args: spoken.append(args)), channel=channel)
    assert bool(sent) == (channel == "telegram")
    assert bool(spoken) == (channel == "voice")


@pytest.mark.asyncio
async def test_web_approval_uses_its_callback_without_voice_prompt():
    from charlie.core import Brain, resolve_tool_approval
    from charlie.config import Config
    thoughts = []
    async def approve(request_id, *args, **kwargs):
        assert resolve_tool_approval(request_id, True, expected_platform="web")
        return True
    brain = Brain(Config(llm_url="http://localhost:11434/v1", llm_key="no-key", llm_model="dummy"),
        on_thought_callback=thoughts.append, on_tool_approval_request=approve)
    try:
        assert await brain.request_tool_approval("desktop_close_app", {"apps": ["calculator"]},
            "Close Calculator", platform="web")
    finally:
        await brain.close()
    assert thoughts == []


def test_speech_does_not_cancel_a_pending_voice_approval(monkeypatch):
    from main import _preempt_foreground_for_speech
    calls = []
    brain = SimpleNamespace(cancel_chat=lambda: calls.append("brain"))
    task = SimpleNamespace(cancel=lambda: calls.append("task"))
    monkeypatch.setattr("charlie.core.get_active_tool_approval", lambda: ("approval-1", "voice"))
    assert _preempt_foreground_for_speech(brain, task) is False
    assert calls == []
    monkeypatch.setattr("charlie.core.get_active_tool_approval", lambda: None)
    assert _preempt_foreground_for_speech(brain, task) is True
    assert calls == ["brain", "task"]


@pytest.mark.asyncio
async def test_research_voice_uses_the_short_result_and_keeps_full_text(tmp_path):
    from main import _deliver_background_result
    from charlie.results import ResultsStore
    path = str(tmp_path / "spoken.db")
    full = "Full research topic and detailed comparison.\n| Option | Price |\n| Model | 99999 |"
    store = ResultsStore(path)
    store.store("task", "Summary", full, 2)
    store.close()
    spoken = []
    await _deliver_background_result("task", "Summary", db_path=path,
        telegram_bot=None, telegram_user_id=0, channel="voice", spoken_summary="I recommend Model for its memory.",
        voice=SimpleNamespace(is_ready=True, speak=lambda *args: spoken.append(args)))
    assert spoken == [("I recommend Model for its memory.", "neutral")]
    store = ResultsStore(path)
    assert store.get("task").full_result == full
    store.close()


def test_savings_and_instalments_are_not_product_prices():
    from charlie.research.facts import extract_facts_from_document
    from charlie.research.models import SourceDocument
    doc = SourceDocument(url="https://hp.com/product", source_id="S1", source_class="official",
        content="Price: ₹78,584\nSave ₹36,990\nEMI ₹10,000 per month")
    facts = extract_facts_from_document(doc, candidate_name="HP Victus 15-FA2381TX Gaming")
    assert [fact.value for fact in facts if fact.aspect == "price"] == ["₹78,584"]


def test_ordinary_spec_hyphens_do_not_look_like_competing_variants():
    name = "HP Victus 15-FA2381TX Gaming"
    assert _matches_candidate_variant(name, name, "https://hp.com/product", "16 GB DDR5 dual-channel RAM")


def test_gpu_diagnostics_are_not_a_shopping_comparison():
    from charlie.research.search import parse_brief
    assert parse_brief("Why does PyTorch fail with CUDA out of memory when the GPU has free VRAM?").entity_kind == "general"


def test_research_speech_summary_drops_process_narration_and_tables():
    from charlie.background_task import _compact_research_speech
    text = "**Auto-assembled from verified sources — model synthesis unavailable.**\n**Recommendation**: Model X.\n| Option | Price |\n| Model X | ₹99,999 |"
    assert _compact_research_speech(text) == "I recommend Model X."


def test_research_speech_keeps_abbreviated_release_dates_together():
    from charlie.background_task import _compact_research_speech
    text = "The latest stable release is **3.14.8** [S1]. It was released on **Sept. 30, 2026** [S1]."
    assert _compact_research_speech(text) == "The latest stable release is 3.14.8. It was released on Sept. 30, 2026."


def test_long_search_requires_two_subject_terms():
    from charlie.research.models import SearchResult
    from charlie.research.ranking import search_result_matches_query
    query = "best laptops for local AI models RTX under 100000 INR India"
    assert not search_result_matches_query(query, SearchResult("BEST Definition", "https://merriam-webster.com/best", "Best meaning"))
    assert search_result_matches_query(query, SearchResult("RTX laptops", "https://example.com/laptops", "RTX laptop models for local AI"))


def test_product_search_rejects_academic_ai_page_without_product_class():
    from charlie.research.models import SearchResult
    from charlie.research.ranking import search_result_matches_query
    query = "best laptops for local AI models RTX under 100000 INR India"
    academic = SearchResult(
        "Foundations of Generative AI",
        "https://example.org/paper",
        "A survey of AI models, GPU training, and retrieval systems.",
    )
    assert not search_result_matches_query(query, academic)
