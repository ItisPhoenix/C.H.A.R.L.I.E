import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    llm_url: str = os.getenv("LLM_URL", "https://integrate.api.nvidia.com/v1")
    llm_key: str = os.getenv("LLM_API_KEY", "no-key")
    llm_model: str = os.getenv("LLM_MODEL", "meta/llama3-70b-instruct")
    
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
    
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "en")
    kokoro_lang: str = "en-us"
    log_file: str = "logs/charlie.log"
    log_level: str = "INFO"

config = Config()
