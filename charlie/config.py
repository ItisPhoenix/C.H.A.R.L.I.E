from typing import Dict, List
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    llm_url: str = os.getenv("LLM_URL", "https://integrate.api.nvidia.com/v1")
    llm_key: str = os.getenv("LLM_API_KEY", "no-key")
    llm_model: str = os.getenv("LLM_MODEL", "meta/llama3-70b-instruct")
    fast_llm_url: str = os.getenv("FAST_LLM_URL", os.getenv("LLM_URL", "https://integrate.api.nvidia.com/v1"))
    fast_llm_key: str = os.getenv("FAST_LLM_KEY", os.getenv("LLM_API_KEY", "no-key"))
    fast_llm_model: str = os.getenv("FAST_LLM_MODEL", os.getenv("LLM_MODEL", "meta/llama3-70b-instruct"))
    
    mic_index: int = int(os.getenv("MIC_INDEX", "0"))
    output_index: int = int(os.getenv("OUTPUT_INDEX", "0"))
    
    whisper_model: str = os.getenv("WHISPER_MODEL", "large-v3")
    silence_timeout: float = float(os.getenv("SILENCE_TIMEOUT", "1.5"))
    phrase_min_duration: float = float(os.getenv("PHRASE_MIN_DURATION", "1.0"))
    phrase_max_duration: float = float(os.getenv("PHRASE_MAX_DURATION", "30.0"))
    kokoro_voice: str = os.getenv("KOKORO_VOICE", "af_heart")
    kokoro_model_dir: str = os.getenv("KOKORO_MODEL_DIR", "models")
    gpu_device: str = os.getenv("GPU_DEVICE", "cuda")
    history_file: str = os.getenv("HISTORY_FILE", "charlie_history.json")
    max_history: int = int(os.getenv("MAX_HISTORY", "12"))
    searxng_url: str = os.getenv("SEARXNG_URL", "")  # e.g. "http://localhost:8080"
    research_memory_db: str = os.getenv("RESEARCH_MEMORY_DB", "research_memory.db")
    
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
