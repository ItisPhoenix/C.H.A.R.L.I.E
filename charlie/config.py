from typing import Dict, List
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv(override=True)

@dataclass
class Config:
    llm_url: str = os.getenv("LLM_URL", "https://integrate.api.nvidia.com/v1")
    llm_key: str = os.getenv("LLM_API_KEY", "no-key")
    llm_model: str = os.getenv("LLM_MODEL", "meta/llama3-70b-instruct")
    fast_llm_url: str = os.getenv("FAST_LLM_URL", os.getenv("LLM_URL", "https://integrate.api.nvidia.com/v1"))
    fast_llm_key: str = os.getenv("FAST_LLM_KEY", os.getenv("LLM_API_KEY", "no-key"))
    fast_llm_model: str = os.getenv("FAST_LLM_MODEL", os.getenv("LLM_MODEL", "meta/llama3-70b-instruct"))
    
    # -1 = system default input/output device; ≥0 = specific device index
    mic_index: int = int(os.getenv("MIC_INDEX", "-1"))
    output_index: int = int(os.getenv("OUTPUT_INDEX", "-1"))
    
    whisper_model: str = os.getenv("WHISPER_MODEL", "distil-large-v3")
    silence_timeout: float = float(os.getenv("SILENCE_TIMEOUT", "0.6"))
    phrase_min_duration: float = float(os.getenv("PHRASE_MIN_DURATION", "0.4"))
    phrase_max_duration: float = float(os.getenv("PHRASE_MAX_DURATION", "30.0"))
    kokoro_voice: str = os.getenv("KOKORO_VOICE", "af_heart")
    kokoro_model_dir: str = os.getenv("KOKORO_MODEL_DIR", "models")
    gpu_device: str = os.getenv("GPU_DEVICE", "cuda")
    history_file: str = os.getenv("HISTORY_FILE", "charlie_history.json")
    max_history: int = int(os.getenv("MAX_HISTORY", "12"))
    searxng_url: str = os.getenv("SEARXNG_URL", "")  # e.g. "http://localhost:8080"
    
    # Wake Word Config
    enable_wake_word: bool = os.getenv("ENABLE_WAKE_WORD", "false").lower() == "true"
    wake_word_model_path: str = os.getenv("WAKE_WORD_MODEL", "charlie/charlie.onnx")
    wake_word_sensitivity: float = float(os.getenv("WAKE_WORD_SENSITIVITY", "0.5"))
    smart_mode_timeout: float = float(os.getenv("SMART_MODE_TIMEOUT", "15.0"))
    
    # Hybrid LLM Router Config
    local_llm_url: str = os.getenv("LOCAL_LLM_URL", "http://localhost:11434/v1")
    local_llm_model: str = os.getenv("LOCAL_LLM_MODEL", "llama3.1:8b")
    local_llm_key: str = os.getenv("LOCAL_LLM_KEY", "no-key")
    enable_local_llm: bool = os.getenv("ENABLE_LOCAL_LLM", "true").lower() == "true"
    hybrid_routing_type: str = os.getenv("HYBRID_ROUTING", "keyword")
    
    # MCP Client Config
    mcp_config_path: str = os.getenv("MCP_CONFIG_PATH", "mcp_config.json")
    
    research_memory_db: str = os.getenv("RESEARCH_MEMORY_DB", "research_memory.db")
    
    memory_db_path: str = os.getenv("CHARLIE_MEMORY_DB", "charlie_memory.db")
    memory_auto_extract: bool = os.getenv("CHARLIE_MEMORY_AUTO_EXTRACT", "true").lower() == "true"
    memory_max_core_facts: int = int(os.getenv("CHARLIE_MEMORY_MAX_CORE", "20"))
    memory_max_recall: int = int(os.getenv("CHARLIE_MEMORY_MAX_RECALL", "5"))
    memory_extract_threshold_words: int = int(os.getenv("CHARLIE_MEMORY_EXTRACT_WORDS", "50"))
    memory_consolidate_after: int = int(os.getenv("CHARLIE_MEMORY_CONSOLIDATE_AFTER", "10"))
    
    soul_path: str = os.getenv("CHARLIE_SOUL_PATH", "SOUL.md")
    user_path: str = os.getenv("CHARLIE_USER_PATH", "USER.md")
    data_dir: str = os.getenv("DATA_DIR", "charlie/data")
    
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "en")
    kokoro_lang: str = "en-us"
    log_file: str = "logs/charlie.log"
    log_level: str = "INFO"
    
    emotion_response_map: Dict[str, List[str]] = None

    def __post_init__(self):
        self.emotion_response_map = {
            "energetic": ["normal", "detailed"],
            "frustrated": ["concise", "normal"],
            "sad": ["calm", "normal"],
            "calm": ["detailed", "normal"],
            "neutral": ["normal", "detailed", "concise"],
        }

config = Config()
