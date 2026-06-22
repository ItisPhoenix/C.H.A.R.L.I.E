"""Tests for strip_internal_reasoning helper.
    
Reads core.py source and exec's just the regex + helper to avoid triggering 
broken charlie package imports."""
import os
import re

# Read core.py and extract only _REASONING_RE and strip_internal_reasoning
CORE_PATH = os.path.join(os.path.dirname(__file__), "..", "charlie", "core.py")
with open(CORE_PATH, encoding="utf-8") as f:
    source = f.read()

# We need to build a minimal execution context with `re` available
# Extract _REASONING_RE assignment and strip_internal_reasoning function
# by finding their line ranges in source
lines = source.splitlines()

# Find _REASONING_RE block (multi-line re.compile)
re_start = next(i for i, l in enumerate(lines) if "_REASONING_RE" in l and "re.compile" in l)
# Find the closing paren of the re.compile call and the function def
re_end = re_start
paren_depth = 0
for i in range(re_start, len(lines)):
    paren_depth += lines[i].count("(") - lines[i].count(")")
    if paren_depth <= 0 and i > re_start:
        re_end = i + 1
        break

# Find strip_internal_reasoning function
fn_start = next(i for i in range(re_end, len(lines)) if "def strip_internal_reasoning" in lines[i])
fn_end = fn_start
# Find next def or class at same indent level (no indent)
for i in range(fn_start + 1, len(lines)):
    if lines[i] and not lines[i].startswith((" ", "\t", ")")) and lines[i].startswith(("def ", "class ", "@")):
        fn_end = i
        break
else:
    fn_end = len(lines)

exec_code = "\n".join(lines[re_start:fn_end])
exec_globals = {"re": re}
exec(exec_code, exec_globals)
strip_internal_reasoning = exec_globals["strip_internal_reasoning"]


class TestStripInternalReasoning:

    def test_chinese_thinking_tags(self):
        text = "   \u601d\u8003 Let me check   \u601d\u8003\u7ed3\u675f The answer is Paris."
        result = strip_internal_reasoning(text)
        assert "\u601d\u8003" not in result
        assert "\u7ed3\u675f" not in result
        assert "Paris" in result

    def test_thinking_tag(self):
        text = "<thinking>internal reasoning</thinking>The answer is 42."
        result = strip_internal_reasoning(text)
        assert "<thinking>" not in result
        assert "42" in result

    def test_thought_tag(self):
        text = "<thought>some reasoning</thought>Done."
        result = strip_internal_reasoning(text)
        assert "<thought>" not in result

    def test_untagged_reasoning(self):
        text = "Let me think step by step. The capital of France is Paris."
        result = strip_internal_reasoning(text)
        assert "Let me" not in result
        assert "Paris" in result

    def test_clean_text_untouched(self):
        text = "The capital of France is Paris."
        result = strip_internal_reasoning(text)
        assert result == "The capital of France is Paris."

    def test_mixed_tags(self):
        text = (
            "<thinking>hmm</thinking>"
            "  \u601d\u8003 thinking  \u601d\u8003\u7ed3\u675f"
            " Let me help you. The answer is 42."
        )
        result = strip_internal_reasoning(text)
        assert "<thinking>" not in result
        assert "\u601d\u8003" not in result
        assert "Let me" not in result
        assert "42" in result

    def test_multiple_untagged_blocks(self):
        text = "I need to search for an answer. Let me check my knowledge base. First, I'll recall. The sky is blue."
        result = strip_internal_reasoning(text)
        assert "sky is blue" in result
        assert "I need" not in result
        assert "Let me" not in result
        assert "First" not in result

    def test_no_false_positive_short_text(self):
        text = "Hi there!"
        result = strip_internal_reasoning(text)
        assert result == "Hi there!"

    def test_case_insensitive_tag(self):
        text = "<THINKING>secret</THINKING>Output"
        result = strip_internal_reasoning(text)
        assert "Output" in result

    def test_dotall_tag_multiline(self):
        text = "<thinking>\nmulti\nline\n</thinking>Out"
        result = strip_internal_reasoning(text)
        assert "Out" in result
