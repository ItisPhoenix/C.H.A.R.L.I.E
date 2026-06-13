import gc
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import requests

from charlie.config import settings
from charlie.utils.logger import get_logger
from charlie.utils.observability import Observability

logger = get_logger(__name__)


class Doctor:
    # Class-level alert cooldown — persists across instances so the
    # /api/doctor endpoint (which creates a new Doctor each call) doesn't spam.
    _last_alert_time: float = 0
    _alert_cooldown: float = 120

    def __init__(self, status_q=None, tracked_pids=None):
        self.obs = Observability(tracked_pids=tracked_pids)
        self.status_q = status_q
        self.settings = settings  # Store settings reference for LLM health check

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
                if self.status_q and (time.time() - Doctor._last_alert_time > Doctor._alert_cooldown):
                    self.status_q.put(
                        {
                            "type": "ALERT",
                            "content": "Doctor: VRAM is nearly exhausted. Sir.",
                        }
                    )
                    Doctor._last_alert_time = time.time()

            if alert == "CRITICAL_RAM":
                gc.collect()
                if self.status_q and (time.time() - Doctor._last_alert_time > Doctor._alert_cooldown):
                    self.status_q.put(
                        {
                            "type": "ALERT",
                            "content": "Doctor: Memory pressure detected. Optimizing.",
                        }
                    )
                    Doctor._last_alert_time = time.time()

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
        """Pings the universal LLM endpoint for model availability.

        Behaviour (Task 1 of Core Loop plan):
        - If LLM_API_KEY is empty, do NOT send an Authorization header
          (sending ``Authorization: Bearer `` (empty value) makes NIM and
          other OpenAI-compatible gateways return 401, which previously
          caused a 30-second "CRITICAL_LLM" false-positive loop).
        - 200-299  -> healthy, log ``llm_health_ok | status=<n>``
        - 401/403  -> healthy, log ``llm_health_unauth | status=<n>``
                       (server reachable, auth applied at chat time)
        - 404      -> healthy, log ``llm_health_no_models_endpoint``
        - 5xx      -> unhealthy, log ``llm_health_down | status=<n>``
        - network  -> unhealthy, log ``llm_health_down | error=<str>``
        """
        llm_url = getattr(self.settings.llm, "llm_url", "")
        if not llm_url:
            logger.debug("llm_health_down | error=no_url")
            return False

        llm_api_key = getattr(self.settings.llm, "llm_api_key", "") or ""
        url = f"{llm_url.rstrip('/')}/v1/models"

        # Build headers conditionally — omit Authorization when the key is empty.
        if llm_api_key:
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {llm_api_key}",
            }
        else:
            headers = {"Accept": "application/json"}

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as e:
            status = e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug(f"llm_health_down | error={e}")
            return False
        except Exception as e:
            logger.debug(f"llm_health_down | error={e}")
            return False

        if 200 <= status < 300:
            logger.info(f"llm_health_ok | status={status}")
            return True
        if status in (401, 403):
            logger.info(f"llm_health_unauth | status={status}")
            return True
        if status == 404:
            logger.info("llm_health_no_models_endpoint")
            return True

        logger.warning(f"llm_health_down | status={status}")
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


# ─── Task 20.1: Self-check dataclasses and run_self_check() ───────────────────


@dataclass
class DoctorCheck:
    """Single diagnostic check result."""

    name: str
    status: str  # "pass" | "warn" | "fail"
    message: str
    cause: str | None = None
    remediation: str | None = None


@dataclass
class DoctorReport:
    """Aggregated self-check report."""

    generated_at: float
    checks: list[DoctorCheck] = field(default_factory=list)
    overall: str = "pass"  # worst severity among checks


