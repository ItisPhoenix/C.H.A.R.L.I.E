"""Structured diagnostic and self-healing system for Charlie V1.

Provides comprehensive subsystem health checks, factual evidence collection,
fix hints, and safe automated repair capabilities with circuit-breaker protection.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from charlie.runtime_introspector import RuntimeIntrospector
from charlie.subsystem_health import HealthState

logger = logging.getLogger("charlie.doctor")

# The authoritative aggregate health verdict is owned by
# charlie.subsystem_health, which already carries the per-subsystem
# (``HealthStatus``) and runtime (``RuntimeStatus``) vocabularies. Re-exporting
# it under the doctor's historical name keeps existing importers working while
# guaranteeing there is exactly one status vocabulary in the runtime: a
# diagnostic report and a runtime-truth snapshot cannot disagree about what
# "degraded" means because they are the same type.
DoctorStatus = HealthState

# LLM and vision probes are real network calls that can bill a completion.
# Re-running diagnostics must not fan out one request per caller, so a verdict
# is reused briefly inside a single doctor instance.
_PROBE_CACHE_TTL_S = 30.0
# Mirrors Brain.probe_primary_llm's default; long enough to cross a slow local
# endpoint, short enough that a diagnostics call cannot hang the runtime.
_DEFAULT_PROBE_TIMEOUT_S = 5.0


class CheckStatus(StrEnum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CheckSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Owner ids the runtime mints for work that is live but was never journaled.
# charlie/core.py builds `operation:<uuid4>` whenever task_id is None (the
# ordinary chat/tool loop) and `fastpath.<intent>` for the fast path, so neither
# ever appears in the TaskJournal. Journal membership alone is therefore not a
# liveness signal -- deriving it that way reported every in-flight lease as an
# orphan. An owner is only an orphan when it matches none of these shapes.
LIVE_OWNER_PREFIXES = ("operation:", "fastpath.")
# Retained from the original predicate: no current production site mints these,
# but an external/extension caller still may, and dropping them would turn a
# working lease into a reported fault.
EXEMPT_OWNER_IDS = ("user",)
EXEMPT_OWNER_PREFIXES = ("session_",)


def is_live_lease_owner(owner: str, known_task_ids: Optional[set[str]] = None) -> bool:
    """True when ``owner`` is a plausible live capability-lease holder.

    Shared by the capability-lease check and its repair so the two can never
    disagree -- a repair using a stricter predicate than the check would release
    the very lease the check just cleared.
    """
    if not owner:
        return False
    if known_task_ids and owner in known_task_ids:
        return True
    if owner in EXEMPT_OWNER_IDS:
        return True
    if owner.startswith(LIVE_OWNER_PREFIXES):
        return True
    return owner.startswith(EXEMPT_OWNER_PREFIXES)


@dataclass(frozen=True)
class ProbeResult:
    """Verdict of a real dependency probe.

    ``detail`` is evidence a human can read back and must never carry a key.
    ``status_code`` is preserved so a caller can tell "credential rejected"
    apart from "route unreachable" instead of collapsing both into one verdict.
    """

    ok: bool
    detail: str
    status_code: Optional[int] = None

    @property
    def credential_rejected(self) -> bool:
        return self.status_code in (401, 403)

    @property
    def transport_failed(self) -> bool:
        """Whether the request never obtained an HTTP response at all.

        A refused connection, DNS failure, or timeout is a different fault from
        a 5xx: the endpoint never saw the request, so it neither accepted nor
        rejected the credential. Collapsing both into one verdict would let a
        network problem be reported as a rejected key.
        """
        return not self.ok and self.status_code is None


@dataclass(frozen=True)
class PortOccupancy:
    """Whether a TCP port is currently held by a listening socket."""

    occupied: bool
    detail: str


def _probe_event_port(port: int) -> PortOccupancy:
    """Observe whether ``port`` is held, without disturbing whatever holds it.

    Binding is the only portable way to ask the OS that question, and this
    implementation never connects or listens, so a live EventBus PUB socket on
    that port is unaffected. On success the probe socket closes immediately,
    which is why a free port can still be claimed by the runtime a moment later
    -- an occupied reading is evidence of a live listener, a free reading only
    says nobody is bound *at this instant*.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError as exc:
        return PortOccupancy(True, f"bind refused: {type(exc).__name__}")
    finally:
        probe.close()
    return PortOccupancy(False, "bind succeeded, so no socket is listening")


def _resolve_event_port() -> int:
    """Re-derive the port ``EventBus`` would bind.

    Mirrors ``charlie.ipc.EventBus.__init__`` instead of constructing a bus:
    constructing one allocates a ZeroMQ context, which is runtime state a
    diagnostic must not own or tear down. Returns 0 when the runtime asked for
    an ephemeral port, which cannot be meaningfully probed.
    """
    from charlie.ipc import DEFAULT_EVENT_PORT

    if os.getenv("CHARLIE_TEST_MODE", "").lower() == "true":
        return int(os.getenv("CHARLIE_TEST_EVENT_PORT", "0"))
    return DEFAULT_EVENT_PORT


def _probe_chat_endpoint(
    base_url: str,
    model: str,
    api_key: Optional[str],
    timeout: float,
    trust_env: bool = False,
) -> Optional[ProbeResult]:
    """POST the same one-shot completion request ``Brain.probe_primary_llm`` sends.

    Returns ``None`` when no transport is importable, so the caller can report
    "unprobed" instead of guessing. Deliberately does not record telemetry or
    emit health transitions: a diagnostic observes, it does not mutate the
    canonical counters it is meant to read.
    """
    try:
        import httpx

        from charlie.utils import build_auth_headers
    except Exception:
        return None

    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "stream": False}
    try:
        with httpx.Client(
            base_url=base_url,
            headers=build_auth_headers(api_key or ""),
            timeout=timeout,
            trust_env=trust_env,
        ) as client:
            response = client.post("chat/completions", json=payload)
    except Exception as exc:
        return ProbeResult(False, f"{type(exc).__name__} contacting {base_url}/chat/completions")

    code = response.status_code
    if code < 400:
        return ProbeResult(True, f"HTTP {code} from {base_url}/chat/completions", code)
    return ProbeResult(False, f"HTTP {code} from {base_url}/chat/completions", code)


def _probe_searxng(base_url: str, timeout: float) -> Optional[ProbeResult]:
    """Ask the configured SearXNG instance for a JSON search response.

    Mirrors ``SearXNGProvider.search``'s request shape -- same endpoint, same
    ``format=json`` -- so a 200 here means the instance is genuinely able to
    serve discovery, not merely that something answered on the port. Returns
    ``None`` when no HTTP transport is importable, so the caller can report
    "unprobed" instead of guessing.

    Read-only and bounded: one GET, no engine side effects, no state written.
    A 200 with no results still proves the instance searched and answered,
    which is the dependency this check owns; result relevance is a research
    quality question, not a health question.
    """
    try:
        import httpx
    except Exception:
        return None

    url = f"{base_url.rstrip('/')}/search"
    params = {"q": "charlie", "format": "json", "language": "en"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url, params=params)
    except Exception as exc:
        return ProbeResult(False, f"{type(exc).__name__} contacting {url}")

    code = response.status_code
    if code >= 400:
        return ProbeResult(False, f"HTTP {code} from {url}", code)
    try:
        payload = response.json()
    except Exception:
        return ProbeResult(False, f"HTTP {code} from {url} but the body is not JSON", code)
    if not isinstance(payload, dict):
        return ProbeResult(False, f"HTTP {code} from {url} but the JSON body is not an object", code)
    if "results" not in payload:
        return ProbeResult(False, f"HTTP {code} from {url} but the response carries no 'results' key", code)
    count = len(payload.get("results") or [])
    return ProbeResult(True, f"HTTP {code} from {url}, {count} result(s)", code)


def _probe_tesseract(binary: str, timeout: float) -> ProbeResult:
    """Ask the tesseract binary to identify itself.

    ``--version`` is the cheapest real proof that the executable exists, loads
    its shared libraries, and can run -- the three ways a screen-OCR tier
    silently breaks in practice.
    """
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return ProbeResult(False, f"executable not found at '{binary}'")
    except subprocess.TimeoutExpired:
        return ProbeResult(False, f"'{binary} --version' did not answer within {timeout:g}s")
    except OSError as exc:
        return ProbeResult(False, f"could not execute '{binary}': {type(exc).__name__}")

    banner = next(iter((completed.stdout or "").strip().splitlines()), "").strip() or "(no output)"
    if completed.returncode != 0:
        return ProbeResult(False, f"'{binary} --version' exited {completed.returncode}: {banner}")
    return ProbeResult(True, f"{banner} (exit 0)")


