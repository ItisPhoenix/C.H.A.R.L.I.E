"""Router heuristic tests — uses importlib to bypass broken charlie.__init__ chain."""
import importlib.util
import os

ROUTER_PATH = os.path.join(os.path.dirname(__file__), "..", "charlie", "llm_router.py")

spec = importlib.util.spec_from_file_location("charlie.llm_router", ROUTER_PATH)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

QC = router.QueryCategory


class TestRouterHeuristic:

    def setup_method(self):
        self.classify = router.RouterHeuristic.classify

    def test_trivial_greetings(self):
        assert self.classify("Hi") == QC.TRIVIAL
        assert self.classify("hello") == QC.TRIVIAL
        assert self.classify("hey") == QC.TRIVIAL
        assert self.classify("bye") == QC.TRIVIAL
        assert self.classify("goodbye") == QC.TRIVIAL

    def test_trivial_politeness(self):
        assert self.classify("Thanks") == QC.TRIVIAL
        assert self.classify("thank you") == QC.TRIVIAL
        assert self.classify("ok") == QC.TRIVIAL
        assert self.classify("okay") == QC.TRIVIAL
        assert self.classify("yes") == QC.TRIVIAL
        assert self.classify("no") == QC.TRIVIAL

    def test_trivial_checkins(self):
        assert self.classify("How are you?") == QC.TRIVIAL
        assert self.classify("what's up") == QC.TRIVIAL
        assert self.classify("Hey, what's up?") == QC.TRIVIAL

    def test_simple_factual(self):
        assert self.classify("What is the capital of France?") == QC.SIMPLE
        assert self.classify("Who is the president?") == QC.SIMPLE
        assert self.classify("Where is Paris?") == QC.SIMPLE
        assert self.classify("When did this happen?") == QC.SIMPLE
        assert self.classify("How many people live there?") == QC.SIMPLE
        assert self.classify("Define ephemeral") == QC.SIMPLE
        assert self.classify("Calculate 12 * 13") == QC.SIMPLE
        assert self.classify("Convert 10 miles to km") == QC.SIMPLE
        assert self.classify("How much does it cost?") == QC.SIMPLE

    def test_complex_analysis(self):
        assert self.classify("Explain quantum mechanics in detail") == QC.COMPLEX
        assert self.classify("Why is the sky blue?") == QC.COMPLEX
        assert self.classify("How to fix a broken pipe") == QC.COMPLEX
        assert self.classify("Compare Python and Rust") == QC.COMPLEX
        assert self.classify("Contrast these two approaches") == QC.COMPLEX
        assert self.classify("Analyze this code") == QC.COMPLEX
        assert self.classify("Evaluate the results") == QC.COMPLEX
        assert self.classify("Elaborate on that point") == QC.COMPLEX

    def test_creative(self):
        assert self.classify("Write me a poem") == QC.CREATIVE
        assert self.classify("Draft an email") == QC.CREATIVE
        assert self.classify("Create a Python script") == QC.CREATIVE
        assert self.classify("Make me a sandwich") == QC.CREATIVE
        assert self.classify("Compose a song about rain") == QC.CREATIVE
        assert self.classify("Tell me a story about a dragon") == QC.CREATIVE

    def test_tool(self):
        assert self.classify("Search for the latest Mars news") == QC.TOOL
        assert self.classify("Research quantum computing") == QC.TOOL
        assert self.classify("Find the nearest coffee shop") == QC.TOOL
        assert self.classify("Look up Python documentation") == QC.TOOL
        assert self.classify("Web search for prices") == QC.TOOL
        assert self.classify("Deep dive into Rust async") == QC.TOOL

    def test_word_count_fallback(self):
        """Short queries without matching prefixes → SIMPLE, long → COMPLEX."""
        assert self.classify("This is a short query") == QC.SIMPLE
        assert self.classify("This is a much longer query that should be complex because of the word count threshold") == QC.COMPLEX

    def test_empty_input(self):
        assert self.classify("") == QC.TRIVIAL

    def test_case_insensitive(self):
        assert self.classify("SEARCH FOR NEWS") == QC.TOOL
        assert self.classify("Why Is The Sky Blue") == QC.COMPLEX


class TestLLMRouter:

    def setup_method(self):
        self.router = router.LLMRouter()

    def test_select_backends_trivial_has_fast(self):
        """Trivial → fast first, then main."""
        backends = self.router.select_backends(
            "hi", ("fast_c", "phi4"), ("main_c", "sonnet"), has_fast=True
        )
        assert len(backends) == 2
        assert backends[0][2] == "fast"

    def test_select_backends_complex_has_fast(self):
        """Complex → main first, then fast."""
        backends = self.router.select_backends(
            "Explain gravity", ("fast_c", "phi4"), ("main_c", "sonnet"), has_fast=True
        )
        assert len(backends) == 2
        assert backends[0][2] == "main"

    def test_select_backends_no_fast(self):
        """No fast backend → main only."""
        backends = self.router.select_backends(
            "hi", ("fast_c", "phi4"), ("main_c", "sonnet"), has_fast=False
        )
        assert len(backends) == 1
        assert backends[0][2] == "main"

    def test_select_backends_tool_no_fast(self):
        """Tool without fast → main only."""
        backends = self.router.select_backends(
            "Search for news", ("fast_c", "phi4"), ("main_c", "sonnet"), has_fast=False
        )
        assert len(backends) == 1
        assert backends[0][2] == "main"

    def test_simple_fast_first(self):
        """SIMPLE → fast first."""
        backends = self.router.select_backends(
            "What is the capital of France?",
            ("fast_c", "phi4"), ("main_c", "sonnet"), has_fast=True
        )
        assert backends[0][2] == "fast"

    def test_first_backend_label_matches_client(self):
        backends = self.router.select_backends(
            "hi", ("fast_c", "phi4"), ("main_c", "sonnet"), has_fast=True
        )
        assert backends[0][0] == "fast_c"
        assert backends[1][0] == "main_c"