def run_self_check() -> DoctorReport:
    """Run a read-only self-check of Charlie's subsystems.

    Probes NIM reachability, STT/TTS model files, Gmail credentials,
    MCP server config, VRAM budget setting, and canonical tier assertions.
    Returns a DoctorReport with pass/warn/fail per check.

    This function MUST NOT modify config or source files.
    """
    checks: list[DoctorCheck] = []

    # ── 1. NIM reachability ──
    checks.append(_check_llm_reachability())

    # ── 2. STT model file ──
    checks.append(
        _check_file_exists(
            name="stt_model",
            path=Path("charlie/models/charlie.onnx"),
            description="STT wake-word model",
        )
    )

    # ── 3. TTS model file ──
    checks.append(
        _check_file_exists(
            name="tts_model",
            path=Path("charlie/models/kokoro-v1.0.onnx"),
            description="TTS Kokoro model",
        )
    )

    # ── 4. Gmail credentials ──
    checks.append(
        _check_file_exists(
            name="gmail_credentials",
            path=Path("config/secure/credentials.json"),
            description="Gmail OAuth credentials",
        )
    )

    # ── 5. MCP servers config ──
    checks.append(_check_mcp_servers())

    # ── 6. VRAM budget ──
    checks.append(_check_vram_budget())

    # ── 7. Canonical tier assertions ──
    checks.append(_check_canonical_tiers())

    # Compute overall severity
    severity_order = {"fail": 2, "warn": 1, "pass": 0}
    worst = max(checks, key=lambda c: severity_order.get(c.status, 0))
    overall = worst.status

    return DoctorReport(
        generated_at=time.time(),
        checks=checks,
        overall=overall,
    )


def _check_llm_reachability() -> DoctorCheck:
    """Probe LLM endpoint with a short timeout."""
    llm_url = getattr(settings.llm, "llm_url", "")
    if not llm_url:
        return DoctorCheck(
            name="llm_reachability",
            status="fail",
            message="LLM URL is not configured.",
            cause="settings.llm.llm_url is empty",
            remediation="Set LLM_URL in .env to any OpenAI-compatible endpoint.",
        )
    # Build /v1/models URL idempotently. NIM/Ollama/LM Studio users
    # commonly include /v1 in their base URL; appending /v1/models
    # unconditionally produces a 404 (e.g. .../v1/v1/models). See the
    # same pattern in charlie/brain/model_router.py:121.
    base = llm_url.rstrip("/")
    models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"

    # Build headers conditionally — omit Authorization when the key is
    # empty. Sending "Authorization: Bearer " (empty value) makes NIM
    # and other OpenAI-compatible gateways return 401/404, which
    # previously caused a false-positive 404 in the self-check.
    headers: dict = {}
    if getattr(settings.llm, "llm_api_key", ""):
        headers["Authorization"] = f"Bearer {settings.llm.llm_api_key}"

    try:
        r = requests.get(models_url, timeout=5, headers=headers)
        # 200-299  -> pass
        # 401/403  -> pass (server reachable, auth applied at chat time)
        # 404      -> pass (server reachable, no /v1/models endpoint)
        # 5xx      -> warn (server reachable, but unhappy)
        # network  -> fail
        if 200 <= r.status_code < 300 or r.status_code in (401, 403, 404):
            return DoctorCheck(
                name="llm_reachability",
                status="pass",
                message=f"LLM endpoint reachable (HTTP {r.status_code}).",
            )
        else:
            return DoctorCheck(
                name="llm_reachability",
                status="warn",
                message=f"LLM endpoint returned HTTP {r.status_code}.",
                cause=f"Server responded with status {r.status_code}",
                remediation="Check LLM_API_KEY and LLM_URL in .env.",
            )
    except requests.exceptions.Timeout:
        return DoctorCheck(
            name="llm_reachability",
            status="fail",
            message="LLM endpoint timed out (5s).",
            cause="Network timeout reaching LLM API",
            remediation="Verify LLM_URL is correct and the service is running.",
        )
    except requests.exceptions.ConnectionError:
        return DoctorCheck(
            name="llm_reachability",
            status="fail",
            message="LLM endpoint connection refused.",
            cause="Cannot connect to LLM API",
            remediation="Verify LLM_URL is correct and the service is running.",
        )
    except Exception as e:
        return DoctorCheck(
            name="llm_reachability",
            status="fail",
            message=f"LLM check failed: {e}",
            cause=str(e),
            remediation="Check network connectivity and LLM configuration.",
        )