@dataclass
class DiagnosticCheck:
    """Represents a single typed subsystem diagnostic check."""

    check_id: str
    category: str
    status: CheckStatus
    severity: CheckSeverity
    summary: str
    evidence: str
    probable_cause: Optional[str] = None
    fix_hint: Optional[str] = None
    repair_available: bool = False
    repair_id: Optional[str] = None
    requires_approval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["severity"] = self.severity.value
        return d


@dataclass
class DoctorReport:
    """Comprehensive diagnostic report aggregating all subsystem checks."""

    timestamp: float
    checks: List[DiagnosticCheck] = field(default_factory=list)
    total_checks: int = 0
    warnings: List[DiagnosticCheck] = field(default_factory=list)
    errors: List[DiagnosticCheck] = field(default_factory=list)

    @property
    def status(self) -> DoctorStatus:
        """The authoritative aggregate verdict for this report.

        Evidence-first and deliberately unable to overclaim:

        ``UNHEALTHY``
            At least one check produced an ERROR.
        ``DEGRADED``
            No ERROR, but at least one WARNING. A subsystem is impaired while
            the core still works.
        ``UNKNOWN``
            Nothing ran (empty report), or nothing was *proven*: the report
            holds no check with status ``OK``, so there is no positive evidence
            to base a healthy verdict on. Prior to this the report collapsed to
            a boolean and reported HEALTHY off ``len(errors) == 0``, which
            printed HEALTHY for a WARNING-level system and for a report that
            proved nothing at all.
        ``HEALTHY``
            At least one check was positively proven ``OK``, and no check
            produced an ERROR or a WARNING.

        ``INFO`` is neutral here. It means "no fault to report" (something
        deliberately disabled, or a plain observation) -- never "proven
        working". Any check that wants to be credited as healthy must reach
        ``OK``; a check that can only establish availability reports WARNING
        instead, so it cannot silently promote the system.
        """
        if not self.checks:
            # Nothing ran: an empty report is not evidence of health.
            return DoctorStatus.UNKNOWN
        if any(check.status == CheckStatus.ERROR for check in self.checks):
            return DoctorStatus.UNHEALTHY
        if any(check.status == CheckStatus.WARNING for check in self.checks):
            return DoctorStatus.DEGRADED
        if not any(check.status == CheckStatus.OK for check in self.checks):
            # Only INFO: no fault, but also nothing proven.
            return DoctorStatus.UNKNOWN
        return DoctorStatus.HEALTHY

    @property
    def is_healthy(self) -> bool:
        """Backward-compatible projection of :attr:`status`.

        Retained because readers outside this module consume it, but it now
        means exactly ``status is HEALTHY``. It deliberately no longer means
        ``len(errors) == 0``: a report whose only evidence is WARNING-level
        impairment is DEGRADED, and this must be False for it. Callers that
        want to distinguish "impaired" from "broken" must read :attr:`status`
        instead of collapsing the two.
        """
        return self.status == DoctorStatus.HEALTHY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_checks": len(self.checks),
            "status": self.status.value,
            "is_healthy": self.is_healthy,
            "warnings_count": len(self.warnings),
            "errors_count": len(self.errors),
            "checks": [c.to_dict() for c in self.checks],
            "warnings": [c.to_dict() for c in self.warnings],
            "errors": [c.to_dict() for c in self.errors],
        }


