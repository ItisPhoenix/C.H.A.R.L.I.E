"""
Tool Call Parser — Extract tool calls from LLM responses.

Handles:
- JSON extraction from raw LLM text (markdown blocks, balanced braces, brute-force repair)
- Regex fallback for non-JSON tool formats (call:tool(args), tool: arg, tool_name(args))
- Streaming delta parsing (OpenAI SSE format)
- Final-answer sanitization for TTS output
- Tool-name validation against a registry
- Phantom phrase detection
- Neural loop detection

Extracted from chain_executor.py.
"""

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Parsed result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ParsedToolCall:
    """Result of parsing an LLM response for tool calls."""
    action: str  # Tool name, or "none" for final answer
    action_input: Dict[str, Any]  # Tool arguments
    final_answer: str  # User-facing response text
    thought: str = ""  # LLM's reasoning (if present)


# ---------------------------------------------------------------------------
# Phantom phrase detection (standalone helper)
# ---------------------------------------------------------------------------

PHANTOM_PHRASES = [
    "i am processing",
    "i'm processing",
    "processing the",
    "i am researching",
    "i'm researching",
    "researching the",
    "i am fetching",
    "i'm fetching",
    "fetching the",
    "i am looking",
    "i'm looking",
    "looking that up",
    "data incoming",
    "stand by for",
    "retrieving the",
    "i am gathering",
    "one moment",
    "processing now",
    "live data now",
    "compiling the",
]


def is_phantom_phrase(text: str) -> bool:
    """Check if text contains phantom processing phrases."""
    if not text:
        return False
    lower = text.lower()
    return any(p in lower for p in PHANTOM_PHRASES)


# ---------------------------------------------------------------------------
# Neural loop detection (standalone helper)
# ---------------------------------------------------------------------------

def detect_neural_loop(
    action: str,
    action_input: Dict[str, Any],
    past_steps: list,
    similarity_threshold: float = 0.9,
) -> bool:
    """
    Detect if the LLM is looping — repeating the same tool call.

    Checks:
    - Exact match: same tool name AND same args
    - Similarity match: same tool name AND args similarity > threshold

    Returns True if a loop is detected.
    """
    if not action or action == "none":
        return False

    for past_step in past_steps:
        past_action = getattr(past_step, "tool_name", "")
        past_args = getattr(past_step, "args", {})

        # Exact match
        if action == past_action and action_input == past_args:
            return True

        # Similarity match (same tool, slightly different args)
        if action == past_action:
            ratio = SequenceMatcher(
                None,
                json.dumps(action_input, sort_keys=True),
                json.dumps(past_args, sort_keys=True),
            ).ratio()
            if ratio > similarity_threshold:
                return True

    return False


# ---------------------------------------------------------------------------
# Tool-name aliases
# ---------------------------------------------------------------------------

_TOOL_ALIASES: Dict[str, str] = {
    "search_web": "browser_search",
    "web_search": "browser_search",
    "google_search": "browser_search",
    "openurl": "open_website",
    "open_url": "open_website",
    "close_window": "close_app",
    "quit_app": "close_app",
}


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