def _check_file_exists(name: str, path: Path, description: str) -> DoctorCheck:
    """Check that a required file exists on disk."""
    if path.exists():
        return DoctorCheck(
            name=name,
            status="pass",
            message=f"{description} found at {path}.",
        )
    return DoctorCheck(
        name=name,
        status="fail",
        message=f"{description} not found at {path}.",
        cause=f"File missing: {path}",
        remediation=f"Ensure '{path}' exists. Re-download or regenerate if needed.",
    )


def _check_mcp_servers() -> DoctorCheck:
    """Check that at least one MCP server is configured."""
    mcp = getattr(settings, "mcp_servers", None)
    if mcp and len(mcp) > 0:
        return DoctorCheck(
            name="mcp_servers",
            status="pass",
            message=f"{len(mcp)} MCP server(s) configured.",
        )
    return DoctorCheck(
        name="mcp_servers",
        status="warn",
        message="No MCP servers configured.",
        cause="settings.mcp_servers is empty",
        remediation="Add MCP server entries to charlie_config.json under 'mcp_servers'.",
    )


def _check_vram_budget() -> DoctorCheck:
    """Check that VRAM budget is set."""
    budget = getattr(settings.resources, "vram_budget_mb", None)
    if budget and budget > 0:
        return DoctorCheck(
            name="vram_budget",
            status="pass",
            message=f"VRAM budget set to {budget} MB.",
        )
    return DoctorCheck(
        name="vram_budget",
        status="warn",
        message="VRAM budget not configured or zero.",
        cause="settings.resources.vram_budget_mb is not set",
        remediation="Set resources.vram_budget_mb in charlie_config.json.",
    )


def _check_canonical_tiers() -> DoctorCheck:
    """Verify canonical tier assertions via ToolRegistry if importable."""
    try:
        from charlie.tools.tool_registry import ToolRegistry
        from charlie.security.tiers import RiskTier

        registry = ToolRegistry()

        # Expected canonical tiers
        expected = {
            "delete_file": RiskTier.TIER_3,
            "self_modify": RiskTier.TIER_3,
            "apply_edit": RiskTier.TIER_3,
            "shutdown": RiskTier.TIER_2,
            "sleep_pc": RiskTier.TIER_2,
            "hibernate_pc": RiskTier.TIER_2,
        }

        # Register dummy tools to verify the seed table enforces tiers
        violations = []
        for tool_name, expected_tier in expected.items():
            # Register with a deliberately wrong tier (TIER_0) to confirm
            # the seed table overrides it
            registry.register(
                name=tool_name,
                description=f"doctor_check_{tool_name}",
                parameters={},
                handler=lambda **kw: None,
                risk_tier=RiskTier.TIER_0,
                category="doctor_check",
            )
            actual_tier = registry.get_tier(tool_name)
            if actual_tier != expected_tier:
                violations.append(f"{tool_name}: expected {expected_tier.name}, got {actual_tier.name}")

        if violations:
            return DoctorCheck(
                name="canonical_tiers",
                status="fail",
                message=f"Tier assertion failures: {'; '.join(violations)}",
                cause="Canonical tier seed table not enforcing expected tiers",
                remediation="Check _CANONICAL_TIER_SEEDS in charlie/tools/tool_registry.py.",
            )
        return DoctorCheck(
            name="canonical_tiers",
            status="pass",
            message="All canonical tier assertions verified (delete_file→TIER_3, shutdown→TIER_2).",
        )
    except ImportError as e:
        return DoctorCheck(
            name="canonical_tiers",
            status="warn",
            message=f"Could not import ToolRegistry: {e}",
            cause="Import error — module may not be installed",
            remediation="Ensure charlie.tools.tool_registry is importable.",
        )
    except Exception as e:
        return DoctorCheck(
            name="canonical_tiers",
            status="warn",
            message=f"Tier check encountered an error: {e}",
            cause=str(e),
            remediation="Review ToolRegistry.register and _CANONICAL_TIER_SEEDS.",
        )
