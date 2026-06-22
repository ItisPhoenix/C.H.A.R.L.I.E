import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv(override=True)

@dataclass
class Config:
    llm_url: str = os.getenv("LLM_URL", "")
    llm_key: str = os.getenv("LLM_API_KEY", "no-key")
    llm_model: str = os.getenv("LLM_MODEL", "")
    
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
    kokoro_lang: str = "en-us"
    default_language: str = os.getenv("DEFAULT_LANGUAGE", "en")
    log_file: str = "logs/charlie.log"
    log_level: str = "INFO"
    
    # VAD Configuration
    vad_threshold: float = float(os.getenv("VAD_THRESHOLD", "0.75"))
    vad_silence_timeout: float = float(os.getenv("VAD_SILENCE_TIMEOUT", "1.2"))
    
    # Barge-in Configuration
    enable_barge_in: bool = os.getenv("ENABLE_BARGE_IN", "true").lower() == "true"
    
    llm_disable_reasoning: bool = os.getenv("LLM_DISABLE_REASONING", "true").lower() == "true"

config = Config()