class ToolCallParser:
    """
    Parses tool calls from LLM responses in various formats.

    Handles malformed JSON, markdown code blocks, regex fallbacks,
    streaming deltas, and sanitizes final-answer text for TTS.
    """

    # ------------------------------------------------------------------ #
    #  Public API (spec-required methods)
    # ------------------------------------------------------------------ #

    def parse_response(self, raw: str) -> Dict[str, Any]:
        """
        Parse a full LLM response into a structured tool-call dict.

        Returns::

            {
                "action": str,          # tool name or "none"
                "action_input": dict,   # arguments for the tool
                "final_answer": str,    # spoken answer (when action is "none")
                "thought": str,         # internal reasoning (if detected)
            }
        """
        parsed = self.parse(raw)
        return {
            "action": parsed.action,
            "action_input": parsed.action_input,
            "final_answer": parsed.final_answer,
            "thought": parsed.thought,
        }

    def parse_streaming_delta(self, delta: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Parse a single OpenAI-format streaming delta chunk.

        Parameters
        ----------
        delta : dict
            The ``choices[0]["delta"]`` object from an SSE chunk.
            Expected keys: ``content``, ``reasoning_content``,
            ``reasoning``, ``thought``.

        Returns
        -------
        dict or None
            A dict like ``{"type": "content", "text": "..."}`` or
            ``{"type": "reasoning", "text": "..."}``, or *None* if the
            delta carries no displayable content.
        """
        if not delta or not isinstance(delta, dict):
            return None

        content = delta.get("content")
        if content:
            return {"type": "content", "text": content}

        reasoning = (
            delta.get("reasoning_content")
            or delta.get("reasoning", "")
            or delta.get("thought", "")
        )
        if reasoning:
            return {"type": "reasoning", "text": reasoning}

        return None

    def sanitize_final_answer(self, text: str, goal: str = "") -> str:
        """
        Clean up final-answer text for TTS output.

        Strips AI apologies, phantom processing phrases, model tags,
        JSON action leaks, thought markers, and ensures the result is
        a speakable sentence.

        This is the extracted and refactored version of
        ``ChainExecutor._sanitize_final_answer``.
        """
        if not text:
            return ""

        # 0. ECHO CANCELLATION
        if goal and text.strip().lower() == goal.strip().lower():
            logger.warning("echo_detected | stripping_exact_goal_mirror")
            return ""

        if goal:
            echo_prefixes = [
                f"you asked about {goal}",
                f"you asked if {goal}",
                f"sir, you asked about {goal}",
                f"regarding {goal}",
                f"in response to your query about {goal}",
            ]
            lower_text = text.lower()
            for prefix in echo_prefixes:
                if lower_text.startswith(prefix):
                    text = text[len(prefix):].lstrip(",. ")
                    break

        # 0b. STRIP MODEL TAGS AND HALLUCINATED TURNS
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"(?i)\b(user|assistant|system|sir):\s*", "", text)
        text = text.replace("<endofturn>", "").replace("<startofturn>", "")

        # 0c. STRIP JSON ACTION FORMAT (LLM output leaking into speech)
        # Handle full JSON object: {"action":"...","action_input":{},"final_answer":"..."}
        json_action_pattern = (
            r'\{\s*"action"\s*:\s*"[^"]*"\s*,'
            r'\s*"action_input"\s*:\s*.*?\}\s*,'
            r'\s*"final_answer"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
        )
        json_match = re.search(json_action_pattern, text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        # Handle JSON prefix (full object at start of text)
        text = re.sub(
            r'^\s*\{\s*"action"\s*:\s*"[^"]*"\s*,'
            r'\s*"action_input"\s*:\s*.*?\}\s*,'
            r'\s*"final_answer"\s*:\s*"',
            "",
            text,
            flags=re.DOTALL,
        )
        text = re.sub(r'"\s*\}\s*$', "", text)
        # Handle JSON suffix (object appended after text content)
        text = re.sub(
            r',?\s*"?\s*\{\s*"action"\s*:\s*"[^"]*"\s*,'
            r'\s*"action_input"\s*:\s*.*?\}\s*,'
            r'\s*"final_answer"\s*:\s*"?[^\n\r"]*"?\s*\}?\s*$',
            "",
            text,
            flags=re.DOTALL,
        )
        # Strip orphaned JSON key-value pairs that LLM embeds mid-text
        text = re.sub(
            r',?\s*"action"\s*:\s*"[^"]*"\s*,\s*"action_input"\s*:\s*\{[^}]*\}\s*,?\s*',
            "",
            text,
        )
        text = re.sub(
            r',\s*"final_answer"\s*:\s*"?[^\n\r"]*"?\s*\}?\s*$',
            "",
            text,
            flags=re.DOTALL,
        )
        # Strip remaining JSON fragments: {"action":"none"} or partial objects
        text = re.sub(r'\{\s*"action"\s*:\s*"[^"]*"\s*\}', "", text)
        text = re.sub(r'\{\s*"action"\s*:\s*"[^"]*",\s*"action_input"\s*:\s*\{[^}]*\}\s*\}', "", text)
        # Strip JSON-like wrappers: {"final_answer": "..."}
        text = re.sub(r'^\s*\{\s*"final_answer"\s*:\s*"', "", text)
        text = re.sub(r'"\s*\}\s*$', "", text)
        # Strip markdown code blocks that wrap JSON
        text = re.sub(r'```(?:json)?\s*\{[^}]*\}\s*```', "", text, flags=re.DOTALL)

        original_text = text

        # 1. Strip phantom "processing" / "researching" promises
        phantom_patterns = [
            r"(?i)\bstand\s+by\s+for\b[^.!?]*[.!?]?",
            r"(?i)\bdata\s+incoming\b",
            r"(?i)\bone\s+moment[,.]?\s*",
            r"(?i)\blooking\s+that\s+up\b",
            r"(?i)\b(just\s+a\s+second|one\s+sec|hold\s+on|please\s+wait)\b[^.!?]*[.!?]?",
        ]
        sanitized = text
        for p in phantom_patterns:
            sanitized = re.sub(p, "", sanitized)

        # Selective stripping for phantom phrases in sentences
        sentences = re.split(r"(?<=[.!?])\s+", sanitized)
        filtered_sentences = []
        for s in sentences:
            if (
                is_phantom_phrase(s)
                and len(s.split()) < 12
                and not any(c.isdigit() for c in s)
            ):
                logger.info(f"phantom_sentence_suppressed | {s[:30]}...")
                continue
            filtered_sentences.append(s)
        sanitized = " ".join(filtered_sentences)

        # 2. Strip common AI apologies and filler
        patterns = [
            r"(?i)\b(i'm sorry|i apologize|apologies|unfortunately|my\s+bad)\b[,.]?\s*",
            r"(?i)\b(as an AI assistant|as an AI|as\s+an\s+intelligence|my\s+apologies)\b[,.]?\s*",
            r"(?i)\b(certainly|surely|of course|happy\s+to\s+help|glad\s+to\s+help|pleased\s+to\s+assist)\b[,.]?\s*",
            r"(?i)\b(is\s+there\s+anything\s+else|how\s+can\s+i\s+assist|what\s+else\s+can\s+i\s+do\s+for\s+you|how\s+can\s+i\s+be\s+of\s+help)\b.*",
            r"(?i)\b(please\s+let\s+me\s+know|feel\s+free\s+to\s+ask|don't\s+hesitate\s+to\s+ask)\b.*",
            r"(?i)\b(i\s+hope\s+this\s+helps|this\s+should\s+help|let\s+me\s+know\s+if\s+you\s+need\s+more)\b.*",
            r"(?i)\b(i\s+have\s+updated|i\s+have\s+performed|i\s+have\s+executed|i\s+have\s+successfully)\b.*",
            r"(?i)\b(task\s+complete|objective\s+reached|process\s+finished)\b.*",
        ]
        for p in patterns:
            sanitized = re.sub(p, "", sanitized)

        # 3. Aggressive filtering of internal thought leakage
        thought_patterns = [
            r"(?i)^(Thought|Observation|Action|Final\s+Answer|Reasoning|Logic|Internal|Process|Step\s+\d+):\s*",
            r"(?i)\b(I\s+think|I\s+believe|In\s+my\s+analysis|Based\s+on\s+the\s+data|According\s+to\s+my\s+calculations)\b[^.!?]*[.!?]?",
            r"(?i)\b(It\s+seems\s+like\s+the\s+user\s+is|The\s+user\s+is\s+trying\s+to|I\s+will\s+try\s+to\s+find\s+a\s+match)\b[^.!?]*[.!?]?",
            r"(?i)\b(The\s+result\s+shows|The\s+data\s+indicates|From\s+the\s+output|As\s+seen\s+in\s+the\s+response)\b[^.!?]*[.!?]?",
            r"(?i)\b(I\s+need\s+to|I\s+should|Let\s+me\s+first|First\s+I\s+will|Next\s+I\s+will)\b[^.!?]*[.!?]?",
            r"(?i)\b(This\s+suggests|This\s+implies|This\s+means|Therefore\s+|Thus\s+|Hence\s+)\b[^.!?]*[.!?]?",
            r"\{[^}]*\}(?=\s*$|\s*[.!?])",
            r"\[[^\]]*\](?=\s*$|\s*[.!?])",
        ]
        for p in thought_patterns:
            sanitized = re.sub(p, "", sanitized)

        # 4. Prevent inappropriate tool promises
        tool_promise_patterns = [
            r"(?i)\b(I\s+will|I\s+shall|I\s+am\s+going\s+to|Let\s+me\s+)\s+(call|use|execute|run|invoke|access|query|fetch|get|retrieve)\s+\w+",
            r"(?i)\b(Next\s+time|In\s+the\s+future|If\s+you\s+need\s+more\s+details|Should\s+I\s+)\s+(call|use|execute|run|invoke)\s+\w+",
        ]
        for p in tool_promise_patterns:
            if re.search(p, sanitized):
                filtered = [
                    sentence
                    for sentence in re.split(r"(?<=[.!?])\s+", sanitized)
                    if not re.search(p, sentence, re.IGNORECASE)
                ]
                sanitized = " ".join(filtered)
                break

        # 5. Response validation
        sanitized = re.sub(r"\s+", " ", sanitized).strip()

        if len(sanitized) < 3:
            meaningful_original = re.sub(r"[.!?\s]+", " ", original_text).strip()
            if len(meaningful_original) > 5:
                sanitized = original_text
            else:
                sanitized = "Understood, Sir."
        elif not re.search(r"[.!?]$", sanitized):
            sanitized = sanitized + "."

        # 6. Final Polish
        if sanitized:
            sanitized = (
                sanitized[0].upper() + sanitized[1:]
                if len(sanitized) > 1
                else sanitized.upper()
            )
            sanitized = re.sub(r"(?i)\bsir\b", "Sir", sanitized)

        return sanitized

    def validate_tool_name(self, name: str, registry: Dict[str, Any]) -> str:
        """
        Validate that *name* exists in *registry*.

        Returns the corrected name if a close match is found, otherwise
        ``"none"``.
        """
        if not name or not isinstance(name, str):
            return "none"

        cleaned = name.lower().strip().replace("()", "")

        if cleaned in registry:
            return cleaned

        if cleaned in _TOOL_ALIASES and _TOOL_ALIASES[cleaned] in registry:
            return _TOOL_ALIASES[cleaned]

        return "none"

    # ------------------------------------------------------------------ #
    #  Legacy API (backward compatible with existing callers)
    # ------------------------------------------------------------------ #

    def parse(self, raw: str, sanitize_fn: Optional[Any] = None) -> ParsedToolCall:
        """
        Parse raw LLM response text into a structured tool call.

        Args:
            raw: Raw LLM response text
            sanitize_fn: Optional function to clean final_answer text.
                         If None, uses ``self.sanitize_final_answer``.

        Returns:
            ParsedToolCall with action, action_input, final_answer, thought
        """
        try:
            # 0. Guard against None / non-string input
            if not raw or not isinstance(raw, str):
                return ParsedToolCall(
                    action="none",
                    action_input={},
                    final_answer="Neural link unstable, Sir.",
                    thought="",
                )

            # 1. Try JSON extraction
            res = self._extract_json(raw)

            # 2. Regex fallback for non-JSON formats
            if not res:
                res = self._regex_fallback(raw, sanitize_fn)

            # 3. Last resort — treat as final answer
            if not res or not isinstance(res, dict):
                clean_raw = self._clean_last_resort(raw, sanitize_fn)
                res = {"action": "none", "action_input": {}, "final_answer": clean_raw}

            # 4. Extract structured result
            thought = res.get("thought", "")
            action = str(res.get("action", "none")).lower().replace("()", "").strip()
            action_input = res.get("action_input", {})
            final_answer = res.get("final_answer", "")
        except Exception as exc:
            logger.error(f"parse_crash | last_resort_fallback | {exc}")
            return ParsedToolCall(
                action="none",
                action_input={},
                final_answer="Neural link unstable, Sir.",
                thought="",
            )

        if thought:
            logger.info("brain_thought", content=thought)

        return ParsedToolCall(
            action=action,
            action_input=action_input if isinstance(action_input, dict) else {},
            final_answer=final_answer,
            thought=thought,
        )

    # ------------------------------------------------------------------ #
    #  Private helpers — JSON extraction
    # ------------------------------------------------------------------ #

    def _extract_json(self, raw: str) -> Optional[Dict[str, Any]]:
        """Try to extract valid JSON from the response."""
        res = None

        # Markdown code blocks first
        match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        if match:
            try:
                res = json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug(f"json_extract_md_failed | {e}")

        if not res:
            # Balanced brace matching
            blocks = re.finditer(
                r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", raw, re.DOTALL
            )
            for block in blocks:
                content = block.group(0)
                try:
                    res = json.loads(content)
                    if res and isinstance(res, dict) and (
                        "action" in res or "final_answer" in res
                    ):
                        break
                except (json.JSONDecodeError, ValueError):
                    # Brute force repair
                    try:
                        fixed = re.sub(r",\s*\}", "}", content)
                        open_b = fixed.count("{")
                        close_b = fixed.count("}")
                        if open_b > close_b:
                            fixed += "}" * (open_b - close_b)
                        res = json.loads(fixed)
                        if res and isinstance(res, dict) and (
                            "action" in res or "final_answer" in res
                        ):
                            break
                    except (json.JSONDecodeError, ValueError):
                        continue

        if not res:
            # Hail Mary: first { to last }
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                try:
                    res = json.loads(raw[start : end + 1])
                except (json.JSONDecodeError, ValueError):
                    logger.debug("json_hail_mary_failed")

        return res

    def _regex_fallback(
        self, raw: str, sanitize_fn: Optional[Any] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Regex fallback for non-JSON tool formats.

        Handles: call:tool(args), tool_call: tool(arg), tool_name: arg, tool_name(args)
        """
        call_match = re.search(
            r"(?:call|tool_call|action):\s*(\w+)", raw, re.IGNORECASE
        )
        if not call_match:
            call_match = re.search(
                r"^(\w+):\s*([\w\s\.]+)", raw.strip(), re.IGNORECASE
            )
        if not call_match:
            call_match = re.search(r"(\w+)\s*\(", raw)

        if not call_match:
            return None

        tool_name = call_match.group(1).lower().strip()
        if tool_name in ("null", "observation"):
            tool_name = "none"

        # Extract args
        args: Dict[str, Any] = {}
        if len(call_match.groups()) > 1 and call_match.group(2):
            val = call_match.group(2).strip()
            if tool_name in ("open_app", "close_app", "open_website", "search"):
                arg_key = (
                    "name"
                    if "app" in tool_name
                    else ("url" if "website" in tool_name else "query")
                )
                args[arg_key] = val

        # Try to extract JSON from the rest
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match and not args:
            try:
                args = json.loads(json_match.group(0))
            except (json.JSONDecodeError, ValueError):
                kv_pairs = re.findall(
                    r"['\"](\w+)['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw
                )
                for k, v in kv_pairs:
                    try:
                        args[k] = json.loads(v) if v.startswith(("[", "{")) else v
                    except (json.JSONDecodeError, ValueError):
                        args[k] = v

        if tool_name == "none":
            if sanitize_fn:
                final_answer = sanitize_fn(raw)
            else:
                final_answer = self.sanitize_final_answer(raw)
            return {
                "thought": "",
                "action": "none",
                "action_input": {},
                "final_answer": final_answer,
            }

        logger.info(f"heuristic_recovery_active | tool={tool_name}")
        return {
            "thought": "",
            "action": tool_name,
            "action_input": args,
            "final_answer": "",
        }

    def _clean_last_resort(
        self, raw: str, sanitize_fn: Optional[Any] = None
    ) -> str:
        """Last resort cleanup — treat raw text as final answer."""
        clean_raw = re.sub(
            r"^(Thought|Reasoning|Internal|Process):\s*",
            "",
            raw,
            flags=re.IGNORECASE,
        )
        clean_raw = re.split(
            r"(?:Action|Tool Call|Step \d):", clean_raw, flags=re.IGNORECASE
        )[0].strip()

        # If it looks like raw JSON without the right keys, suppress
        if clean_raw.startswith("{") and clean_raw.endswith("}"):
            try:
                tmp = json.loads(clean_raw)
                if not any(k in tmp for k in ("final_answer", "thought")):
                    clean_raw = ""
            except (json.JSONDecodeError, ValueError):
                pass

        sanitizer = sanitize_fn or self.sanitize_final_answer
        return sanitizer(clean_raw if clean_raw else raw)