class CharlieDoctor:
    """Main diagnostic orchestrator and safe self-healing repair engine."""

    def __init__(
        self,
        config: Optional[Any] = None,
        introspector: Optional[RuntimeIntrospector] = None,
        capability_index: Optional[Any] = None,
        task_journal: Optional[Any] = None,
        lease_manager: Optional[Any] = None,
        health_registry: Optional[Any] = None,
        memory_service: Optional[Any] = None,
        llm_probe: Optional[Callable[[], Optional[ProbeResult]]] = None,
        probe_timeout: float = _DEFAULT_PROBE_TIMEOUT_S,
    ) -> None:
        """Build a diagnostic instance.

        ``llm_probe`` exists because ``charlie_doctor_diagnose`` constructs this
        class with no Brain (``charlie/tools.py``), and ``Brain.probe_primary_llm``
        is an async coroutine owned by a live client. Rather than requiring a
        Brain, the owner may inject any callable returning a ``ProbeResult`` --
        returning ``None`` states "this source cannot produce a verdict", which
        the model check reports as unverified rather than healthy.
        """
        if capability_index is None:
            from charlie.capabilities import get_capability_index

            capability_index = get_capability_index()
        self._config = config
        self._introspector = introspector or RuntimeIntrospector(
            config=config,
            capability_index=capability_index,
            health_registry=health_registry,
            task_journal=task_journal,
            lease_manager=lease_manager,
            memory_service=memory_service,
        )
        self._capability_index = capability_index
        self._task_journal = task_journal
        self._lease_manager = lease_manager
        self._health_registry = health_registry
        self._memory_service = memory_service
        self._llm_probe = llm_probe
        self._probe_timeout = probe_timeout
        self._llm_probe_cache: Optional[tuple[float, Optional[ProbeResult]]] = None

        # Repair tracking & circuit breaker
        self._repair_attempts: Dict[str, List[float]] = {}
        self._repair_failures: Dict[str, int] = {}

    # -------------------------------------------------------------------------
    # Probe plumbing
    # -------------------------------------------------------------------------

    def _runtime_config(self) -> Any:
        """Read config without assuming the introspector exposes a private hook.

        Reduced test doubles and the tools.py construction path both lack a
        usable config; a diagnostic that raises on that is worse than one that
        reports it could not observe the dependency.
        """
        getter = getattr(self._introspector, "_get_config", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def _probe_cache_is_fresh(self) -> bool:
        """Whether this instance already settled on a probe verdict for this run."""
        entry = self._llm_probe_cache
        return entry is not None and time.time() - entry[0] <= _PROBE_CACHE_TTL_S

    def _cached_llm_probe(self) -> Optional[ProbeResult]:
        """Return the recent LLM probe verdict without issuing a new request.

        The secrets check reads this instead of probing again, so "key is
        present" can be distinguished from "key is accepted" without paying for
        a second billable completion. Returns ``None`` when no verdict exists
        yet or the cached one aged out -- callers must treat that as unknown.
        """
        return self._llm_probe_cache[1] if self._probe_cache_is_fresh() else None

    def _run_llm_probe(self, cfg: Any) -> Optional[ProbeResult]:
        """Probe the configured chat route, reusing a recent verdict if present."""
        if self._probe_cache_is_fresh():
            return self._llm_probe_cache[1]
        if self._llm_probe is not None:
            result = self._llm_probe()
        else:
            result = _probe_chat_endpoint(
                base_url=cfg.llm_url,
                model=getattr(cfg, "llm_model", None) or "unknown",
                api_key=getattr(cfg, "llm_key", None) or getattr(cfg, "llm_api_key", None),
                timeout=self._probe_timeout,
                trust_env=bool(getattr(cfg, "llm_trust_env", False)),
            )
        self._llm_probe_cache = (time.time(), result)
        return result

    # -------------------------------------------------------------------------
    # Diagnostic Checks Suite
    # -------------------------------------------------------------------------

    def diagnose(self) -> DoctorReport:
        """Run all structured diagnostic checks across Charlie subsystems."""
        checks: List[DiagnosticCheck] = []

        # 1. Config Validity
        checks.append(self._check_config_validity())
        # 2. Model Provider. This check owns the live LLM probe, so it must run
        #    before secrets: the secrets check reads the probe verdict out of
        #    the cache to tell "key is present" apart from "key is accepted",
        #    and it deliberately never issues a second billable request. Run
        #    first, that verdict is already settled; run second, the cache is
        #    empty and a rejected key is reported as healthy. No other check
        #    reads the probe or another check's result, so this reordering
        #    changes only the report order, not any verdict.
        checks.append(self._check_model_provider())
        # 3. Secrets Configured
        checks.append(self._check_secrets_configured())
        # 4. Capability Registry
        checks.append(self._check_capability_registry())
        # 5. EventBus / IPC
        checks.append(self._check_event_bus())
        # 6. Task Journal
        checks.append(self._check_task_journal())
        # 7. Capability Leases
        checks.append(self._check_capability_leases())
        # 8. Memory Subsystem
        checks.append(self._check_memory_subsystem())
        # 10. Browser Subsystem
        checks.append(self._check_browser_subsystem())
        # 11. Desktop Subsystem
        checks.append(self._check_desktop_subsystem())
        # 11a. Research provider readiness (no non-invasive provider health API exists)
        checks.append(self._check_research_provider())
        # 12. Vision & OCR
        checks.append(self._check_vision_ocr())
        # 13. Voice Subsystem
        checks.append(self._check_voice_subsystem())
        # 14. Data Directories
        checks.append(self._check_data_directories())
        # 15. Subsystem Health Transitions
        checks.append(self._check_subsystem_health())
        # 16. Recovery State
        checks.append(self._check_recovery_state())
        # 17. Extension Registry
        checks.append(self._check_extensions_registry())

        warnings = [c for c in checks if c.status == CheckStatus.WARNING]
        errors = [c for c in checks if c.status == CheckStatus.ERROR]
        return DoctorReport(
            timestamp=time.time(),
            checks=checks,
            total_checks=len(checks),
            warnings=warnings,
            errors=errors,
        )

    # -------------------------------------------------------------------------
    # Individual Checks Implementation
    # -------------------------------------------------------------------------

    def _check_config_validity(self) -> DiagnosticCheck:
        cfg = self._introspector._get_config()
        if cfg is None:
            return DiagnosticCheck(
                check_id="config_validity",
                category="config",
                status=CheckStatus.ERROR,
                severity=CheckSeverity.CRITICAL,
                summary="Configuration not initialized",
                evidence="Config instance could not be loaded from charlie.config.",
                fix_hint="Verify .env file syntax and python path.",
            )

        return DiagnosticCheck(
            check_id="config_validity",
            category="config",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Configuration valid and loaded",
            evidence=(
                f"Loaded configuration: provider={getattr(cfg, 'llm_provider', 'unknown')}, "
                f"model={getattr(cfg, 'llm_model', 'unknown')}."
            ),
        )

    def _check_secrets_configured(self) -> DiagnosticCheck:
        cfg = self._runtime_config()
        model_info = self._introspector.get_model_info() if cfg else {}
        api_key = bool(model_info.get("api_key_configured"))
        provider = model_info.get("provider", "unknown")

        # If provider requires cloud key and it's missing
        if provider in ("openai", "anthropic", "gemini", "groq") and not api_key:
            return DiagnosticCheck(
                check_id="secrets_configured",
                category="secrets",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.HIGH,
                summary="LLM API key not configured",
                evidence=f"Configured provider '{provider}' requires an API key, but LLM_API_KEY is not set.",
                probable_cause="No LLM_API_KEY provided in .env or environment variables.",
                fix_hint="Set LLM_API_KEY in .env or switch to a local provider like Ollama.",
            )

        # Presence is a local read; acceptance is a remote fact. The model check
        # owns the live LLM probe and has already run by now (see diagnose()), so
        # read its cached verdict rather than issuing a second billable request
        # -- and say plainly when no verdict exists instead of implying the key
        # was tested. Every branch below must be literally true of the probe that
        # actually ran: a 5xx and a dead socket are different faults, and neither
        # is a rejected credential.
        probe = self._cached_llm_probe()
        if probe is not None and probe.credential_rejected:
            return DiagnosticCheck(
                check_id="secrets_configured",
                category="secrets",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.HIGH,
                summary=f"LLM credential present but rejected by the endpoint (HTTP {probe.status_code})",
                evidence=(
                    f"LLM API key is present for provider '{provider}', but the live endpoint probe "
                    f"answered {probe.status_code}. A present key is not a working key."
                ),
                probable_cause="The key is expired, revoked, or scoped to a different provider/account.",
                fix_hint="Replace LLM_API_KEY with a key the configured provider accepts.",
            )

        if probe is None:
            summary = "Required secrets configured; endpoint acceptance unverified"
            evidence = (
                f"LLM API key is present for provider '{provider}'. Only presence was verified: "
                "no live probe evidence is available to this diagnostic instance, so whether the "
                "key is accepted by the endpoint is unverified (see model_provider)."
            )
        elif probe.ok:
            summary = "Required secrets configured"
            evidence = (
                f"LLM API key is present for provider '{provider}' and the live endpoint probe "
                f"accepted it: {probe.detail}."
            )
        elif probe.transport_failed:
            # No HTTP response at all: the request never got an answer to read,
            # so calling this a rejection would be inventing a verdict.
            summary = "Required secrets configured; endpoint acceptance unverified"
            evidence = (
                f"LLM API key is present for provider '{provider}'. The live probe never obtained an "
                f"HTTP response from the route ({probe.detail}), so the endpoint neither accepted nor "
                "rejected the key and its acceptance is unverified. This is a transport failure, not a "
                "credential rejection. See model_provider for the failing dependency."
            )
        else:
            # An HTTP answer that is not 401/403: a server-side or routing fault.
            # The route answered, so it read the request, but it did not reject
            # the credential -- the key is not disproved.
            summary = "Required secrets configured; endpoint acceptance unverified"
            evidence = (
                f"LLM API key is present for provider '{provider}'. The route answered the live probe "
                f"with {probe.detail}, which is a server-side or gateway failure rather than a "
                "credential rejection (that would be HTTP 401/403), so the key itself was not "
                "disproved. See model_provider for the failing dependency."
            )

        return DiagnosticCheck(
            check_id="secrets_configured",
            category="secrets",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary=summary,
            evidence=evidence,
        )

    def _check_model_provider(self) -> DiagnosticCheck:
        """Verify the primary LLM route against the live endpoint, not just config.

        Introspection alone (``RuntimeIntrospector.get_model_info``) reads
        ``cfg.llm_model`` and can never observe a failure, so a dead provider
        used to be reported as OK while the runtime logged
        "Primary LLM probe failed with network error". charlie/AGENTS.md §12
        forbids exactly that, so this check now contacts the route and fails.
        """
        cfg = self._runtime_config()
        m = self._introspector.get_model_info()
        route = (
            f"Provider: {m.get('provider')}, Model: {m.get('model')}, "
            f"Base URL: {m.get('api_base_url') or 'default'}, Vision: {m.get('vision_model')}."
        )

        if cfg is None:
            # No config means no route to probe. config_validity owns the error;
            # claiming a model verdict here would be invention. WARNING, not
            # INFO: the primary dependency is unproven, and an unproven primary
            # route must never be able to aggregate into HEALTHY.
            return DiagnosticCheck(
                check_id="model_provider",
                category="models",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Model state unverified",
                evidence=(
                    f"{route} No configuration is available to this diagnostic instance, "
                    "so the LLM route was not probed."
                ),
            )

        base_url = getattr(cfg, "llm_url", None) or getattr(cfg, "llm_base_url", None)
        if not base_url:
            return DiagnosticCheck(
                check_id="model_provider",
                category="models",
                status=CheckStatus.ERROR,
                severity=CheckSeverity.HIGH,
                summary="LLM endpoint not configured",
                evidence=f"{route} Neither LLM_URL nor llm_base_url is set, so the primary route cannot be used.",
                probable_cause="LLM_URL is missing from the environment and .env.",
                fix_hint="Set LLM_URL to the OpenAI-compatible base URL and restart the runtime.",
            )

        result = self._run_llm_probe(cfg)
        if result is None:
            # No probe capability: unverified, not healthy. The endpoint may well
            # be fine, but nothing observed it and the report must say so -- as a
            # WARNING, because Charlie cannot answer anything without this route.
            return DiagnosticCheck(
                check_id="model_provider",
                category="models",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Model configured; no live probe available",
                evidence=(
                    f"{route} No live probe is available to this diagnostic instance "
                    "(no HTTP transport and no injected probe), so the endpoint was never contacted. "
                    "Model health is unverified."
                ),
            )

        if result.ok:
            return DiagnosticCheck(
                check_id="model_provider",
                category="models",
                status=CheckStatus.OK,
                severity=CheckSeverity.LOW,
                summary=f"Model endpoint reachable: {m.get('model', 'unknown')} ({m.get('provider', 'unknown')})",
                evidence=f"{route} Live probe succeeded: {result.detail}.",
            )

        # Three distinct faults must not be collapsed into one verdict: the
        # route rejected the credential (401/403), the route never answered
        # (transport), or the route answered with its own failure (e.g. 5xx).
        if result.credential_rejected:
            summary = f"LLM credential rejected by endpoint (HTTP {result.status_code})"
            probable_cause = "The configured API key is expired, revoked, or scoped to another provider/account."
            fix_hint = "Replace LLM_API_KEY with a key the configured provider accepts."
        elif result.transport_failed:
            summary = f"LLM endpoint unreachable: {m.get('model', 'unknown')} ({m.get('provider', 'unknown')})"
            probable_cause = "The LLM endpoint is down, or this host has no network path to it."
            fix_hint = "Verify LLM_URL reachability and outbound network access from this host."
        else:
            summary = f"LLM endpoint returned an error: {m.get('model', 'unknown')} ({m.get('provider', 'unknown')})"
            probable_cause = (
                f"The route answered HTTP {result.status_code}, so it is reachable and the request "
                "reached it, but it refused to serve this completion."
            )
            fix_hint = "Check the provider's own status and the model name the route expects."

        return DiagnosticCheck(
            check_id="model_provider",
            category="models",
            status=CheckStatus.ERROR,
            severity=CheckSeverity.HIGH,
            summary=summary,
            evidence=f"{route} Live probe failed: {result.detail}. The configured route cannot currently answer.",
            probable_cause=probable_cause,
            fix_hint=fix_hint,
        )

    def _check_capability_registry(self) -> DiagnosticCheck:
        caps = self._introspector.get_capabilities_info()
        total = caps.get("total", 0)
        avail = caps.get("available_count", 0)

        if total == 0:
            return DiagnosticCheck(
                check_id="capability_registry",
                category="capabilities",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="No capabilities registered",
                evidence="CapabilityIndex reports 0 registered capability domains.",
                fix_hint="Ensure charlie.tools / charlie.capabilities are properly imported during bootstrap.",
            )

        # Partial availability is not coherence. Reporting "OK (12/20 available)"
        # let a runtime missing 8 capability domains aggregate to HEALTHY, which
        # is exactly the false green this check exists to prevent: the index is
        # only healthy when every registered domain can actually be invoked.
        if avail < total:
            missing = sorted(
                str(cap_id)
                for cap_id, record in (caps.get("by_id") or {}).items()
                if isinstance(record, dict) and record.get("available") is not True
            )
            return DiagnosticCheck(
                check_id="capability_registry",
                category="capabilities",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary=f"Capability index incomplete: {avail}/{total} available",
                evidence=(
                    f"{total} capability domains registered, {avail} currently available; "
                    f"{total - avail} not invokable"
                    + (f": {', '.join(missing[:12])}" + (" ..." if len(missing) > 12 else "") if missing else "")
                    + "."
                ),
                probable_cause="A registered capability's dependency import or availability probe failed.",
                fix_hint="Resolve the failing dependency for the unavailable capability domains listed above.",
            )

        return DiagnosticCheck(
            check_id="capability_registry",
            category="capabilities",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary=f"Capability index coherent ({avail}/{total} available)",
            evidence=f"All {total} registered capability domains report available.",
        )

    def _check_event_bus(self) -> DiagnosticCheck:
        """Report only the local transport dependency; delivery readiness is unknown."""
        try:
            import zmq

            transport = f"ZeroMQ {zmq.zmq_version()} via pyzmq {zmq.__version__}"
        except Exception as exc:
            return DiagnosticCheck(
                check_id="event_bus",
                category="health",
                status=CheckStatus.ERROR,
                severity=CheckSeverity.HIGH,
                summary="EventBus transport unavailable",
                evidence=(
                    f"ZeroMQ transport could not be imported: {type(exc).__name__}: {exc}. "
                    "Without it charlie.ipc.EventBus cannot bind or publish."
                ),
                probable_cause="pyzmq is not installed or its native libzmq failed to load.",
                fix_hint="Install pyzmq: `uv sync` or `pip install pyzmq`.",
            )

        cfg = self._runtime_config()
        if cfg is None:
            return DiagnosticCheck(
                check_id="event_bus",
                category="health",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="EventBus readiness unverified",
                evidence=(
                    f"{transport}. No configuration is available to this diagnostic instance, "
                    "so runtime EventBus readiness could not be observed; no socket or delivery "
                    "probe was performed."
                ),
            )

        # What is actually proven here is narrow and must be labelled as such:
        # the ZeroMQ transport imports, and nothing else. Publication is
        # observable without shipping a real event, which is read-only and
        # bounded; delivery is observable only by consuming from the live bus,
        # which would inject into runtime state a diagnostic does not own. So
        # this check reports transport availability and stops there -- as INFO,
        # because nothing here is faulted and nothing here is proven.
        observations = [
            f"{transport} is importable",
            "bus bind/publish state was not probed",
            "no event was published or consumed, so delivery is unproven",
        ]

        return DiagnosticCheck(
            check_id="event_bus",
            category="health",
            status=CheckStatus.INFO,
            severity=CheckSeverity.LOW,
            summary="EventBus transport available; delivery unverified",
            evidence=" ".join(observations) + ".",
            fix_hint=(
                "Start the runtime bus and confirm a subscriber receives a published event to clear "
                "this warning; the doctor cannot prove delivery without injecting runtime events."
            ),
        )

    def _check_task_journal(self) -> DiagnosticCheck:
        """Assert that the journal can actually be read from and written to.

        The previous version of this check asserted nothing: it read a
        read-only snapshot and returned OK-shaped evidence for a journal that
        may not exist, may be memory-only, and may be sitting on an unwritable
        state path. A journal that cannot persist loses every task on restart,
        so the check now proves both halves or reports which one failed.

        READ is exercised against the live journal through the same accessor
        the runtime uses. WRITE is proven against the journal's real
        persistence target on this host (existence, parent-directory and file
        permissions) rather than by inserting a throwaway record, because a
        diagnostic must not write into the task history it is here to report on.
        """
        journal = self._task_journal
        if journal is None:
            return DiagnosticCheck(
                check_id="task_journal",
                category="tasks",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Task journal unavailable",
                evidence=(
                    "No TaskJournal instance is wired into this diagnostic, so task history cannot be "
                    "read or written. Task state would not survive a restart."
                ),
                fix_hint="Construct CharlieDoctor with the runtime's TaskJournal so its durability can be verified.",
            )

        # --- READ: exercise the live journal rather than trusting a cached count.
        try:
            records = journal.list()
        except Exception as exc:
            return DiagnosticCheck(
                check_id="task_journal",
                category="tasks",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Task journal unreadable",
                evidence=f"Reading the live TaskJournal raised {type(exc).__name__}: {exc}.",
                probable_cause="The journal backing store is corrupt or unreadable.",
                fix_hint="Inspect the journal state file and permissions.",
            )
        if not isinstance(records, list):
            return DiagnosticCheck(
                check_id="task_journal",
                category="tasks",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Task journal read returned an unexpected shape",
                evidence=f"TaskJournal.list() returned {type(records).__name__}, expected list.",
            )

        # --- WRITE: prove the real persistence target accepts writes.
        state_path = getattr(journal, "_state_path", None)
        if state_path is None:
            return DiagnosticCheck(
                check_id="task_journal",
                category="tasks",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary=f"Task journal readable but not persisted ({len(records)} record(s))",
                evidence=(
                    f"Read of the live journal returned {len(records)} record(s), but the journal has no "
                    "state_path: it is memory-only, so every task is lost on restart and a write cannot be "
                    "proven."
                ),
                probable_cause="TaskJournal was constructed without a state_path.",
                fix_hint="Construct the runtime TaskJournal with a state_path so task history is durable.",
            )

        path = Path(state_path)
        observations = [f"read {len(records)} record(s) from the live journal", f"state_path={path}"]
        if path.exists():
            if not os.access(path, os.W_OK):
                return DiagnosticCheck(
                    check_id="task_journal",
                    category="tasks",
                    status=CheckStatus.WARNING,
                    severity=CheckSeverity.HIGH,
                    summary="Task journal state file is not writable",
                    evidence="; ".join(observations) + "; the existing state file rejects writes.",
                    probable_cause="File permissions or a lock prevent the journal from persisting updates.",
                    fix_hint="Grant write access to the journal state file.",
                )
        else:
            parent = path.parent
            if not parent.exists() or not os.access(parent, os.W_OK):
                return DiagnosticCheck(
                    check_id="task_journal",
                    category="tasks",
                    status=CheckStatus.WARNING,
                    severity=CheckSeverity.HIGH,
                    summary="Task journal state directory is not writable",
                    evidence=(
                        "; ".join(observations)
                        + f"; parent directory '{parent}' does not exist or rejects writes, so the journal "
                        "cannot create its state file."
                    ),
                    probable_cause="The journal data directory is missing or read-only.",
                    fix_hint="Create the journal data directory and grant write access.",
                )

        return DiagnosticCheck(
            check_id="task_journal",
            category="tasks",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary=f"Task journal readable and writable ({len(records)} record(s))",
            evidence=(
                "; ".join(observations)
                + "; the persistence target accepts writes, so new tasks will survive a restart."
            ),
        )

    def _check_capability_leases(self) -> DiagnosticCheck:
        leases_info = self._introspector.get_leases_info()
        active_leases = leases_info.get("active_leases", {})

        # Liveness comes from the shape of the owner id plus the journal, never
        # from journal membership alone: real owners are frequently `operation:*`
        # or `fastpath.*` and are never journaled.
        tasks_info = self._introspector.get_tasks_info()
        active_task_ids = {t["task_id"] for t in tasks_info.get("active_tasks", []) if t.get("task_id")}

        orphans = {}
        unjournaled = {}
        for cap, owner in active_leases.items():
            if owner in active_task_ids:
                continue
            if is_live_lease_owner(owner, active_task_ids):
                unjournaled.setdefault(cap, owner)
            else:
                orphans[cap] = owner

        if orphans:
            return DiagnosticCheck(
                check_id="capability_leases",
                category="leases",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary=f"Orphan capability lease detected: {list(orphans.keys())}",
                evidence=(
                    f"Leases held by terminated/unknown owners: {orphans}. "
                    f"Live non-journal owners (not counted as orphans): {unjournaled or 'none'}."
                ),
                probable_cause="A task terminated without explicitly releasing its capability lock.",
                fix_hint="Execute lease cleanup to release stuck resource locks.",
                repair_available=True,
                repair_id="repair_stale_leases",
                requires_approval=False,
            )

        return DiagnosticCheck(
            check_id="capability_leases",
            category="leases",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary="Capability lease arbitration healthy",
            evidence=(
                f"{len(active_leases)} active leases, no orphan locks detected. "
                f"Live non-journal owners: {unjournaled or 'none'}."
            ),
        )

    def _check_memory_subsystem(self) -> DiagnosticCheck:
        mem = self._introspector.get_memory_info()
        if not isinstance(mem, dict):
            mem = {"status": "error"}

        health = mem.get("health") if isinstance(mem.get("health"), dict) else {}
        structured = health.get("structured") or mem.get("structured")
        semantic = health.get("semantic") or mem.get("semantic_health") or mem.get("semantic")
        status = mem.get("status", "unavailable")
        if not isinstance(status, str):
            status = "error"
        if status not in {"available", "degraded", "unavailable", "error", "disabled"}:
            status = "error"

        def component_state(label: str, component: Any) -> str:
            if not isinstance(component, dict):
                return f"{label} state unavailable"
            component_status = component.get("status")
            if component_status == "available":
                return f"{label} available"
            if component_status == "disabled":
                return f"{label} intentionally disabled"
            if component_status == "unavailable":
                return f"{label} unavailable"
            if component_status == "error":
                return f"{label} error"
            return f"{label} state unknown"

        structured_evidence = component_state("Structured memory", structured)
        semantic_evidence = component_state("Semantic memory", semantic)
        evidence = f"{structured_evidence}; {semantic_evidence}."
        total_items = mem.get("total_items", 0)
        if not isinstance(total_items, int) or total_items < 0:
            total_items = 0

        # Protect callers that still provide legacy stats without component
        # status: an explicitly unavailable semantic adapter cannot be called
        # healthy merely because the wrapper returned statistics.
        semantic_unavailable = isinstance(semantic, dict) and (
            semantic.get("status") == "unavailable"
            or (
                semantic.get("status") is None
                and semantic.get("available") is False
                and semantic.get("configured") is not False
            )
        )
        if status == "available" and semantic_unavailable:
            status = "degraded"

        if status == "error":
            return DiagnosticCheck(
                check_id="memory_subsystem",
                category="memory",
                status=CheckStatus.ERROR,
                severity=CheckSeverity.HIGH,
                summary="Memory subsystem error",
                evidence=f"{evidence} Memory service reported an adapter error without exposing internal details.",
                fix_hint="Check SQLite database permissions and schema.",
            )

        if status == "unavailable":
            return DiagnosticCheck(
                check_id="memory_subsystem",
                category="memory",
                status=CheckStatus.ERROR,
                severity=CheckSeverity.HIGH,
                summary="Memory subsystem unavailable",
                evidence=f"{evidence} Required memory backend is not available.",
                fix_hint="Check SQLite database permissions and configured memory adapters.",
            )

        if status == "degraded":
            return DiagnosticCheck(
                check_id="memory_subsystem",
                category="memory",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Memory subsystem degraded",
                evidence=f"{evidence} Structured memory remains available with reduced semantic capability.",
                fix_hint="Check the semantic embedding service or disable optional vector memory.",
            )

        if status == "disabled":
            return DiagnosticCheck(
                check_id="memory_subsystem",
                category="memory",
                status=CheckStatus.INFO,
                severity=CheckSeverity.LOW,
                summary="Memory subsystem disabled",
                evidence=f"{evidence} Memory is intentionally disabled.",
            )

        semantic_disabled = isinstance(semantic, dict) and (
            semantic.get("status") == "disabled"
            or (
                semantic.get("status") is None
                and semantic.get("available") is False
                and semantic.get("configured") is False
            )
        )
        if semantic_disabled:
            return DiagnosticCheck(
                check_id="memory_subsystem",
                category="memory",
                status=CheckStatus.INFO,
                severity=CheckSeverity.LOW,
                summary="Memory subsystem available; semantic memory disabled",
                evidence=f"{evidence} Structured memory contains {total_items} managed item(s).",
            )

        return DiagnosticCheck(
            check_id="memory_subsystem",
            category="memory",
            status=CheckStatus.OK,
            severity=CheckSeverity.LOW,
            summary=(
                "Memory subsystem healthy"
                if isinstance(structured, dict)
                and structured.get("status") == "available"
                and isinstance(semantic, dict)
                and semantic.get("status") == "available"
                else "Memory subsystem available"
            ),
            evidence=f"{evidence} {total_items} managed item(s) reported.",
        )

    def _check_browser_subsystem(self) -> DiagnosticCheck:
        """Availability-only: prove the import, refuse to claim the launch.

        ``get_subsystem_info()['browser']['available']`` is a module-level
        import flag. It says the browser bindings loaded; it says nothing about
        whether a browser can launch, reach the network, or execute an action.
        Launching one is a heavyweight, host-visible side effect that a
        diagnostic must not perform behind the user's back, so importable
        bindings report INFO: no fault observed, function unproven. Bindings
        that failed to import are a real fault and stay WARNING.
        """
        subsys = self._introspector.get_subsystem_info()
        b_info = subsys.get("browser") if isinstance(subsys, dict) else None
        if not isinstance(b_info, dict) or not isinstance(b_info.get("available"), bool):
            return DiagnosticCheck(
                check_id="browser_subsystem",
                category="browser",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Browser readiness unverified",
                evidence=(
                    "No usable read-only browser dependency status was returned; no browser was launched. "
                    "Browser function is unproven."
                ),
            )
        avail = b_info.get("available", False)

        status = CheckStatus.INFO if avail else CheckStatus.WARNING
        summary = (
            "Browser bindings importable; launch readiness unverified (availability-only)"
            if avail
            else "Browser dependencies unavailable"
        )
        evidence = (
            f"AVAILABILITY-ONLY. Module available={avail}. This proves imports resolved and nothing more: "
            "no browser was launched and no navigation, DOM, or screenshot action was exercised."
        )

        return DiagnosticCheck(
            check_id="browser_subsystem",
            category="browser",
            status=status,
            severity=CheckSeverity.LOW,
            summary=summary,
            evidence=evidence,
            fix_hint=None if avail else "Install playwright: `uv run playwright install chromium`",
        )

    def _check_desktop_subsystem(self) -> DiagnosticCheck:
        """Availability-only: prove the import, refuse to claim host control.

        Same contract as the browser check. ``DESKTOP_AVAILABLE`` is an import
        flag; actually touching the host would move the user's mouse and
        foreground windows, which a diagnostic has no business doing unasked.
        """
        subsys = self._introspector.get_subsystem_info()
        d_info = subsys.get("desktop") if isinstance(subsys, dict) else None
        if not isinstance(d_info, dict) or not isinstance(d_info.get("available"), bool):
            return DiagnosticCheck(
                check_id="desktop_subsystem",
                category="desktop",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Desktop readiness unverified",
                evidence=(
                    "No usable read-only desktop dependency status was returned; no host action was "
                    "attempted. Desktop control is unproven."
                ),
            )
        avail = d_info.get("available", False)

        return DiagnosticCheck(
            check_id="desktop_subsystem",
            category="desktop",
            status=CheckStatus.INFO if avail else CheckStatus.WARNING,
            severity=CheckSeverity.LOW,
            summary=(
                "Desktop bindings importable; action readiness unverified (availability-only)"
                if avail
                else "Desktop dependencies unavailable"
            ),
            evidence=(
                f"AVAILABILITY-ONLY. Module available={avail}, platform={d_info.get('platform')}. "
                "This proves imports resolved and nothing more: no observation, focus, or input action "
                "was performed against the host."
            ),
        )

    def _check_research_provider(self) -> DiagnosticCheck:
        """Prove search actually answers, or say exactly how far verification got.

        Search is the substrate every research answer rests on, and it had no
        check at all -- a system with no working search provider aggregated to
        HEALTHY. This check inventories the configured providers from config
        (data-driven: no provider name is hardcoded into the logic) and then
        performs a real, bounded, read-only request against whichever one it can
        prove.

        A configured-but-unprobed provider is reported as a WARNING, never OK:
        a key or URL sitting in config is presence, and presence is not a
        working search backend.
        """
        cfg = self._runtime_config()
        if cfg is None:
            return DiagnosticCheck(
                check_id="research_provider",
                category="research",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary="Search readiness unverified",
                evidence=(
                    "No configuration is available to this diagnostic instance, so no search provider "
                    "could be identified and no request was made."
                ),
            )

        if not bool(getattr(cfg, "research_enabled", True)):
            # Deliberately switched off: not a fault, and not a claim of health.
            return DiagnosticCheck(
                check_id="research_provider",
                category="research",
                status=CheckStatus.INFO,
                severity=CheckSeverity.LOW,
                summary="Research disabled by configuration",
                evidence="RESEARCH_ENABLED is false; no search provider was probed.",
            )

        # Inventory configured providers from config. Anything listed here is
        # presence only -- acceptance is proven below, never assumed.
        searxng_url = (getattr(cfg, "searxng_url", "") or "").strip()
        unprobed: List[str] = []
        if not searxng_url:
            # A configured instance is probed for real below; only an absent one
            # belongs in the presence-only inventory.
            unprobed.append("searxng (SEARXNG_URL unset)")

        for attr, label in (
            ("exa_api_key", "exa"),
            ("tavily_api_key", "tavily"),
        ):
            if (getattr(cfg, attr, "") or "").strip():
                unprobed.append(f"{label} (key present, acceptance unverified)")

        for enabled_attr, url_attr, label in (
            ("research_bing_enabled", "research_bing_endpoint", "bing"),
            ("research_yacy_enabled", "research_yacy_url", "yacy"),
            ("research_ddg_enabled", "research_ddg_endpoint", "duckduckgo"),
        ):
            if bool(getattr(cfg, enabled_attr, False)):
                if (getattr(cfg, url_attr, "") or "").strip():
                    unprobed.append(f"{label} (enabled; endpoint reachable/unreachable not probed)")
                else:
                    unprobed.append(f"{label} (enabled but endpoint unset)")

        # --- Real probe: a bounded GET against the self-hosted instance.
        if searxng_url:
            probe = _probe_searxng(searxng_url, self._probe_timeout)
            if probe is None:
                return DiagnosticCheck(
                    check_id="research_provider",
                    category="research",
                    status=CheckStatus.WARNING,
                    severity=CheckSeverity.MEDIUM,
                    summary="SearXNG probe unavailable (availability-only)",
                    evidence=(
                        f"SEARXNG_URL={searxng_url} is configured, but no HTTP transport is available to "
                        "this diagnostic, so the instance was never contacted."
                    ),
                )
            if not probe.ok:
                return DiagnosticCheck(
                    check_id="research_provider",
                    category="research",
                    status=CheckStatus.WARNING,
                    severity=CheckSeverity.MEDIUM,
                    summary=f"SearXNG not answering: {probe.detail}",
                    evidence=(
                        f"Live probe of {searxng_url}/search failed: {probe.detail}. Search cannot "
                        "currently return results, so research has no discovery stage."
                    ),
                    probable_cause="The SearXNG instance is down, unreachable, or no longer serving JSON.",
                    fix_hint="Start the SearXNG instance and confirm /search responds with format=json.",
                )
            return DiagnosticCheck(
                check_id="research_provider",
                category="research",
                status=CheckStatus.OK,
                severity=CheckSeverity.LOW,
                summary=f"Search provider reachable: {probe.detail}",
                evidence=(
                    f"Live probe of {searxng_url}/search returned HTTP {probe.status_code} with a valid "
                    "JSON body. Search discovery is proven to work end to end."
                    + (
                        f" Other configured providers remain unprobed: {'; '.join(unprobed)}."
                        if unprobed
                        else ""
                    )
                ),
            )

        if not unprobed:
            return DiagnosticCheck(
                check_id="research_provider",
                category="research",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.HIGH,
                summary="Research enabled but no search provider configured",
                evidence=(
                    "RESEARCH_ENABLED is true, yet no search backend is configured. Every research request "
                    "would fail at the discovery stage with nothing to report."
                ),
                probable_cause="No search provider URL or key is set in .env.",
                fix_hint="Set SEARXNG_URL, or configure a hosted search provider key.",
            )

        return DiagnosticCheck(
            check_id="research_provider",
            category="research",
            status=CheckStatus.WARNING,
            severity=CheckSeverity.MEDIUM,
            summary="Search configured but unproven (availability-only)",
            evidence=(
                "AVAILABILITY-ONLY. Research is enabled and configured providers are present: "
                f"{'; '.join(unprobed)}. No provider was contacted, so whether search actually returns "
                "results is unproven."
            ),
        )

    def _check_vision_ocr(self) -> DiagnosticCheck:
        """Verify the perception tier by exercising its real dependencies.

        Three separate things can fail independently here, so each is observed:
        the OCR python packages import, the tesseract executable answers, and
        the configured vision endpoint completes a request. None of them is
        inferred from configuration.
        """
        cfg = self._runtime_config()
        if cfg is None:
            return DiagnosticCheck(
                check_id="vision_ocr",
                category="vision",
                status=CheckStatus.INFO,
                severity=CheckSeverity.LOW,
                summary="Vision/OCR state unknown",
                evidence=(
                    "No configuration is available to this diagnostic instance, so neither the OCR "
                    "binary nor the vision endpoint could be observed."
                ),
            )

        observations: List[str] = []
        degraded = False

        # 1. OCR python packages (mss / pytesseract / Pillow), as the capture tier
        #    itself reports them -- no duplicate import logic here.
        try:
            from charlie.desktop import ocr as ocr_tier

            ocr_available = bool(getattr(ocr_tier, "OCR_AVAILABLE", False))
        except Exception as exc:
            ocr_available = False
            observations.append(f"charlie.desktop.ocr import failed: {type(exc).__name__}")
        if ocr_available:
            observations.append("OCR tier importable (mss/pytesseract/Pillow present)")
        else:
            degraded = True
            observations.append("OCR tier reports OCR_AVAILABLE=False (mss/pytesseract/Pillow missing)")

        # 2. The exact binary the OCR tier would run. ocr.py only overrides
        #    pytesseract's default when TESSERACT_CMD is set, otherwise it relies
        #    on PATH -- so the probe follows the same resolution.
        configured_cmd = (getattr(cfg, "tesseract_cmd", None) or "").strip()
        binary = configured_cmd or (shutil.which("tesseract") or "")
        if not binary:
            degraded = True
            observations.append("no tesseract binary configured (TESSERACT_CMD unset and 'tesseract' not on PATH)")
        else:
            binary_probe = _probe_tesseract(binary, self._probe_timeout)
            observations.append(f"tesseract '{binary}': {binary_probe.detail}")
            if not binary_probe.ok:
                degraded = True

        # 3. Vision endpoint, only when it is switched on -- probing a
        #    deliberately disabled feature would report a fault the owner chose.
        if not getattr(cfg, "vision_enabled", False):
            observations.append("vision endpoint disabled by configuration (VISION_ENABLED=false)")
        else:
            vision_url = getattr(cfg, "vision_llm_url", None)
            if not vision_url:
                degraded = True
                observations.append("vision endpoint enabled but VISION_LLM_URL is not set")
            else:
                vision_probe = _probe_chat_endpoint(
                    base_url=vision_url,
                    model=getattr(cfg, "vision_llm_model", None) or "unknown",
                    api_key=getattr(cfg, "vision_llm_key", None),
                    timeout=self._probe_timeout,
                )
                if vision_probe is None:
                    degraded = True
                    observations.append("vision endpoint probe unavailable (no HTTP transport)")
                elif vision_probe.ok:
                    observations.append(f"vision endpoint '{vision_url}': {vision_probe.detail}")
                else:
                    degraded = True
                    observations.append(f"vision endpoint '{vision_url}' probe failed: {vision_probe.detail}")

        return DiagnosticCheck(
            check_id="vision_ocr",
            category="vision",
            status=CheckStatus.WARNING if degraded else CheckStatus.OK,
            severity=CheckSeverity.MEDIUM if degraded else CheckSeverity.LOW,
            summary=(
                "Vision/OCR tier degraded"
                if degraded
                else "Vision/OCR dependencies verified"
            ),
            evidence=" ".join(observations),
            fix_hint=(
                "Install the tesseract binary and the OCR python packages, or start the configured "
                "vision endpoint. UIA remains the primary desktop perception tier."
                if degraded
                else None
            ),
        )

    def _check_voice_subsystem(self) -> DiagnosticCheck:
        """Availability-only: report the startup string, claim nothing more.

        Comparing a startup-state string proves that some supervisor reported a
        state; it cannot prove a microphone captured audio or that TTS was
        audible. charlie/AGENTS.md 8 requires real-host evidence for voice, so
        this check is availability-only: a supervisor reporting no failure
        emits INFO, since nothing is faulted and nothing is proven. A reported
        failure is a real fault and stays WARNING.
        """
        subsys = self._introspector.get_subsystem_info()
        v_info = subsys.get("voice", {})
        model_present = v_info.get("wake_word_model_present", False)

        health_info = self._introspector.get_health_info()
        voice_health = health_info.get("voice", {}) if isinstance(health_info, dict) else {}
        voice_state = voice_health.get("status", "unknown") if isinstance(voice_health, dict) else "unknown"
        failed = voice_state in {"failed", "error", "degraded", "unavailable"}
        status = CheckStatus.WARNING if failed else CheckStatus.INFO
        summary = (
            "Voice runtime reports failure"
            if failed
            else f"Voice runtime state {voice_state}; audio readiness unverified (availability-only)"
        )
        return DiagnosticCheck(
            check_id="voice_subsystem",
            category="voice",
            status=status,
            severity=CheckSeverity.HIGH if failed else CheckSeverity.LOW,
            summary=summary,
            evidence=(
                f"AVAILABILITY-ONLY. Runtime state: {voice_state}; wake model present: {model_present}. "
                "Microphone capture and audible playback were not verified, so voice function is unproven."
            ),
        )

    def _check_data_directories(self) -> DiagnosticCheck:
        cwd = Path(os.getcwd())
        writable = os.access(cwd, os.W_OK)
        return DiagnosticCheck(
            check_id="data_directories",
            category="storage",
            status=CheckStatus.INFO if writable else CheckStatus.WARNING,
            severity=CheckSeverity.LOW,
            summary="Repository write access reported" if writable else "Repository write access unavailable",
            evidence=f"OS write-access check for '{cwd}': {writable}. Individual data stores unverified.",
        )

    def _check_subsystem_health(self) -> DiagnosticCheck:
        try:
            health = self._introspector.get_health_info()
        except Exception as exc:
            return DiagnosticCheck(
                check_id="subsystem_health", category="health", status=CheckStatus.INFO,
                severity=CheckSeverity.MEDIUM, summary="Runtime health unavailable",
                evidence=f"Read-only runtime health snapshot unavailable: {type(exc).__name__}.",
            )
        if not isinstance(health, dict) or not health:
            return DiagnosticCheck(
                check_id="subsystem_health",
                category="health",
                status=CheckStatus.INFO,
                severity=CheckSeverity.LOW,
                summary="Runtime health unknown",
                evidence="No authoritative subsystem health observations are available.",
            )

        degraded = []
        unknown = []
        observed = {}
        for name, value in health.items():
            state = value.get("status") if isinstance(value, dict) else None
            if not isinstance(state, str) or not state or state == "unknown":
                unknown.append(name)
            elif state in {"failed", "error", "degraded", "unavailable"}:
                degraded.append(name)
                observed[name] = state
            elif state not in {"running", "disabled"}:
                unknown.append(name)

        if degraded:
            return DiagnosticCheck(
                check_id="subsystem_health",
                category="health",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.MEDIUM,
                summary=f"Subsystem supervisors report degraded state: {degraded}",
                evidence=f"Reported failure states: {observed}. Functional capability checks were not run.",
                probable_cause="One or more subsystem health owners report a failed or degraded state.",
                fix_hint="Check the affected subsystem's runtime diagnostics.",
            )

        return DiagnosticCheck(
            check_id="subsystem_health",
            category="health",
            status=CheckStatus.INFO,
            severity=CheckSeverity.LOW,
            summary=(
                "Subsystem readiness unknown"
                if unknown
                else "Subsystem lifecycle states observed; capability readiness unknown"
            ),
            evidence=(
                f"Tracked subsystem states: {len(health)}; unknown states: {unknown or 'none'}. "
                "Running or disabled lifecycle state does not verify subsystem operations."
            ),
        )

    def _check_recovery_state(self) -> DiagnosticCheck:
        blocked = [repair for repair in self._repair_failures if self.is_repair_circuit_broken(repair)]
        return DiagnosticCheck(
            check_id="recovery_state",
            category="recovery",
            status=CheckStatus.WARNING if blocked else CheckStatus.INFO,
            severity=CheckSeverity.LOW,
            summary="Doctor repair circuit breakers blocked" if blocked else "Doctor repair circuit breakers clear",
            evidence=f"Blocked repairs in this diagnostic instance: {blocked}. Other recovery owners not verified.",
        )

    def _check_extensions_registry(self) -> DiagnosticCheck:
        try:
            from charlie.self_extension.registry import ExtensionRegistry

            reg = ExtensionRegistry(capability_index=self._capability_index)
            entries = reg.list()
            enabled_cnt = sum(1 for e in entries if e.enabled)
            enabled_capabilities = [
                entry for entry in entries
                if entry.enabled and getattr(entry.kind, "value", entry.kind) in {"skill", "code_small"}
            ]
            unsupported_enabled = [
                entry.extension_id for entry in entries
                if entry.enabled and getattr(entry.kind, "value", entry.kind) not in {"skill", "code_small"}
            ]
            if not enabled_capabilities:
                if unsupported_enabled:
                    return DiagnosticCheck(
                        check_id="extensions_registry",
                        category="extensions",
                        status=CheckStatus.INFO,
                        severity=CheckSeverity.LOW,
                        summary="Enabled extension readiness unknown",
                        evidence=(
                            f"Manifest contains {len(entries)} entries; runtime capability mapping is not "
                            f"defined for enabled kinds: {unsupported_enabled}."
                        ),
                    )
                return DiagnosticCheck(
                    check_id="extensions_registry",
                    category="extensions",
                    status=CheckStatus.OK,
                    severity=CheckSeverity.LOW,
                    summary=f"Extension manifest valid ({len(entries)} installed, {enabled_cnt} enabled)",
                    evidence="No enabled skill/code capability entries require runtime availability checks.",
                )

            try:
                capabilities = self._introspector.get_capabilities_info()
            except Exception as exc:
                capabilities = None
                capability_error = type(exc).__name__
            else:
                capability_error = None
            by_id = capabilities.get("by_id") if isinstance(capabilities, dict) else None
            if not isinstance(by_id, dict):
                # Enabled dependencies whose availability could not be read are
                # unproven, not fine: WARNING keeps them out of HEALTHY.
                return DiagnosticCheck(
                    check_id="extensions_registry",
                    category="extensions",
                    status=CheckStatus.WARNING,
                    severity=CheckSeverity.MEDIUM,
                    summary="Enabled extension availability unverified",
                    evidence=(
                        f"{len(enabled_capabilities)} enabled skill/code entry/entries exist, but the "
                        f"capability index could not be read{f' ({capability_error})' if capability_error else ''}. "
                        "Their availability is unproven."
                    ),
                )

            unavailable = []
            unknown = []
            for entry in enabled_capabilities:
                capability = by_id.get(entry.extension_id)
                if capability is None:
                    unavailable.append(entry.extension_id)
                    continue
                if not isinstance(capability, dict) or not isinstance(capability.get("available"), bool):
                    unknown.append(entry.extension_id)
                elif not capability["available"]:
                    unavailable.append(entry.extension_id)
            if unavailable:
                return DiagnosticCheck(
                    check_id="extensions_registry",
                    category="extensions",
                    status=CheckStatus.WARNING,
                    severity=CheckSeverity.MEDIUM,
                    summary=f"Enabled extension capability unavailable: {unavailable}",
                    evidence=(
                        f"Manifest lists {len(enabled_capabilities)} enabled skill/code capability entries; "
                        "one or more are missing from or unavailable in the runtime capability index."
                    ),
                )
            if unknown or unsupported_enabled:
                return DiagnosticCheck(
                    check_id="extensions_registry",
                    category="extensions",
                    status=CheckStatus.WARNING,
                    severity=CheckSeverity.MEDIUM,
                    summary="Enabled extension availability partially unverified",
                    evidence=(
                        f"AVAILABILITY-ONLY. Capability state unknown for {unknown or 'none'}; unsupported "
                        f"enabled kinds: {unsupported_enabled or 'none'}. At least one enabled extension is "
                        "neither confirmed available nor confirmed broken."
                    ),
                )
            return DiagnosticCheck(
                check_id="extensions_registry",
                category="extensions",
                status=CheckStatus.OK,
                severity=CheckSeverity.LOW,
                summary=f"Extension capabilities available ({len(enabled_capabilities)} enabled)",
                evidence=f"All enabled skill/code entries have available capability records; {len(entries)} total manifest entries.",
            )
        except Exception as e:
            return DiagnosticCheck(
                check_id="extensions_registry",
                category="extensions",
                status=CheckStatus.WARNING,
                severity=CheckSeverity.LOW,
                summary="Extension manifest verification warning",
                evidence=f"Encountered warning: {e}",
            )

    # -------------------------------------------------------------------------
    # Safe Healing & Self-Repair Engine
    # -------------------------------------------------------------------------

    def is_repair_circuit_broken(self, repair_id: str) -> bool:
        """Check if a repair has exceeded maximum failure threshold (circuit breaker)."""
        failures = self._repair_failures.get(repair_id, 0)
        return failures >= 3

    def record_repair_attempt(self, repair_id: str, success: bool) -> None:
        """Track repair attempts and failures for circuit-breaker management."""
        now = time.time()
        self._repair_attempts.setdefault(repair_id, []).append(now)
        if success:
            self._repair_failures[repair_id] = 0
        else:
            self._repair_failures[repair_id] = self._repair_failures.get(repair_id, 0) + 1

    def execute_repair(self, repair_id: str, approved: bool = False) -> Dict[str, Any]:
        """Execute safe automated repairs or gate consequential repairs with approval."""
        if self.is_repair_circuit_broken(repair_id):
            return {
                "success": False,
                "repair_id": repair_id,
                "message": (
                    f"Circuit breaker tripped for '{repair_id}': too many failed attempts (>=3). "
                    "Manual intervention required."
                ),
            }

        # Consequential / External repairs requiring explicit user approval
        consequential_repairs = {
            "repair_consequential_action",
            "repair_delete_workspace_cache",
            "repair_reset_configuration",
            "repair_install_dependency",
        }

        if repair_id in consequential_repairs and not approved:
            return {
                "success": False,
                "repair_id": repair_id,
                "message": (
                    f"Approval required: Repair '{repair_id}' involves consequential changes "
                    "and requires explicit confirmation."
                ),
                "requires_approval": True,
            }

        try:
            # 1. Stale leases cleanup
            if repair_id == "repair_stale_leases":
                from charlie.resource_locks import CapabilityLeaseManager, force_release
                from charlie.task_journal import TaskJournal

                journal = self._introspector._get_task_journal()
                manager = self._introspector._get_lease_manager()
                if not isinstance(journal, TaskJournal) or not isinstance(manager, CapabilityLeaseManager):
                    raise RuntimeError("Lease repair requires the owning runtime's journal and lease manager")
                active_ids = {task.id for task in journal.list(include_terminal=False)}
                stale = {
                    cap: owner for cap, owner in manager.snapshot().items()
                    if not is_live_lease_owner(owner, active_ids)
                }
                # Re-check liveness immediately before each drop: a capability can
                # be re-leased between the snapshot and the release. force_release
                # (not the cooperative refcount-aware release) is correct here --
                # a lease object surviving without a live owner shape or journal
                # task is the definition of a terminated holder.
                released = 0
                for capability, owner in stale.items():
                    if manager.current_owner(capability) != owner:
                        continue
                    if is_live_lease_owner(owner, active_ids):
                        continue
                    if force_release(capability, owner):
                        released += 1
                self.record_repair_attempt(repair_id, success=True)
                return {"success": True, "repair_id": repair_id, "message": f"Released {released} stale leases."}

            # 2. Refresh code index
            elif repair_id == "repair_refresh_code_index":
                from charlie.code_index import CodeIndex
                CodeIndex().refresh(force=True)
                self.record_repair_attempt(repair_id, success=True)
                return {"success": True, "repair_id": repair_id, "message": "CodeIndex refresh complete."}

            # 3. Unknown / simulated failing repair
            else:
                self.record_repair_attempt(repair_id, success=False)
                return {
                    "success": False,
                    "repair_id": repair_id,
                    "message": f"Repair handler '{repair_id}' completed with errors.",
                }

        except Exception as e:
            logger.error("Error executing repair %s: %s", repair_id, e)
            self.record_repair_attempt(repair_id, success=False)
            return {"success": False, "repair_id": repair_id, "message": f"Repair failed: {e}"}

    # -------------------------------------------------------------------------
    # Report formatting
    # -------------------------------------------------------------------------

    def format_report(self, report: DoctorReport) -> str:
        """Format a concise runtime health report."""
        lines = [
            "=" * 64,
            " CHARLIE DOCTOR — RUNTIME HEALTH DIAGNOSTICS",
            "=" * 64,
        ]

        for c in report.checks:
            icon = (
                "OK"
                if c.status == CheckStatus.OK
                else (
                    "WARN"
                    if c.status == CheckStatus.WARNING
                    else ("INFO" if c.status == CheckStatus.INFO else "FAIL")
                )
            )
            lines.append(f" [{icon:<4}] {c.check_id:<24} {c.summary}")
            lines.append(f"     Evidence: {c.evidence}")
            if c.fix_hint and c.status != CheckStatus.OK:
                lines.append(f"     Fix Hint: {c.fix_hint}")

        lines.append("-" * 64)
        status_text = report.status.value.upper()
        lines.append(
            f" Status: {status_text} | Total Checks: {report.total_checks} | "
            f"Warnings: {len(report.warnings)} | Errors: {len(report.errors)}"
        )
        lines.append("=" * 64)
        return "\n".join(lines)
