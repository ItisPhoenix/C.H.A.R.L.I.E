import time
import logging
from typing import Dict

logger = logging.getLogger("charlie.latency")


class PipelineTimer:
    def __init__(self):
        self._t: Dict[str, float] = {}

    def mark(self, stage: str):
        self._t[stage] = time.time()

    def log_delta(self, from_stage: str, to_stage: str, label: str) -> float:
        """Log pipeline delta in ms. Returns the delta, or 0.0 if missing."""
        start = self._t.get(from_stage)
        end = self._t.get(to_stage)
        if start and end:
            ms = (end - start) * 1000
            logger.info(f"pipeline_latency | {label}={ms:.1f}ms")
            return ms
        return 0.0

    @staticmethod
    def warn_if_exceeds(label: str, value_ms: float, threshold_ms: float):
        """Log a warning if a pipeline stage exceeds its latency budget."""
        if value_ms > threshold_ms:
            logger.warning(
                f"pipeline_latency | {label}={value_ms:.1f}ms | threshold={threshold_ms}ms | WARNING"
            )
