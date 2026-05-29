import gc
import time

import psutil
import requests

from charlie.config import settings
from charlie.utils.logger import get_logger
from charlie.utils.observability import Observability

logger = get_logger(__name__)


class Doctor:
    def __init__(self, status_q=None, tracked_pids=None):
        self.obs = Observability(tracked_pids=tracked_pids)
        self.status_q = status_q
        self.settings = settings  # Store settings reference for LLM health check
        self.last_alert_time = 0
        self.alert_cooldown = 120

        # Health state
        self.restart_history = []
        self._llm_healthy = True
        self._last_llm_check = 0

    def update_pids(self, pids):
        self.obs.update_pids(pids)

    def perform_vitals_check(self):
        """Runs a diagnostic cycle and repairs if necessary."""
        vitals = self.obs.get_vitals()
        alerts = self.obs.check_thresholds(vitals)

        # Check LLM Health every 30s
        now = time.time()
        if now - self._last_llm_check > 30:
            is_healthy = self.check_llm_health()
            if not is_healthy and self._llm_healthy:
                alerts.append("CRITICAL_LLM")
            self._llm_healthy = is_healthy
            self._last_llm_check = now

        if alerts:
            logger.warning("doctor_diagnosed_issues", alerts=alerts)
            self._remediate(alerts, vitals)
            return True
        return False

    def _remediate(self, alerts, vitals):
        """Autonomous repair logic for resource exhaustion and service failure."""
        for alert in alerts:
            if alert == "CRITICAL_LLM":
                logger.error("remediate_llm | llm_service_down")
                if self.status_q:
                    self.status_q.put(
                        {
                            "type": "ALERT",
                            "content": "Doctor: Sir, the LM Studio service is unreachable. Brain function is impaired.",
                        }
                    )

            if alert == "CRITICAL_CPU":
                offender = self._find_cpu_offender()
                logger.warning(f"remediate_cpu | top_offender={offender}")
                if self.status_q:
                    self.status_q.put(
                        {
                            "type": "ALERT",
                            "content": f"Doctor: High CPU load detected, Sir. '{offender}' is consuming excessive resources.",
                        }
                    )

            if alert == "CRITICAL_VRAM":
                if self.status_q and (
                    time.time() - self.last_alert_time > self.alert_cooldown
                ):
                    self.status_q.put(
                        {
                            "type": "ALERT",
                            "content": "Doctor: VRAM is nearly exhausted. Sir.",
                        }
                    )
                    self.last_alert_time = time.time()

            if alert == "CRITICAL_RAM":
                gc.collect()
                if self.status_q and (
                    time.time() - self.last_alert_time > self.alert_cooldown
                ):
                    self.status_q.put(
                        {
                            "type": "ALERT",
                            "content": "Doctor: Memory pressure detected. Optimizing.",
                        }
                    )
                    self.last_alert_time = time.time()

    def _find_cpu_offender(self):
        """Identifies the non-Charlie process using the most CPU."""
        try:
            processes = []
            for proc in psutil.process_iter(["name", "cpu_percent"]):
                # Ignore self and common system idle
                if proc.info["name"].lower() in [
                    "idle",
                    "system idle process",
                    "charlie",
                    "python",
                ]:
                    continue
                processes.append((proc.info["name"], proc.info["cpu_percent"]))

            if not processes:
                return "Unknown"
            top = sorted(processes, key=lambda x: x[1], reverse=True)[0]
            return top[0]
        except Exception as e:
            logger.debug(f"cpu_remediation_failed | error={e}")
            return "Unknown Process"

    def check_llm_health(self) -> bool:
        """Pings NIM (NVIDIA) LLM service for model availability."""
        try:
            # Check NIM endpoint instead of LM Studio
            nim_url = getattr(
                self.settings.llm, "nim_base_url", "https://integrate.api.nvidia.com"
            )
            r = requests.get(f"{nim_url.rstrip('/')}/v1/models", timeout=5)
            return r.status_code == 200
        except Exception as e:
            logger.debug(f"llm_health_check_failed | error={e}")
            return False

    def track_restart(self):
        """Registers a restart and checks for flood conditions."""
        now = time.time()
        self.restart_history = [t for t in self.restart_history if now - t < 60]
        self.restart_history.append(now)

        if len(self.restart_history) > 3:
            logger.critical("doctor_restart_flood_detected")
            if self.status_q:
                self.status_q.put(
                    {
                        "type": "ALERT",
                        "content": "Doctor: CRITICAL FAULT. Multiple restarts failed. Emergency shutdown initiated.",
                    }
                )
            return False  # Flood triggered
        return True

    def auto_repair_brain(self, error_trace):
        """Triggers AI-driven self-healing for the brain."""
        logger.info("doctor_triggering_self_healing", trace_len=len(error_trace))
        if self.status_q and hasattr(self.status_q, "put"):
            try:
                self.status_q.put(
                    {
                        "type": "ALERT",
                        "content": "Doctor: Internal fault detected. Initiating self-repair sequence.",
                    }
                )
            except Exception as e:
                logger.error(f"doctor_status_put_failed | {e}")

        # The actual repair is orchestrated by the Watchdog's SelfHealer
        # but the Doctor validates the intent.
        return True
