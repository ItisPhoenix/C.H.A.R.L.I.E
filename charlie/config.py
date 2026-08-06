import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dotenv import load_dotenv

load_dotenv(override=True)

# Restart tiers for editable-field metadata (see FieldMeta below):
#   None      -- read fresh on every use; applying an update is instant, no reload.
#   "voice"   -- baked into VoiceEngine/ASR-worker construction; needs a voice-engine respawn.
#   "mcp"     -- needs the MCP client subprocess stopped/restarted.
#   "plugins" -- needs plugin tools re-registered.
#   "process" -- read once at process boot (e.g. into a DB connection or OS hotkey hook);
#                only takes effect on a full app restart.


def _meta(
    env: str,
    group: str,
    secret: bool = False,
    restart: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the dataclasses.field metadata for one user-editable .env-backed setting.

    This is the single place that declares which Config fields are exposed to the
    settings UI (charlie/web_server.py:/api/config) and how each one applies --
    editable_field_specs()/apply_env_updates() below read it back out. Fields with
    no _meta() (system_root, charlie_launch_id, soul, ...) are OS-derived or
    file-loaded, not user-editable .env values, and are simply skipped.
    """
    return {"env": env, "group": group, "secret": secret, "restart": restart}


@dataclass
class Config:
    llm_url: str = field(
        default=os.getenv("LLM_URL", ""),
        metadata=_meta("LLM_URL", "LLM", restart="reload"),
    )
    llm_key: str = field(
        default=os.getenv("LLM_API_KEY", "no-key"),
        metadata=_meta("LLM_API_KEY", "LLM", secret=True, restart="reload"),
    )
    llm_model: str = field(
        default=os.getenv("LLM_MODEL", ""),
        metadata=_meta("LLM_MODEL", "LLM", restart="reload"),
    )

    # -1 = system default input device; >=0 = specific device index
    mic_index: int = field(
        default=int(os.getenv("MIC_INDEX", "-1")),
        metadata=_meta("MIC_INDEX", "Voice & Speech", restart="voice"),
    )

    # Speech / ASR / TTS
    whisper_model: str = field(
        default=os.getenv("WHISPER_MODEL", "large-v3"),
        metadata=_meta("WHISPER_MODEL", "Voice & Speech", restart="voice"),
    )
    phrase_min_duration: float = field(
        default=float(os.getenv("PHRASE_MIN_DURATION", "0.35")),
        metadata=_meta("PHRASE_MIN_DURATION", "VAD & ASR Tuning", restart="voice"),
    )
    phrase_max_duration: float = field(
        default=float(os.getenv("PHRASE_MAX_DURATION", "45.0")),
        metadata=_meta("PHRASE_MAX_DURATION", "VAD & ASR Tuning", restart="voice"),
    )
    kokoro_voice: str = field(
        default=os.getenv("KOKORO_VOICE", "af_heart"),
        metadata=_meta("KOKORO_VOICE", "Voice & Speech", restart="voice"),
    )
    kokoro_model_dir: str = field(
        default=os.getenv("KOKORO_MODEL_DIR", "models"),
        metadata=_meta("KOKORO_MODEL_DIR", "Voice & Speech", restart="voice"),
    )
    gpu_device: str = field(
        default=os.getenv("GPU_DEVICE", "cuda"),
        metadata=_meta("GPU_DEVICE", "Voice & Speech", restart="voice"),
    )
    kokoro_lang: str = field(
        default=os.getenv("KOKORO_LANG", "en-us"),
        metadata=_meta("KOKORO_LANG", "Voice & Speech", restart="voice"),
    )
    default_language: str = field(
        default=os.getenv("DEFAULT_LANGUAGE", "en"),
        metadata=_meta("DEFAULT_LANGUAGE", "Voice & Speech", restart="voice"),
    )

    # Runtime-tunable env override read by onnxruntime at import time.
    # onnxruntime reads ORT_LOG_LEVEL from the process environment, so we
    # propagate the configured value here (the single sanctioned env-write
    # site) before any module imports onnxruntime.
    ort_log_level: str = field(
        default=os.getenv("ORT_LOG_LEVEL", "3"),
        metadata=_meta("ORT_LOG_LEVEL", "Server", restart="process"),
    )

    # VAD / ASR tuning -- all baked into the ASR worker subprocess at spawn time.
    vad_threshold: float = field(
        default=float(os.getenv("VAD_THRESHOLD", "0.25")),
        metadata=_meta("VAD_THRESHOLD", "VAD & ASR Tuning", restart="voice"),
    )
    # Silero VAD speech-probability cutoff -- distinct scale from VAD_THRESHOLD's raw mic amplitude.
    asr_vad_threshold: float = field(
        default=float(os.getenv("ASR_VAD_THRESHOLD", "0.5")),
        metadata=_meta("ASR_VAD_THRESHOLD", "VAD & ASR Tuning", restart="voice"),
    )
    vad_silence_timeout: float = field(
        default=float(os.getenv("VAD_SILENCE_TIMEOUT", "1.5")),
        metadata=_meta("VAD_SILENCE_TIMEOUT", "VAD & ASR Tuning", restart="voice"),
    )
    vad_min_speech_duration_ms: int = field(
        default=int(os.getenv("VAD_MIN_SPEECH_DURATION_MS", "120")),
        metadata=_meta("VAD_MIN_SPEECH_DURATION_MS", "VAD & ASR Tuning", restart="voice"),
    )
    vad_max_speech_duration_s: int = field(
        default=int(os.getenv("VAD_MAX_SPEECH_DURATION_S", "60")),
        metadata=_meta("VAD_MAX_SPEECH_DURATION_S", "VAD & ASR Tuning", restart="voice"),
    )
    vad_min_silence_duration_ms: int = field(
        default=int(os.getenv("VAD_MIN_SILENCE_DURATION_MS", "1000")),
        metadata=_meta("VAD_MIN_SILENCE_DURATION_MS", "VAD & ASR Tuning", restart="voice"),
    )
    vad_speech_pad_ms: int = field(
        default=int(os.getenv("VAD_SPEECH_PAD_MS", "320")),
        metadata=_meta("VAD_SPEECH_PAD_MS", "VAD & ASR Tuning", restart="voice"),
    )
    asr_beam_size: int = field(
        default=int(os.getenv("ASR_BEAM_SIZE", "1")),
        metadata=_meta("ASR_BEAM_SIZE", "VAD & ASR Tuning", restart="voice"),
    )
    asr_best_of: int = field(
        default=int(os.getenv("ASR_BEST_OF", "1")),
        metadata=_meta("ASR_BEST_OF", "VAD & ASR Tuning", restart="voice"),
    )
    asr_repetition_penalty: float = field(
        default=float(os.getenv("ASR_REPETITION_PENALTY", "1.15")),
        metadata=_meta("ASR_REPETITION_PENALTY", "VAD & ASR Tuning", restart="voice"),
    )

    # Barge-in Configuration
    enable_barge_in: bool = field(
        default=os.getenv("ENABLE_BARGE_IN", "true").lower() == "true",
        metadata=_meta("ENABLE_BARGE_IN", "Chat Behavior", restart="reload"),
    )

    llm_disable_reasoning: bool = field(
        default=os.getenv("LLM_DISABLE_REASONING", "true").lower() == "true",
        metadata=_meta("LLM_DISABLE_REASONING", "Chat Behavior", restart="reload"),
    )
    # Enable native JSON tool calling for compatible remote APIs (OpenAI, Anthropic).
    # When False, falls back to text-based TOOL: parsing for local models.
    native_tool_calling: bool = field(
        default=os.getenv("NATIVE_TOOL_CALLING", "true").lower() == "true",
        metadata=_meta("NATIVE_TOOL_CALLING", "Chat Behavior", restart="reload"),
    )

    # Iteration Budget & Context Compression
    iteration_budget_max: int = field(
        default=int(os.getenv("ITERATION_BUDGET_MAX", "50")),
        metadata=_meta("ITERATION_BUDGET_MAX", "Chat Behavior", restart="reload"),
    )
    context_window: int = field(
        default=int(os.getenv("CONTEXT_WINDOW", "32000")),
        metadata=_meta("CONTEXT_WINDOW", "Chat Behavior", restart="reload"),
    )
    compression_soft_threshold: float = field(
        default=float(os.getenv("COMPRESSION_SOFT_THRESHOLD", "0.5")),
        metadata=_meta("COMPRESSION_SOFT_THRESHOLD", "Chat Behavior", restart="reload"),
    )
    compression_threshold: float = field(
        default=float(os.getenv("COMPRESSION_THRESHOLD", "0.85")),
        metadata=_meta("COMPRESSION_THRESHOLD", "Chat Behavior", restart="reload"),
    )
    history_keep_recent: int = field(
        default=int(os.getenv("HISTORY_KEEP_RECENT", "6")),
        metadata=_meta("HISTORY_KEEP_RECENT", "Chat Behavior", restart="reload"),
    )
    history_summary_max_chars: int = field(
        default=int(os.getenv("HISTORY_SUMMARY_MAX_CHARS", "400")),
        metadata=_meta("HISTORY_SUMMARY_MAX_CHARS", "Chat Behavior", restart="reload"),
    )
    memory_file: str = field(
        default=os.getenv("MEMORY_FILE", "MEMORY.md"),
        metadata=_meta("MEMORY_FILE", "Memory Files", restart="reload"),
    )
    user_file: str = field(
        default=os.getenv("USER_FILE", "USER.md"),
        metadata=_meta("USER_FILE", "Memory Files", restart="reload"),
    )
    opinions_file: str = field(
        default=os.getenv("OPINIONS_FILE", "OPINIONS.md"),
        metadata=_meta("OPINIONS_FILE", "Memory Files", restart="reload"),
    )
    project_file: str = field(
        default=os.getenv("PROJECT_FILE", "PROJECT.md"),
        metadata=_meta("PROJECT_FILE", "Memory Files", restart="reload"),
    )
    prompt_memory_max: int = field(
        default=int(os.getenv("PROMPT_MEMORY_MAX", "2200")),
        metadata=_meta("PROMPT_MEMORY_MAX", "Memory Files", restart="reload"),
    )
    session_db_path: str = field(
        default=os.getenv("SESSION_DB_PATH", "sessions.db"),
        metadata=_meta("SESSION_DB_PATH", "Server", restart="process"),
    )
    extensions_state_path: str = field(
        default=os.getenv("EXTENSIONS_STATE_PATH", "extensions_state.json"),
        metadata=_meta("EXTENSIONS_STATE_PATH", "Server", restart="process"),
    )
    # Search provider (SearXNG self-hosted)
    searxng_url: str = field(
        default=os.getenv("SEARXNG_URL", ""),
        metadata=_meta("SEARXNG_URL", "Search Providers", restart="reload"),
    )
    exa_api_key: str = field(
        default=os.getenv("EXA_API_KEY", ""),
        metadata=_meta("EXA_API_KEY", "Search Providers", secret=True, restart="reload"),
    )
    tavily_api_key: str = field(
        default=os.getenv("TAVILY_API_KEY", ""),
        metadata=_meta("TAVILY_API_KEY", "Search Providers", secret=True, restart="reload"),
    )

    # Wake Word Configuration -- classifier is loaded once when VoiceEngine starts.
    wake_word_enabled: bool = field(
        default=os.getenv("WAKE_WORD_ENABLED", "false").lower() == "true",
        metadata=_meta("WAKE_WORD_ENABLED", "Wake Word", restart="voice"),
    )
    wake_word_model_path: str = field(
        default=os.getenv("WAKE_WORD_MODEL_PATH", "charlie/charlie.onnx"),
        metadata=_meta("WAKE_WORD_MODEL_PATH", "Wake Word", restart="voice"),
    )
    wake_word_threshold: float = field(
        default=float(os.getenv("WAKE_WORD_THRESHOLD", "0.6")),
        metadata=_meta("WAKE_WORD_THRESHOLD", "Wake Word", restart="voice"),
    )
    wake_word_activity_timeout_seconds: int = field(
        default=int(os.getenv("WAKE_WORD_ACTIVITY_TIMEOUT", "600")),
        metadata=_meta("WAKE_WORD_ACTIVITY_TIMEOUT", "Wake Word", restart="voice"),
    )
    wake_word_audio_chime_path: str = field(
        default=os.getenv("WAKE_WORD_CHIME_PATH", "assets/wake_word_chime.wav"),
        metadata=_meta("WAKE_WORD_CHIME_PATH", "Wake Word", restart="voice"),
    )
    # --- Vector Memory Configuration ---
    memory_db_path: str = field(
        default=os.getenv("MEMORY_DB_PATH", "charlie_memory_db"),
        metadata=_meta("MEMORY_DB_PATH", "Vector Memory", restart="process"),
    )
    memory_relevance_threshold: float = field(
        default=float(os.getenv("MEMORY_RELEVANCE_THRESHOLD", "0.3")),
        metadata=_meta("MEMORY_RELEVANCE_THRESHOLD", "Vector Memory"),
    )
    memory_embedding_model: str = field(
        default=os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5"),
        metadata=_meta("MEMORY_EMBEDDING_MODEL", "Vector Memory", restart="process"),
    )
    memory_embedding_url: str = field(
        default=os.getenv("MEMORY_EMBEDDING_URL", ""),
        metadata=_meta("MEMORY_EMBEDDING_URL", "Vector Memory", restart="process"),
    )
    memory_auto_extract: bool = field(
        default=os.getenv("MEMORY_AUTO_EXTRACT", "true").lower() == "true",
        metadata=_meta("MEMORY_AUTO_EXTRACT", "Vector Memory", restart="reload"),
    )
    # Memory capacity management
    memory_nudge_interval: int = field(
        default=int(os.getenv("MEMORY_NUDGE_INTERVAL", "5")),
        metadata=_meta("MEMORY_NUDGE_INTERVAL", "Vector Memory", restart="reload"),
    )
    memory_capacity_threshold: float = field(
        default=float(os.getenv("MEMORY_CAPACITY_THRESHOLD", "0.8")),
        metadata=_meta("MEMORY_CAPACITY_THRESHOLD", "Vector Memory", restart="reload"),
    )
    # --- Agentic OS Toggles ---
    mcp_enabled: bool = field(
        default=os.getenv("MCP_ENABLED", "false").lower() == "true",
        metadata=_meta("MCP_ENABLED", "Agentic OS", restart="mcp"),
    )
    # Comma-separated MCP server specs; each is "name|command|arg1,arg2,...".
    # Empty means no servers are started even when mcp_enabled is true.
    mcp_servers: List[str] = field(
        default_factory=lambda: [
            s.strip()
            for s in os.getenv("MCP_SERVERS", "").split(",")
            if s.strip()
        ],
        metadata=_meta("MCP_SERVERS", "Agentic OS", restart="mcp"),
    )
    # Optional standard "mcpServers" JSON config file (Claude Desktop / Cursor /
    # VS Code format) -- an alternative to MCP_SERVERS for hand-editing servers.
    # Both sources are merged; missing file is not an error.
    mcp_config_path: str = field(
        default=os.getenv("MCP_CONFIG_PATH", "mcp_config.json"),
        metadata=_meta("MCP_CONFIG_PATH", "Agentic OS", restart="mcp"),
    )
    # --- Desktop control (UI Automation) ---
    desktop_control_enabled: bool = field(
        default=os.getenv("DESKTOP_CONTROL_ENABLED", "false").lower() == "true",
        metadata=_meta("DESKTOP_CONTROL_ENABLED", "Desktop Control", restart="reload"),
    )
    # Independent of desktop_control_enabled -- lets the dashboard live-frame feed be
    # turned off (privacy) without disabling desktop control tools themselves.
    desktop_frame_capture_enabled: bool = field(
        default=os.getenv("DESKTOP_FRAME_CAPTURE_ENABLED", "true").lower() == "true",
        metadata=_meta("DESKTOP_FRAME_CAPTURE_ENABLED", "Desktop Control", restart="reload"),
    )
    # Off by default (2026-08-07) -- the tool-call-count trigger drafted a skill out
    # of routine multi-search research tasks, not just genuinely reusable procedures.
    auto_skill_gen_enabled: bool = field(
        default=os.getenv("AUTO_SKILL_GEN_ENABLED", "false").lower() == "true",
        metadata=_meta("AUTO_SKILL_GEN_ENABLED", "Agentic OS", restart="reload"),
    )
    # Read once into a pynput GlobalHotKeys listener at Brain construction -- needs a full restart to re-arm.
    desktop_panic_hotkey: str = field(
        default=os.getenv("DESKTOP_PANIC_HOTKEY", "ctrl+alt+q"),
        metadata=_meta("DESKTOP_PANIC_HOTKEY", "Desktop Control", restart="process"),
    )
    desktop_max_actions: int = field(
        default=int(os.getenv("DESKTOP_MAX_ACTIONS", "40")),
        metadata=_meta("DESKTOP_MAX_ACTIONS", "Desktop Control", restart="reload"),
    )
    # Seconds of no real user input required before Charlie may start/continue
    # an unattended background desktop task (charlie.desktop.session.user_idle_seconds).
    desktop_idle_threshold_s: float = field(
        default=float(os.getenv("DESKTOP_IDLE_THRESHOLD_S", "120.0")),
        metadata=_meta("DESKTOP_IDLE_THRESHOLD_S", "Desktop Control", restart="reload"),
    )
    desktop_ocr_enabled: bool = field(
        default=os.getenv("DESKTOP_OCR_ENABLED", "true").lower() == "true",
        metadata=_meta("DESKTOP_OCR_ENABLED", "Desktop Control", restart="reload"),
    )
    # Per-turn caps for background tasks -- see charlie.background_task's dataclasses.replace(config, ...).
    background_iteration_budget_max: int = field(
        default=int(os.getenv("BACKGROUND_ITERATION_BUDGET_MAX", "40")),
        metadata=_meta("BACKGROUND_ITERATION_BUDGET_MAX", "Desktop Control", restart="reload"),
    )
    background_max_actions: int = field(
        default=int(os.getenv("BACKGROUND_MAX_ACTIONS", "100")),
        metadata=_meta("BACKGROUND_MAX_ACTIONS", "Desktop Control", restart="reload"),
    )
    tesseract_cmd: str = field(
        default=os.getenv("TESSERACT_CMD", ""),
        metadata=_meta("TESSERACT_CMD", "Desktop Control", restart="reload"),
    )
    # Separate, independently-configured vision endpoint -- small/big LLMs stay text-only.
    vision_enabled: bool = field(
        default=os.getenv("VISION_ENABLED", "false").lower() == "true",
        metadata=_meta("VISION_ENABLED", "Vision", restart="reload"),
    )
    vision_llm_url: str = field(
        default=os.getenv("VISION_LLM_URL", ""),
        metadata=_meta("VISION_LLM_URL", "Vision", restart="reload"),
    )
    vision_llm_key: str = field(
        default=os.getenv("VISION_LLM_KEY", "no-key"),
        metadata=_meta("VISION_LLM_KEY", "Vision", secret=True, restart="reload"),
    )
    vision_llm_model: str = field(
        default=os.getenv("VISION_LLM_MODEL", ""),
        metadata=_meta("VISION_LLM_MODEL", "Vision", restart="reload"),
    )
    vision_llm_timeout_s: float = field(
        default=float(os.getenv("VISION_LLM_TIMEOUT_S", "120.0")),
        metadata=_meta("VISION_LLM_TIMEOUT_S", "Vision", restart="reload"),
    )
    plugins_enabled: bool = field(
        default=os.getenv("PLUGINS_ENABLED", "false").lower() == "true",
        metadata=_meta("PLUGINS_ENABLED", "Plugins", restart="plugins"),
    )
    # Restrict plugin filesystem access to these directories (comma-separated).
    # Empty means the plugins default to the current working directory only.
    plugin_allow_dirs: List[str] = field(
        default_factory=lambda: [
            d.strip()
            for d in os.getenv("PLUGIN_ALLOW_DIRS", "").split(",")
            if d.strip()
        ],
        metadata=_meta("PLUGIN_ALLOW_DIRS", "Plugins", restart="plugins"),
    )
    # --- Proactive resource monitoring ---
    # CPU/RAM percent thresholds for proactive alerts (sustained 3 samples before alerting)
    alert_cpu_pct: float = field(
        default=float(os.getenv("ALERT_CPU_PCT", "95")),
        metadata=_meta("ALERT_CPU_PCT", "Monitoring", restart="reload"),
    )
    alert_ram_pct: float = field(
        default=float(os.getenv("ALERT_RAM_PCT", "92")),
        metadata=_meta("ALERT_RAM_PCT", "Monitoring", restart="reload"),
    )

    charlie_host: str = field(
        default=os.getenv("CHARLIE_HOST", "127.0.0.1"),
        metadata=_meta("CHARLIE_HOST", "Server", restart="process"),
    )
    charlie_port: int = field(
        default=int(os.getenv("CHARLIE_PORT", "8000")),
        metadata=_meta("CHARLIE_PORT", "Server", restart="process"),
    )
    # Not user-editable .env values -- process identity / OS-derived / file-loaded, so no _meta().
    charlie_launch_id: str = os.getenv("CHARLIE_LAUNCH_ID", "")
    system_root: str = os.getenv("SystemRoot", r"C:\Windows").lower()
    program_files: str = os.getenv("ProgramFiles", r"C:\Program Files")
    program_files_x86: str = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")

    soul: str = ""

    def __post_init__(self) -> None:
        if self.kokoro_lang == "en":
            self.kokoro_lang = "en-us"

    @classmethod
    def editable_field_specs(cls) -> List[Dict[str, Any]]:
        """Describe every .env-backed field for the settings UI (charlie/web_server.py).

        Derived entirely from the metadata declared above -- adding a new Config
        field with a _meta(...) call is enough for it to show up in /api/config
        and the settings page with no other file needing to know its name.
        """
        specs = []
        for f in fields(cls):
            env = f.metadata.get("env")
            if not env:
                continue
            if f.type is bool:
                kind = "bool"
            elif f.type is int:
                kind = "int"
            elif f.type is float:
                kind = "float"
            elif f.type == List[str]:
                kind = "list"
            else:
                kind = "str"
            specs.append(
                {
                    "key": env,
                    "field": f.name,
                    "group": f.metadata.get("group", "Other"),
                    "secret": bool(f.metadata.get("secret", False)),
                    "restart": f.metadata.get("restart"),
                    "type": kind,
                    "label": _humanize_label(f.name),
                }
            )
        return specs

    def apply_env_updates(self, updates: Dict[str, Any]) -> Set[str]:
        """Apply {ENV_VAR_NAME: value} to this instance's live attributes.

        `value` may already be natively typed (bool/int/float/list, e.g. straight
        from a JSON request body) or a raw string (e.g. reloaded from os.environ).
        Returns the set of restart tiers touched (see module docstring above) so
        the caller knows which subsystem, if any, needs reloading.
        """
        by_env = {f.metadata.get("env"): f for f in fields(self) if f.metadata.get("env")}
        touched: Set[str] = set()
        for env_key, raw_value in updates.items():
            f = by_env.get(env_key)
            if f is None:
                continue
            setattr(self, f.name, _coerce(raw_value, f.type))
            restart = f.metadata.get("restart")
            if restart:
                touched.add(restart)
        return touched


_LABEL_ACRONYMS = {
    "llm", "url", "asr", "tts", "vad", "mcp", "gpu", "ocr", "id",
}


def _humanize_label(field_name: str) -> str:
    words = [w.upper() if w in _LABEL_ACRONYMS else w.capitalize() for w in field_name.split("_")]
    return " ".join(words)


def _coerce(raw: Any, ftype: Any) -> Any:
    if ftype is bool:
        return raw if isinstance(raw, bool) else str(raw).strip().lower() == "true"
    if ftype is int:
        return int(raw)
    if ftype is float:
        return float(raw)
    if ftype == List[str]:
        if isinstance(raw, list):
            return [str(v).strip() for v in raw if str(v).strip()]
        return [s.strip() for s in str(raw).split(",") if s.strip()]
    return str(raw)


config = Config()

# onnxruntime reads ORT_LOG_LEVEL from the env at import time -- the one sanctioned env-write site.
os.environ.setdefault("ORT_LOG_LEVEL", config.ort_log_level)

# Load SOUL.md into config.soul at startup
_SOUL_PATH = Path("SOUL.md")
_DEFAULT_SOUL = (
    "You are Charlie. You are warm but efficient. You get to the point fast, then offer warmth."
    " No fluff. No sycophancy. You speak like a trusted colleague who actually cares."
    " Never guess at facts you don't have -- say what you don't know and check before answering."
)
if not _SOUL_PATH.exists():
    _SOUL_PATH.write_text(_DEFAULT_SOUL, encoding="utf-8")
config.soul = _SOUL_PATH.read_text(encoding="utf-8")
