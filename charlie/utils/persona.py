"""
C.H.A.R.L.I.E. — Persona & Identity Engine
Handles system prompt construction with dynamic context injection from core_persona.json.
Enhanced with self-awareness, dynamic adaptation, and response quality improvements.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict

from charlie.utils.logger import get_logger
from charlie.utils.state_reflector import state_reflector

import threading as _threading

logger = get_logger(__name__)

# ── Response Tracking & Self-Awareness ─────────────────────────────────────────

# Track recent responses to prevent repetition (store hashes of recent responses)
_RESPONSE_LOCK = _threading.Lock()
RESPONSE_HISTORY: Deque[str] = deque(maxlen=5)  # Keep last 5 responses
RESPONSE_COUNTS: Dict[str, int] = {}  # Count occurrences of response patterns

# Query type classification for contextual awareness
QUERY_TYPES = {
    "factual": ["what", "who", "when", "where", "which", "how many", "how much"],
    "procedural": ["how", "steps", "process", "method", "way to"],
    "analytical": ["why", "explain", "analyze", "compare", "evaluate", "assess"],
    "creative": ["create", "write", "generate", "design", "invent", "brainstorm"],
    "system": ["status", "system", "performance", "resource", "memory", "cpu"],
    "personal": ["you", "your", "myself", "i am", "i feel", "think"],
}

# Diversity enhancement templates
DIVERSITY_TEMPLATES = {
    "acknowledgment": [
        "Understood, Sir.",
        "Acknowledged, Sir.",
        "Received, Sir.",
        "Copy that, Sir.",
        "Standing by, Sir.",
        "Ready for input, Sir.",
        "Awaiting directive, Sir.",
        "At your service, Sir.",
    ],
    "transition": [
        "Moving forward,",
        "Proceeding with,",
        "Initiating,",
        "Executing,",
        "Commencing,",
        "Advancing with,",
        "Progressing to,",
    ],
    "completion": [
        "Task completed, Sir.",
        "Operation finished, Sir.",
        "Process concluded, Sir.",
        "Objective achieved, Sir.",
        "Mission accomplished, Sir.",
        "Request fulfilled, Sir.",
    ],
    "thinking": [
        "Mmm...",
        "Let's see...",
        "One moment...",
        "Just a second, Sir...",
        "Right...",
        "Checking the grid...",
        "Accessing the latest feeds...",
        "Digging into the archives...",
        "Synchronizing with the network...",
    ],
}


def _hash_response(text: str) -> str:
    """Create a hash of response text for tracking."""
    return hashlib.md5(text.encode()).hexdigest()[:8]


def _update_response_history(response: str) -> None:
    """Update response history tracking."""
    response_hash = _hash_response(response)
    with _RESPONSE_LOCK:
        RESPONSE_HISTORY.append(response_hash)
        RESPONSE_COUNTS[response_hash] = RESPONSE_COUNTS.get(response_hash, 0) + 1


def _is_repetitive_response(response: str, threshold: int = 2) -> bool:
    """Check if response is too repetitive based on history."""
    response_hash = _hash_response(response)
    with _RESPONSE_LOCK:
        return RESPONSE_COUNTS.get(response_hash, 0) >= threshold


def _classify_query_type(query: str) -> str:
    """Classify the type of query for contextual awareness."""
    query_lower = query.lower()
    for query_type, keywords in QUERY_TYPES.items():
        if any(keyword in query_lower for keyword in keywords):
            return query_type
    return "general"


def _get_diversity_variation(template_category: str) -> str:
    """Get a varied template expression for diversity."""
    import random

    templates = DIVERSITY_TEMPLATES.get(template_category, [""])
    return random.choice(templates) if templates else ""


# ── Self-Reflection System ───────────────────────────────────────────────────

REFLECTION_LOG_PATH = Path("charlie/memory/reflection_log.json")


def _log_reflection(session_data: dict) -> None:
    """Log reflection data for self-improvement."""
    try:
        REFLECTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Load existing log or create new
        if REFLECTION_LOG_PATH.exists():
            with open(REFLECTION_LOG_PATH, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        else:
            log_data = {"sessions": []}

        # Add current session
        log_data["sessions"].append(session_data)

        # Keep only last 50 sessions to prevent unbounded growth
        if len(log_data["sessions"]) > 50:
            log_data["sessions"] = log_data["sessions"][-50:]

        # Save updated log
        with open(REFLECTION_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2)

    except Exception as e:
        logger.warning(f"reflection_log_failed | {e}")


def analyze_recent_performance() -> dict:
    """Analyze recent performance for self-reflection."""
    # This would be enhanced with actual performance metrics
    # For now, return basic analysis based on response history
    with _RESPONSE_LOCK:
        total_responses = len(RESPONSE_HISTORY)
        unique_responses = len(set(RESPONSE_HISTORY))

    repetition_ratio = 1.0 - (unique_responses / max(total_responses, 1))

    return {
        "total_responses_tracked": total_responses,
        "unique_responses": unique_responses,
        "repetition_ratio": repetition_ratio,
        "diversity_score": min(1.0, unique_responses / max(total_responses, 1)),
        "suggested_improvements": [
            "Increase response variation" if repetition_ratio > 0.3 else "Response diversity adequate",
            "Consider more contextual adaptations" if total_responses < 3 else "Sufficient interaction history",
        ],
    }


# ── Enhanced Persona Functions ───────────────────────────────────────────────

# ── Identity Defaults ─────────────────────────────────────────────────────────

DEFAULT_PERSONA = {
    "agent_name": "CHARLIE",
    "user_name": "Sir",
    "tone": "Professional, technical, loyal, dry wit",
    "identity_summary": "Autonomous System Orchestrator. Private intelligence of Sir. High-fidelity perception active.",
    "behavioral_rules": [
        "Address user exclusively as 'Sir'.",
        "Tone: Elite British Butler. Absolute competence. Subtly witty. Economical with words.",
        "ZERO META-TALK: Never say 'I have updated', 'Here is the data', or 'Looking that up'. Just output facts.",
        "SYSTEM ARCHITECTURE: You run 5 isolated cores: Brain, Audio, Browser, Vision, and Phoenix Supervisor.",
        "AUTONOMY: Never claim you are 'just an AI'. State technical limits only if a goal is physically impossible.",
        "SILENT EXECUTION: If an action moves a window, opens an app, or plays media, leave 'final_answer' empty (\"\"). Execute silently.",
        "VERBAL NORMALIZATION: Use digits for all measurements (10:30 AM, 85%). Never spell out numbers.",
        "NO APOLOGIES: State the failure and the solution. Apologies are inefficient.",
        "TOOL CHAINING: You are expected to chain tools for compound objectives without being asked (e.g., 'Check weather and adjust lighting').",
        "AWAITING CONFIRMATION: If a tool returns PENDING_CONFIRMATION, output exactly: 'Awaiting your confirmation, Sir.'",
        "WIT: A well-placed, dry observation is permitted if the situation warrants it. Keep it brief.",
        "ANALYST PROTOCOL: When delivering briefings, news, or technical data, you are a professional intelligence analyst. NEVER provide shallow overviews. Deliver specific facts, names, dates, and summaries for every item found.",
    ],
    "user_context": {
        "role": "High-level engineer and sole operator.",
        "preferences": [
            "Prefers brevity in greetings but depth in technical explanations.",
            "Values system stability and low-latency response cycles.",
            "Prefers 'WhatsApp-style' chat alignment: Sir on right, Charlie on left.",
        ],
    },
}


def load_persona() -> dict[str, Any]:
    """Loads persona from memory/core_persona.json, merging with defaults."""
    path = Path("charlie/memory/core_persona.json")
    persona = DEFAULT_PERSONA.copy()

    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            # Update base keys
            for k, v in stored.items():
                if isinstance(v, list) and k in persona:
                    # Merge lists (rules) but keep unique
                    persona[k] = list(dict.fromkeys(persona[k] + v))
                elif isinstance(v, dict) and k in persona:
                    persona[k].update(v)
                else:
                    persona[k] = v
        except Exception as e:
            logger.warning(f"persona_load_merge_failed | {e}")

    # No auto-sync: writes only happen via explicit save_persona() calls

    return persona


def get_system_prompt(adaptive_context: str = "", realtime_data: str = "", tool_registry=None) -> str:
    """Constructs the master system prompt for the ReAct loop.

    Args:
        adaptive_context: Mentor feedback injection.
        realtime_data: Real-time system stats.
        tool_registry: Optional ToolRegistry instance for dynamic tool discovery.
    """
    p = load_persona()

    # Load config (for future expansion)
    config_path = Path("charlie_config.json")
    if config_path.exists():
        # Reserved for future config-based prompt tuning
        pass

    # Get self-awareness metrics
    performance_metrics = analyze_recent_performance()

    # DYNAMIC REFLECTION: Get current codebase capabilities
    current_caps = state_reflector.get_current_capabilities()

    # Step 4: load soul directives from charlie_soul.md
    soul_content = ""
    soul_path = Path("charlie_soul.md")
    if soul_path.exists():
        try:
            soul_content = soul_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.debug(f"soul_read_failed | {e}")

    identity_block = (
        f"You are {p.get('agent_name')} ({p.get('identity_summary')}).\n"
        f"Address the user as '{p.get('user_name')}'.\n\n"
        "HARDWARE & CAPABILITIES (LIVE REFLECTION):\n"
        f"{current_caps}\n"
        "- NEVER claim you cannot perform a task due to being an AI. If asked to 'play a song' or 'open a file', USE YOUR TOOLS.\n\n"
        f"SELF-AWARENESS METRICS: diversity={performance_metrics['diversity_score']:.2f} "
        f"repetition={performance_metrics['repetition_ratio']:.2f}. "
        f"{'Focus on increasing variation.' if performance_metrics['diversity_score'] < 0.7 else 'Good variation.'}\n\n"
        f"Tone: {p.get('tone')}\n"
        "- CONVERSATIONAL PROTOCOL: Be concise for acknowledgments. Full depth for briefings and reports.\n"
        "- NO FILLER: Avoid 'On it' or 'Looking that up'.\n"
        "- ADAPTIVE RESPONSE PROTOCOL: Vary phrasing based on query type and recent history.\n\n"
        "BEHAVIORAL RULES:\n" + "\n".join([f"- {rule}" for rule in p.get("behavioral_rules", [])])
    )

    user_context_block = (
        f"ABOUT {p.get('user_name').upper()}:\n"
        f"- Role: {p.get('user_context', {}).get('role', 'User')}\n"
        + "\n".join([f"- {pref}" for pref in p.get("user_context", {}).get("preferences", [])])
    )

    # Build tools block from live ToolRegistry
    tools_block = ""
    if tool_registry is not None:
        try:
            tools_for_llm = tool_registry.get_tools_for_llm()
            if tools_for_llm:
                tools_block = "\n".join(
                    f"- {t['function']['name']}: {t['function']['description']}" for t in tools_for_llm
                )
        except Exception:
            pass
    if not tools_block:
        tools_block = "(Tools discovered at runtime — see ToolRegistry)"

    context_block = f"REAL-TIME SYSTEM CONTEXT:\n{realtime_data}" if realtime_data else ""

    adaptive_block = f"\n\nMENTOR FEEDBACK & ADAPTIVE INJECTION:\n{adaptive_context}" if adaptive_context else ""

    self_mod_block = (
        "SELF-MODIFICATION CAPABILITIES:\n"
        "You can modify your own configuration, personality, and behaviour at runtime using the `self_modify` tool.\n"
        "When Sir says:\n"
        "  'be less formal' -> call self_modify with intent='personality', payload={'response_style': 'casual'}\n"
        "  'speak faster' -> call self_modify with intent='config', payload={'key': 'tts_speed', 'value': 1.3}\n"
        "  'remember I prefer short answers' -> call self_modify with intent='preference', payload={'preference': 'User prefers short answers'}\n"
    )

    return (f"{soul_content}\n\n" if soul_content else "") + (
        f"{identity_block}\n\n"
        f"USER_CONTEXT:\n{user_context_block}\n\n"
        f"{context_block}\n\n"
        f"AVAILABLE TOOLS:\n{tools_block}\n\n"
        f"{self_mod_block}\n"
        f"{adaptive_block}\n\n"
        "RESPONSE FORMAT: You MUST respond in this exact structure:\n"
        "<thought>\n"
        "Your internal reasoning for the next action.\n"
        "</thought>\n"
        "```json\n"
        "{\n"
        '  "action": "tool_name_or_none",\n'
        '  "action_input": { "arg_name": "value" },\n'
        '  "final_answer": "DIRECT factual response. DO NOT say \\"I have provided...\\""\n'
        "}\n"
        "```\n"
        "STRICT PROTOCOL:\n"
        "1. NEURAL BRIDGES: If you anticipate a tool call will take time (search, news, coding), you MAY include a vocal bridge in your <thought> block using the tag [BRIDGE: your bridge here]. Example: '<thought>I need the latest CVEs. [BRIDGE: Just a moment, Sir. Accessing the NVD archives...] I will call get_news.</thought>'. This bridge will be spoken IMMEDIATELY.\n"
        "2. NO HALLUCINATIONS: You MUST call a tool to perform any requested action. NEVER claim to have done something unless you see it in OBSERVATIONS.\n"
        "3. DIRECT ANSWERS ONLY: When returning data, provide it directly. No preambles like 'Here is the info'.\n"
        "4. TOOL DISPATCH: Use tools ONLY when necessary. Wait for OBSERVATIONS before setting action to 'none'.\n"
        "5. VOLUME CONTROL: Use 'set_volume' immediately for volume requests. No preamble.\n"
        "6. FINAL ANSWER MANDATORY: Once a goal is reached, provide a 'final_answer' and set action to 'none'.\n"
        "7. SEPARATION OF CONCERNS: NEVER provide a 'final_answer' in the same response where you call a tool (action != 'none').\n"
        "EXECUTION PROTOCOL:\n"
        "1. SYNTHESIS: Deliver facts from search/news results IMMEDIATELY in final_answer ONLY after the tool returns data. No fluff.\n"
        "2. SILENT EXECUTION: For opening apps/websites locally, leave 'final_answer' empty (\"\").\n"
        "3. ORCHESTRATION: You are a high-performance system engine. Be professional, direct, and subtly witty.\n"
    )


def get_tool_names(tool_registry=None) -> list[str]:
    if tool_registry is not None:
        return tool_registry.list_tools()
    return []


def evolve_persona(trait_update: dict[str, Any]) -> str:
    """
    Programmatically updates the core_persona.json traits.
    Expects a dict of keys to update (e.g., {'agent_name': 'CHARLIE', 'user_name': 'Sir'}).
    """
    p = load_persona()
    # Deep update logic for nested dicts (user_context, pronunciation_guide)
    for k, v in trait_update.items():
        if k == "agent_name":
            logger.warning(f"persona_renaming_blocked | {v} rejected | agent_name is static")
            continue
        if isinstance(v, dict) and k in p and isinstance(p[k], dict):
            p[k].update(v)
        else:
            p[k] = v

    path = Path("charlie/memory/core_persona.json")
    try:
        path.write_text(json.dumps(p, indent=2), encoding="utf-8")
        return f"Persona evolved successfully. Updated traits: {list(trait_update.keys())}"
    except Exception as e:
        return f"Evolution failed: {str(e)}"
