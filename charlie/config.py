from typing import Dict, List
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv(override=True)

@dataclass
class Config:
    llm_url: str = os.getenv("LLM_URL", "")
    llm_key: str = os.getenv("LLM_API_KEY", "no-key")
    llm_model: str = os.getenv("LLM_MODEL", "")
    fast_llm_url: str = os.getenv("FAST_LLM_URL", "")
    fast_llm_key: str = os.getenv("FAST_LLM_KEY", "no-key")
    fast_llm_model: str = os.getenv("FAST_LLM_MODEL", "")
    
    # -1 = system default input/output device; ≥0 = specific device index
    mic_index: int = int(os.getenv("MIC_INDEX", "-1"))
    output_index: int = int(os.getenv("OUTPUT_INDEX", "-1"))
    
    whisper_model: str = os.getenv("WHISPER_MODEL", "distil-large-v3")
    silence_timeout: float = float(os.getenv("SILENCE_TIMEOUT", "1.0"))
    phrase_min_duration: float = float(os.getenv("PHRASE_MIN_DURATION", "0.8"))
    phrase_max_duration: float = float(os.getenv("PHRASE_MAX_DURATION", "30.0"))
    kokoro_voice: str = os.getenv("KOKORO_VOICE", "af_heart")
    kokoro_model_dir: str = os.getenv("KOKORO_MODEL_DIR", "models")
    gpu_device: str = os.getenv("GPU_DEVICE", "cuda")
    history_file: str = os.getenv("HISTORY_FILE", "charlie_history.json")
    max_history: int = int(os.getenv("MAX_HISTORY", "12"))
    searxng_url: str = os.getenv("SEARXNG_URL", "")  # e.g. "http://localhost:8080"
    
    # Hybrid LLM Router Config
    
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
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "384"))
    enable_semantic_memory: bool = os.getenv("ENABLE_SEMANTIC_MEMORY", "true").lower() == "true"
    # VAD Configuration
    vad_threshold: float = float(os.getenv("VAD_THRESHOLD", "0.75"))
    vad_silence_timeout: float = float(os.getenv("VAD_SILENCE_TIMEOUT", "1.2"))
    
    # Barge-in Configuration
    enable_barge_in: bool = os.getenv("ENABLE_BARGE_IN", "true").lower() == "true"

    # Buddy UI Configuration
    enable_buddy_ui: bool = os.getenv("ENABLE_BUDDY_UI", "true").lower() == "true"
    buddy_port: int = int(os.getenv("BUDDY_PORT", "8765"))

    
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
