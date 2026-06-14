import os
import logging
import numpy as np
from faster_whisper import WhisperModel
import multiprocessing as mp
import time

# Set up logging for the worker process
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("charlie.asr_worker")

def asr_worker_process(input_queue: mp.Queue, output_queue: mp.Queue, model_size: str, device: str, default_language: str):
    """
    Worker process that handles Whisper transcription.
    """
    logger.info(f"ASR Worker started. Loading model: {model_size} on {device}")
    
    try:
        # Load WhisperModel once
        whisper = WhisperModel(
            model_size,
            device=device,
            compute_type="float16" if device == "cuda" else "int8",
            local_files_only=True
        )
    except Exception as e:
        logger.warning(f"ASR Worker: Local load failed, attempting download: {e}")
        whisper = WhisperModel(model_size, device=device, compute_type="float16" if device == "cuda" else "int8")

    logger.info("ASR Worker: Whisper model loaded and ready.")

    while True:
        try:
            # Poll for work
            payload = input_queue.get()
            if payload is None:  # Shutdown signal
                logger.info("ASR Worker: Shutdown signal received.")
                break
            
            audio_data_bytes, sample_rate = payload
            # Convert bytes back to numpy array
            audio_data = np.frombuffer(audio_data_bytes, dtype=np.float32)
            
            # Transcribe
            segments, info = whisper.transcribe(
                audio_data,
                language=default_language,
                initial_prompt="I am speaking to my witty and intelligent AI assistant, Charlie.",
                beam_size=5,
                word_timestamps=False
            )
            
            text = "".join([s.text for s in segments]).strip()
            confidence = info.language_probability
            
            # Return result
            output_queue.put((text, confidence))
            
        except Exception as e:
            logger.error(f"ASR Worker: Error during transcription: {e}")
            output_queue.put(("", 0.0))  # Return empty result on error

if __name__ == "__main__":
    # This file isn't meant to be run directly, but if it is, we could add test logic here
    pass
