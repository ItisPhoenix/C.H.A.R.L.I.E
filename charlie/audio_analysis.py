import numpy as np

def analyze_wave_mouth_values(samples: np.ndarray, sr: int, chunk_ms: int = 50) -> list[float]:
    """
    Analyzes audio samples and returns a list of mouth openness values [0, 1] 
    based on RMS amplitude per chunk.
    """
    if len(samples) == 0:
        return [0.0]

    chunk_size = int(sr * chunk_ms / 1000)
    if chunk_size == 0:
        return [0.0]

    # Split into chunks
    num_chunks = max(1, len(samples) // chunk_size)
    chunks = np.array_split(samples[:num_chunks * chunk_size], num_chunks)
    
    # Calculate RMS for each chunk
    rms_values = [np.sqrt(np.mean(chunk**2)) for chunk in chunks]
    
    min_rms = min(rms_values)
    max_rms = max(rms_values)
    
    if max_rms == min_rms:
        return [0.0] * len(rms_values)
    
    # Normalize to [0, 1]
    return [(rms - min_rms) / (max_rms - min_rms) for rms in rms_values]
