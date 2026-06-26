import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass
class Config:
    llm_url: str = os.getenv("LLM_URL", "")
    llm_key: str = os.getenv("LLM_API_KEY", "no-key")
    llm_model: str = os.getenv("LLM_MODEL", "")
    # Fallback LLM provider (used when primary LLM fails)
    fallback_llm_url: str = os.getenv("FALLBACK_LLM_URL", "")
    fallback_llm_key: str = os.getenv("FALLBACK_LLM_API_KEY", "no-key")
    fallback_llm_model: str = os.getenv("FALLBACK_LLM_MODEL", "")

    # -1 = system default input/output device; >=0 = specific device index
    mic_index: int = int(os.getenv("MIC_INDEX", "-1"))
    output_index: int = int(os.getenv("OUTPUT_INDEX", "-1"))

    # Speech / ASR / TTS
    whisper_model: str = os.getenv("WHISPER_MODEL", "large-v3")
    silence_timeout: float = float(os.getenv("SILENCE_TIMEOUT", "0.6"))
    phrase_min_duration: float = float(os.getenv("PHRASE_MIN_DURATION", "0.35"))
    phrase_max_duration: float = float(os.getenv("PHRASE_MAX_DURATION", "45.0"))
    kokoro_voice: str = os.getenv("KOKORO_VOICE", "af_heart")
    kokoro_model_dir: str = os.getenv("KOKORO_MODEL_DIR", "models")
    gpu_device: str = os.getenv("GPU_DEVICE", "cuda")
    kokoro_lang: str = "en-us"
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "en")
    log_file: str = "logs/charlie.log"
    log_level: str = "INFO"

    # VAD / ASR tuning
    vad_threshold: float = float(os.getenv("VAD_THRESHOLD", "0.25"))
    vad_silence_timeout: float = float(os.getenv("VAD_SILENCE_TIMEOUT", "0.6"))
    vad_min_speech_duration_ms: int = int(os.getenv("VAD_MIN_SPEECH_DURATION_MS", "120"))
    vad_max_speech_duration_s: int = int(os.getenv("VAD_MAX_SPEECH_DURATION_S", "60"))
    vad_min_silence_duration_ms: int = int(os.getenv("VAD_MIN_SILENCE_DURATION_MS", "480"))
    vad_speech_pad_ms: int = int(os.getenv("VAD_SPEECH_PAD_MS", "320"))
    asr_beam_size: int = int(os.getenv("ASR_BEAM_SIZE", "6"))
    asr_best_of: int = int(os.getenv("ASR_BEST_OF", "6"))
    asr_repetition_penalty: float = float(os.getenv("ASR_REPETITION_PENALTY", "1.15"))

    # Barge-in Configuration
    enable_barge_in: bool = os.getenv("ENABLE_BARGE_IN", "true").lower() == "true"

    llm_disable_reasoning: bool = (
        os.getenv("LLM_DISABLE_REASONING", "true").lower() == "true"
    )
    # Enable native JSON tool calling for compatible remote APIs (OpenAI, Anthropic).
    # When False, falls back to text-based TOOL: parsing for local models.
    native_tool_calling: bool = (
        os.getenv("NATIVE_TOOL_CALLING", "true").lower() == "true"
    )

    # Iteration Budget & Context Compression
    iteration_budget_max: int = int(os.getenv("ITERATION_BUDGET_MAX", "12"))
    context_window: int = int(os.getenv("CONTEXT_WINDOW", "8192"))
    compression_threshold: float = float(os.getenv("COMPRESSION_THRESHOLD", "0.8"))
    memory_file: str = os.getenv("MEMORY_FILE", "MEMORY.md")
    user_file: str = os.getenv("USER_FILE", "USER.md")
    opinions_file: str = os.getenv("OPINIONS_FILE", "OPINIONS.md")
    prompt_memory_max: int = int(os.getenv("PROMPT_MEMORY_MAX", "2200"))
    session_db_path: str = os.getenv("SESSION_DB_PATH", "sessions.db")
    # Search provider (SearXNG self-hosted)
    searxng_url: str = os.getenv("SEARXNG_URL", "")
    exa_api_key: str = os.getenv("EXA_API_KEY", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    # Wake Word Configuration
    wake_word_enabled: bool = os.getenv("WAKE_WORD_ENABLED", "false").lower() == "true"
    wake_word_model_path: str = os.getenv("WAKE_WORD_MODEL_PATH", "charlie/charlie.onnx")
    wake_word_threshold: float = float(os.getenv("WAKE_WORD_THRESHOLD", "0.6"))
    wake_word_activity_timeout_seconds: int = int(os.getenv("WAKE_WORD_ACTIVITY_TIMEOUT", "600"))
    wake_word_audio_chime_path: str = os.getenv("WAKE_WORD_CHIME_PATH", "assets/wake_word_chime.wav")
    # --- Vector Memory Configuration ---
    memory_db_path: str = os.getenv("MEMORY_DB_PATH", "charlie_memory_db")
    memory_relevance_threshold: float = float(os.getenv("MEMORY_RELEVANCE_THRESHOLD", "0.3"))
    memory_embedding_model: str = os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5")
    memory_embedding_url: str = os.getenv("MEMORY_EMBEDDING_URL", "http://127.0.0.1:1234/v1")
    memory_auto_extract: bool = os.getenv("MEMORY_AUTO_EXTRACT", "true").lower() == "true"
    # Memory capacity management
    memory_nudge_interval: int = int(os.getenv("MEMORY_NUDGE_INTERVAL", "5"))
    memory_capacity_threshold: float = float(os.getenv("MEMORY_CAPACITY_THRESHOLD", "0.8"))

    soul: str = ""


config = Config()

# Load SOUL.md into config.soul at startup
_SOUL_PATH = Path("SOUL.md")
_DEFAULT_SOUL = (
    "You are Charlie. You are warm but efficient. You get to the point fast, then offer warmth."
    " No fluff. No sycophancy. You speak like a trusted colleague who actually cares."
)
if not _SOUL_PATH.exists():
    _SOUL_PATH.write_text(_DEFAULT_SOUL, encoding="utf-8")
config.soul = _SOUL_PATH.read_text(encoding="utf-8")
