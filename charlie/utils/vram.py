"""GPU VRAM detection via nvidia-smi. Windows-only."""
import os
import subprocess

def detect_total_vram_mb() -> int:
    """Auto-detect total GPU VRAM via nvidia-smi. Falls back to 7168 MB."""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, startupinfo=startupinfo,
        )
        if result.returncode == 0:
            total = int(result.stdout.strip().splitlines()[0].strip())
            return total
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, IndexError):
        pass
    return 7168

def calculate_budget_mb(total_vram_mb: int) -> int:
    """Available VRAM budget after fixed costs.
    STT (faster-whisper): ~2500 MB, TTS (kokoro-onnx): ~1000 MB, headroom: 500 MB.
    """
    fixed_mb = 2500 + 1000 + 500  # STT + TTS + headroom
    budget = total_vram_mb - fixed_mb
    return max(budget, 1024)  # Minimum 1 GB
