import ctypes

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


def get_idle_time() -> float:
    """Returns system idle time in seconds on Windows."""
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return millis / 1000.0
    except Exception as e:
        logger.debug(f"get_idle_time_failed | error={e}")
    return 0.0


def is_system_active(threshold: int = 300) -> bool:
    """Returns True if system had input within the threshold."""
    return get_idle_time() < threshold


_NVML_INIT = False
_NVML_HANDLE = None

def _ensure_nvml():
    global _NVML_INIT, _NVML_HANDLE
    if not _NVML_INIT:
        try:
            import pynvml
            pynvml.nvmlInit()
            _NVML_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(0)
            _NVML_INIT = True
        except Exception as e:
            logger.debug(f"nvml_init_failed | {e}")
            return False
    return True

def get_vram_usage() -> float:
    """Retrieves VRAM usage percentage from NVIDIA GPUs if available."""
    if not _ensure_nvml():
        return 0.0
    try:
        import pynvml
        info = pynvml.nvmlDeviceGetMemoryInfo(_NVML_HANDLE)
        return min(100.0, (info.used / info.total) * 100)
    except Exception as e:
        logger.debug(f"nvml_vram_check_failed | {e}")
    return 0.0


def get_vram_used_mb() -> float:
    """Retrieves VRAM usage in MB."""
    if not _ensure_nvml():
        return 0.0
    try:
        import pynvml
        info = pynvml.nvmlDeviceGetMemoryInfo(_NVML_HANDLE)
        return info.used / (1024 * 1024)
    except Exception as e:
        logger.debug(f"nvml_vram_mb_check_failed | {e}")
    return 0.0


def get_visible_window_titles(limit: int = 10) -> list[str]:
    """Returns titles of visible, non-empty windows."""
    try:
        import pygetwindow as gw
        return [w.title for w in gw.getAllWindows() if w.visible and w.title][:limit]
    except Exception:
        return []


def get_vram_percent() -> float:
    """Returns VRAM usage as a percentage of the configured limit."""
    from charlie.config import settings
    used = get_vram_used_mb()
    limit = getattr(settings.llm, "vram_limit_mb", 8192)
    return min(100.0, (used / limit) * 100) if limit > 0 else 0.0


def get_system_vitals() -> dict:
    """Returns CPU%, RAM%, VRAM_MB, VRAM%, VRAM_LIMIT_MB in a single call."""
    import psutil

    from charlie.config import settings
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    vram_mb = get_vram_used_mb()
    vram_limit = getattr(settings.llm, "vram_limit_mb", 8192)
    vram_pct = min(100.0, (vram_mb / vram_limit) * 100) if vram_limit > 0 else 0.0
    return {"cpu": cpu, "ram": ram, "vram_mb": vram_mb, "vram_pct": vram_pct, "vram_limit": vram_limit}
