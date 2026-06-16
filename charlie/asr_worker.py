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
        logger.warning(f"ASR Worker: Local load failed for {model_size}, attempting download: {e}")
        try:
            whisper = WhisperModel(model_size, device=device, compute_type="float16" if device == "cuda" else "int8")
        except Exception as e2:
            logger.warning(f"ASR Worker: Failed to load {model_size}: {e2}. Falling back to large-v3.")
            whisper = WhisperModel(
                "large-v3",
                device=device,
                compute_type="float16" if device == "cuda" else "int8"
            )

    logger.info("ASR Worker: Whisper model loaded and ready.")

    while True:
        try:
            # Poll with timeout so KeyboardInterrupt can fire cleanly
            try:
                payload = input_queue.get(timeout=1.0)
            except Exception:
                continue
            if payload is None:  # Shutdown signal
                break
            
            audio_data_bytes, sample_rate = payload
            audio_data = np.frombuffer(audio_data_bytes, dtype=np.float32)
            
            start_time = time.time()
            segments, info = whisper.transcribe(
                audio_data,
                language=default_language,
                initial_prompt="I am speaking to my witty and intelligent AI assistant, Charlie.",
                beam_size=5,
                best_of=5,
                vad_filter=True,
                word_timestamps=False,
                condition_on_previous_text=False,
            )
            
            text = "".join([s.text for s in segments]).strip()
            confidence = info.language_probability
            latency_ms = (time.time() - start_time) * 1000
            logger.info(f"pipeline_stage | stage=asr | latency_ms={latency_ms:.1f}")
            output_queue.put((text, confidence))
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"ASR Worker: Error during transcription: {e}")
            output_queue.put(("", 0.0))

    logger.info("ASR Worker: Shutting down.")

if __name__ == "__main__":
    # This file isn't meant to be run directly, but if it is, we could add test logic here
    pass
