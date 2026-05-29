"""
C.H.A.R.L.I.E. — Relationship & Trust Ledger
Tracks trust scores and calibrates personality based on user feedback.
"""

import concurrent.futures
import json
import os
import time
from datetime import datetime
from typing import Dict

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

TRUST_LEDGER_PATH = "charlie/personality/trust_ledger.jsonl"
RELATIONSHIP_PATH = "charlie/personality/relationship.json"
class RelationshipManager:
    def __init__(self):
        os.makedirs(os.path.dirname(RELATIONSHIP_PATH), exist_ok=True)
        self.trust_score = 50.0  # Default neutral-start
        self.trust_level = "Cooperative"
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.status_q = None # Injected by Brain
        self._load_relationship()

    def _load_relationship(self):
        if os.path.exists(RELATIONSHIP_PATH):
            try:
                with open(RELATIONSHIP_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.trust_score = data.get("trust_score", 50.0)
                    self._update_level()
            except Exception as e:
                logger.error(f"relationship_load_failed | {e}")

    def _save_relationship(self):
        """Saves relationship data to disk in background thread."""
        data = {"trust_score": self.trust_score, "last_updated": time.time()}
        def _bg_save():
            try:
                with open(RELATIONSHIP_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.error(f"relationship_save_failed | {e}")

        self._executor.submit(_bg_save)

    def _update_level(self):
        s = self.trust_score
        if s <= 30: self.trust_level = "Cautious"
        elif s <= 60: self.trust_level = "Cooperative"
        elif s <= 85: self.trust_level = "Familiar"
        else: self.trust_level = "Autonomous"

    def log_event(self, event_type: str, reason: str, delta: float = 0.0):
        """Logs a trust event to the ledger and updates score (Async I/O)."""
        # Default deltas if not provided
        if delta == 0.0:
            deltas = {
                "tool_success": 1.0,
                "user_confirmation": 0.5,
                "user_correction": -2.0,
                "action_aborted": -0.5,
                "action_confirmed": 0.5
            }
            delta = deltas.get(event_type, 0.0)

        self.trust_score = max(0.0, min(100.0, self.trust_score + delta))
        self._update_level()
        self._save_relationship()

        # Signal status_q
        if self.status_q:
            self.status_q.put({
                "type": "RELATIONSHIP_UPDATE",
                "content": {
                    "score": self.trust_score,
                    "level": self.trust_level,
                    "event": event_type,
                    "hint": self.get_density_hint()
                }
            })

        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "delta": delta,
            "new_score": round(self.trust_score, 2),
            "reason": reason
        }

        def _bg_log():
            try:
                with open(TRUST_LEDGER_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception as e:
                logger.error(f"trust_ledger_append_failed | {e}")

        self._executor.submit(_bg_log)

    def get_trust_context(self) -> str:
        """Returns context for system prompt injection."""
        return (
            f"Current Trust Score: {self.trust_score:.1f}/100\n"
            f"Relationship Level : {self.trust_level}\n"
            f"Tone Calibration   : {self._get_tone_directive()}"
        )

    def _get_tone_directive(self) -> str:
        if self.trust_level == "Cautious":
            return "Be formal, verify all actions, avoid humor."
        if self.trust_level == "Cooperative":
            return "Professional, standard verification, helpful."
        if self.trust_level == "Familiar":
            return "Warm, proactive suggestions, dry wit permitted."
        if self.trust_level == "Autonomous":
            return "Full partnership, minimal verification for TIER 1, intuitive."
        return "Standard."

    def get_density_hint(self) -> Dict[str, float]:
        """Returns visual scaling/opacity hints based on trust."""
        if self.trust_level == "Cautious":
            return {"opacity": 1.0, "scale": 1.0, "telemetry_detail": 1.0}
        if self.trust_level == "Cooperative":
            return {"opacity": 0.85, "scale": 1.0, "telemetry_detail": 0.8}
        if self.trust_level == "Familiar":
            return {"opacity": 0.7, "scale": 0.95, "telemetry_detail": 0.5}
        if self.trust_level == "Autonomous":
            return {"opacity": 0.5, "scale": 0.9, "telemetry_detail": 0.2}
        return {"opacity": 0.8, "scale": 1.0, "telemetry_detail": 0.7}
