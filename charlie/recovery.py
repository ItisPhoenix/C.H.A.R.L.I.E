import contextvars
import enum
import logging
import os
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from charlie.core import ApprovalDecision

logger = logging.getLogger("charlie.recovery")

class FailureClass(enum.Enum):
    TIMEOUT = "TIMEOUT"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION = "PERMISSION"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    UNKNOWN = "UNKNOWN"

from charlie.config import config

system_root: str = config.system_root
# Guards the file_write redirect below: routing it through the canonical tool
# path re-enters recover_tool for the same tool name, which would otherwise
# recurse forever on the same basename. ContextVar, so it is per-task.
_RECOVERY_IN_PROGRESS: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "charlie_recovery_in_progress", default=False
)


_BLOCKED_RECOVERY_PATHS: List[str] = [
    system_root,
    os.path.join(system_root, "system32"),
    os.path.join(system_root, "syswow64")
]

_BLOCKED_RECOVERY_PROCESSES: List[str] = [
    "explorer.exe",
    "code.exe",
    "taskhostw.exe"
]

_BLOCKED_RECOVERY_PORTS: List[int] = [22, 80, 443]

def is_safe_to_recover(command: str) -> bool:
    """Verifies that the recovery action is safe to execute.

    Recovery commands come from an LLM suggestion or a rewrite strategy, not
    the user directly, so they must pass the same shell_execute guard
    (metacharacters + risky-keyword blocklist) in addition to the
    recovery-specific path/process/port checks below -- otherwise a
    recovery-suggested command could execute things shell_execute itself
    would refuse (e.g. "format", "del /f /s", or metacharacter injection).
    """
    from charlie.tools import is_shell_command_blocked

    blocked_reason = is_shell_command_blocked(command)
    if blocked_reason:
        logger.warning("Safety Guardrail: %s", blocked_reason)
        return False

    cmd_lower = command.lower().strip()
    for path in _BLOCKED_RECOVERY_PATHS:
        if path in cmd_lower:
            logger.warning("Safety Guardrail: Command mentions blocked path: %s", path)
            return False
    for proc in _BLOCKED_RECOVERY_PROCESSES:
        if proc in cmd_lower:
            logger.warning("Safety Guardrail: Command mentions blocked process: %s", proc)
            return False
    for port in _BLOCKED_RECOVERY_PORTS:
        if re.search(rf":{re.escape(str(port))}(?=\D|$)", cmd_lower):
            logger.warning("Safety Guardrail: Command mentions blocked port: %d", port)
            return False
    return True

class RecoveryResult:
    def __init__(
        self,
        success: bool,
        command: Optional[str] = None,
        message: Optional[str] = None,
        error: Optional[str] = None
    ):
        self.success = success
        self.command = command
        self.message = message
        self.error = error


class RecoveryOutcome(str):
    """One approved recovery, carrying the answer to "did anything run?".

    A ``str`` subclass, so the human-readable message contract callers already
    rely on is preserved, plus the one fact a caller must never have to infer
    from prose:

    ``executed``
        ``True`` only when recovery itself performed the work and ``result``
        holds the real output. An approved *proposal* is not an execution.
        Recovery locates or rewrites a command and asks the canonical owner for
        the decision; the tool layer stays the only place a command executes,
        and therefore stays the place that holds the capability lease, applies
        policy, and runs the semantic verifier. Executing here would be a
        second, unguarded execution path (charlie/AGENTS.md 1, 4).
    ``instruction``
        What the caller should do next -- e.g. the resolved command to retry.
    ``result``
        The real tool output. Present only when ``executed`` is ``True``.

    A caller must treat anything without ``executed is True`` as a proposal
    that changed nothing, so a recovery can never launder a failure into a
    success.
    """

    executed: bool
    instruction: str
    result: Any

    def __new__(
        cls,
        message: str,
        *,
        executed: bool,
        instruction: str = "",
        result: Any = None,
    ) -> "RecoveryOutcome":
        outcome = super().__new__(cls, message)
        outcome.executed = bool(executed)
        outcome.instruction = str(instruction or "")
        outcome.result = result
        return outcome


def _approved_retry_only(
    proposed_command: str,
    explanation: str,
    source: str,
) -> RecoveryOutcome:
    """Approved for a retry that recovery itself does not perform."""

    return RecoveryOutcome(
        explanation,
        executed=False,
        instruction=(
            f"{explanation} The {source} resolution did not run anything: retry "
            f"shell_execute with this command instead: {proposed_command}"
        ),
    )

def normalize_exception(e: Exception) -> Dict[str, Any]:
    """Standardizes Python / OS exceptions into unified schema."""
    error_class = type(e).__name__
    message = str(e)
    failure_class = FailureClass.UNKNOWN

    errno_val = getattr(e, "errno", None)
    winerror_val = getattr(e, "winerror", None)

    is_not_found = (
        isinstance(e, FileNotFoundError)
        or winerror_val == 2
        or "[winerror 2]" in message.lower()
    )
    is_permission = (
        isinstance(e, PermissionError)
        or winerror_val in (5, 32)
        or "[winerror 5]" in message.lower()
        or "[winerror 32]" in message.lower()
    )
    is_timeout = (
        isinstance(e, subprocess.TimeoutExpired)
        or "timeout" in error_class.lower()
    )
    is_resource = (
        isinstance(e, OSError)
        and (
            winerror_val == 10048
            or errno_val == 10048
            or "10048" in message
            or "wsaeaddrinuse" in message.lower()
        )
    )

    if is_not_found:
        failure_class = FailureClass.NOT_FOUND
    elif is_permission:
        failure_class = FailureClass.PERMISSION
    elif is_timeout:
        failure_class = FailureClass.TIMEOUT
    elif is_resource:
        failure_class = FailureClass.RESOURCE_LIMIT

    return {
        "error_class": error_class,
        "message": message,
        "failure_class": failure_class,
        "attempt_count": 1
    }

def run_command_safe(command: str) -> subprocess.CompletedProcess:
    """Executes a shell command synchronously with a standard timeout."""
    return subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=15.0
    )

_event_bus: Any = None

async def request_recovery_approval(
    brain: Any,
    original_command: str,
    proposed_command: str,
    failure_class: str,
    explanation: str,
    source: str,
    execution_context: Optional[Any] = None,
) -> "ApprovalDecision":
    """Take the canonical approval decision for one proposed recovery.

    ``Brain._request_tool_approval_decision`` returning
    ``charlie.core.ApprovalDecision`` is the single authoritative owner of the
    approval outcome: the same function gates ordinary tool calls in
    ``_execute_operation_primitive``. Recovery asks that owner instead of
    inventing a second approval protocol of its own.

    Every path that cannot reach the owner fails closed with
    ``ApprovalDecision.UNAVAILABLE``; this helper never derives a permissive
    outcome from a bool, from free text, or from the absence of an answer.
    Turn/task/session identity and the approval platform are deliberately left
    to the owner, which owns that context.
    """
    from charlie.core import ApprovalDecision

    if execution_context is not None and execution_context.cancellation_requested:
        logger.warning(
            "Recovery approval cancelled for source=%s command=%s",
            source,
            original_command,
        )
        return ApprovalDecision.UNAVAILABLE

    canonical = getattr(brain, "_request_tool_approval_decision", None)
    if canonical is None:
        logger.warning(
            "Recovery approval unavailable; no canonical decision owner is wired for source=%s "
            "command=%s. Failing closed.",
            source,
            original_command,
        )
        return ApprovalDecision.UNAVAILABLE

    try:
        decision = await canonical(
            "shell_execute",
            {"command": proposed_command},
            f"recovery ({source}) after a {failure_class} failure: {explanation}",
        )
    except Exception as approval_exc:
        logger.warning(
            "Canonical approval decision failed for source=%s command=%s: %s. Failing closed.",
            source,
            original_command,
            approval_exc,
        )
        return ApprovalDecision.UNAVAILABLE

    if not isinstance(decision, ApprovalDecision):
        logger.warning(
            "Canonical approval decision returned %s instead of ApprovalDecision for source=%s "
            "command=%s. Failing closed.",
            type(decision).__name__,
            source,
            original_command,
        )
        return ApprovalDecision.UNAVAILABLE

    return decision


async def recover_tool(
    brain: Any,
    tool_name: str,
    arguments: Dict[str, Any],
    e: Exception,
    *,
    execution_context: Optional[Any] = None,
) -> Optional[RecoveryOutcome]:
    """Universal recovery coordinator. Tries cache, then strategies.

    Every proposed recovery is gated on the canonical approval decision owned by
    ``Brain._request_tool_approval_decision``; recovery contributes the proposal,
    never the verdict. Returns a :class:`RecoveryOutcome` when a recovery was
    approved, or ``None`` when none was, so the caller reports the original
    failure truthfully.

    Approval is not execution. A ``RecoveryOutcome`` with ``executed=False``
    means the resolution is ready for the caller's next tool call and nothing
    was run; a caller must keep reporting the failure.
    """
    if execution_context is not None and execution_context.cancellation_requested:
        return None

    failure = normalize_exception(e)

    failure_class = failure["failure_class"]
    error_msg = failure["message"]

    logger.info(
        "Initiating recovery pipeline for tool %s (class %s): %s",
        tool_name,
        failure_class.value,
        error_msg
    )

    # 1. Handle file_write PermissionError/AccessDenied
    if tool_name == "file_write" and failure_class == FailureClass.PERMISSION:
        if execution_context is not None and execution_context.cancellation_requested:
            return None
        try:
            old_path = arguments.get("path", "")
            if old_path:
                home_dir = os.path.expanduser("~")
                docs_dir = os.path.join(home_dir, "Documents")

                # Extract file name and build new safe path in Documents
                file_name = os.path.basename(old_path)
                new_path = os.path.join(docs_dir, file_name)

                logger.info("Redirecting file_write from %s to safe path %s", old_path, new_path)

                # Route through the canonical tool path so the write is subject to
                # the registry policy layer, capability leases, and the approval
                # decision -- a recovery redirect must not be a privileged write.
                if _RECOVERY_IN_PROGRESS.get():
                    logger.warning(
                        "Recovery file_write redirect re-entered itself; refusing."
                    )
                    return None
                if brain is None or not hasattr(brain, "execute_tool_operation"):
                    logger.warning(
                        "No canonical tool owner available for the file_write "
                        "redirect; refusing rather than writing unapproved."
                    )
                    return None
                token = _RECOVERY_IN_PROGRESS.set(True)
                try:
                    outcome = await brain.execute_tool_operation(
                        "file_write",
                        {
                            "path": new_path,
                            "content": arguments.get("content", ""),
                        },
                    )
                finally:
                    _RECOVERY_IN_PROGRESS.reset(token)
                res = getattr(outcome, "result", None)
                status = getattr(outcome, "status", None)
                status_value = str(getattr(status, "value", status) or "")
                if status_value != "completed":
                    logger.warning(
                        "Canonical file_write redirect did not complete (status=%s); "
                        "reporting the failure.",
                        status_value or "unknown",
                    )
                    return None
                res = str(res or "")
                if not res.startswith("Error"):
                    return RecoveryOutcome(
                        f"Redirected save: I couldn't write to the system folder due to "
                        f"permissions, so I saved the file to '{new_path}' instead.",
                        executed=True,
                        result=res,
                        instruction=f"The file was saved to '{new_path}' instead.",
                    )
        except Exception as redirect_exc:
            logger.warning("Failed to redirect file_write: %s", redirect_exc)

    # 2. Handle shell_execute (command recovery logic)
    if tool_name == "shell_execute":
        from charlie.core import ApprovalDecision

        command = arguments.get("command", "")
        if not command:
            return None

        # Check local cache
        from charlie.recovery_cache import get_cached_resolution, set_cached_resolution
        cached_cmd = get_cached_resolution(command, failure_class.value, error_msg)
        if cached_cmd and not is_safe_to_recover(cached_cmd):
            logger.warning(
                "Safety Guardrail: cached recovery resolution is unsafe; refusing it. cached=%s",
                cached_cmd,
            )
            cached_cmd = None
        if cached_cmd:
            explanation = "Resolution retrieved from local command recovery cache."
            approval_decision = await request_recovery_approval(
                brain,
                original_command=command,
                proposed_command=cached_cmd,
                failure_class=failure_class.value,
                explanation=explanation,
                source="cache",
                execution_context=execution_context,
            )
            if approval_decision is ApprovalDecision.APPROVED:
                return _approved_retry_only(cached_cmd, explanation, "cache")
            logger.info(
                "Cached resolution was not approved (%s); trying strategies.",
                approval_decision.value,
            )

        # Try strategies
        for strategy in RECOVERY_REGISTRY:
            if strategy.can_handle(failure):
                logger.info("Attempting strategy: %s", type(strategy).__name__)
                try:
                    res = await strategy.recover(command, failure)
                    if execution_context is not None and execution_context.cancellation_requested:
                        return None
                    if res.success and res.command:
                        if not is_safe_to_recover(res.command):
                            logger.warning(
                                "Safety Guardrail: recovery strategy %s proposed an unsafe "
                                "command; refusing it. proposed=%s",
                                type(strategy).__name__,
                                res.command,
                            )
                            continue
                        if res.command == command:
                            logger.info(
                                "Skipping automatic retry of unchanged command after %s; "
                                "the original outcome may be uncertain.",
                                failure_class.value,
                            )
                            continue
                        explanation = (
                            res.message or
                            f"Recovery strategy {type(strategy).__name__} resolved command executable."
                        )
                        approval_decision = await request_recovery_approval(
                            brain,
                            original_command=command,
                            proposed_command=res.command,
                            failure_class=failure_class.value,
                            explanation=explanation,
                            source="strategy",
                            execution_context=execution_context,
                        )
                        if approval_decision is ApprovalDecision.APPROVED:
                            set_cached_resolution(command, failure_class.value, error_msg, res.command)
                            return _approved_retry_only(res.command, explanation, "strategy")
                except Exception as strat_exc:
                    logger.warning("Strategy execution failed: %s", strat_exc)

        logger.info("All recovery strategies exhausted.")

        return None


class BaseRecoveryStrategy:
    def can_handle(self, failure: Dict[str, Any]) -> bool:
        raise NotImplementedError()

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        raise NotImplementedError()

class DeclassProcessStrategy(BaseRecoveryStrategy):
    """Recognize timeouts without replaying a command whose outcome is uncertain."""
    def can_handle(self, failure: Dict[str, Any]) -> bool:
        return failure["failure_class"] == FailureClass.TIMEOUT

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        return RecoveryResult(success=False, error="Automatic retry disabled after command timeout")

class SystemPathSearchStrategy(BaseRecoveryStrategy):
    """Strategy for NOT_FOUND: searches PATH, registry and standard program folders."""
    def can_handle(self, failure: Dict[str, Any]) -> bool:
        return failure["failure_class"] == FailureClass.NOT_FOUND

    def _search_windows_registry(self, app_name: str) -> Optional[str]:
        if sys.platform != "win32":
            return None
        try:
            import winreg
            # Search App Paths registry key
            key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app_name}.exe"
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    return val
            except OSError:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    val, _ = winreg.QueryValueEx(key, "")
                    return val
        except Exception as e:
            logger.debug("Registry lookup failed for %s: %s", app_name, e)
        return None

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        parts = command.split()
        if not parts:
            return RecoveryResult(success=False, error="Empty command")

        executable = parts[0]
        # Already absolute? Skip
        if os.path.isabs(executable):
            return RecoveryResult(success=False, error="Already absolute path")

        # 1. Search PATH
        found_path = shutil.which(executable)
        if found_path:
            new_command = " ".join([found_path] + parts[1:])
            return RecoveryResult(success=True, command=new_command, message=f"Found in PATH: {found_path}")

        # 2. Search Windows Registry
        reg_path = self._search_windows_registry(executable)
        if reg_path and os.path.exists(reg_path):
            new_command = " ".join([f'"{reg_path}"'] + parts[1:])
            return RecoveryResult(success=True, command=new_command, message=f"Found in Registry: {reg_path}")

        # 3. Search common system folders
        common_dirs = []
        if sys.platform == "win32":
            pf = config.program_files
            pf86 = config.program_files_x86
            windir = config.system_root
            common_dirs.extend([
                windir,
                os.path.join(windir, "System32"),
                pf,
                pf86
            ])

        for d in common_dirs:
            ext = executable if executable.endswith(".exe") else f"{executable}.exe"
            target = os.path.join(d, ext)
            if os.path.exists(target):
                new_command = " ".join([f'"{target}"'] + parts[1:])
                return RecoveryResult(
                    success=True,
                    command=new_command,
                    message=f"Found in system folder: {target}"
                )

        return RecoveryResult(success=False, error="Binary not found in search paths")

class RelativePathResolveStrategy(BaseRecoveryStrategy):
    """Strategy for NOT_FOUND: resolves relative file paths referenced in the command."""
    def can_handle(self, failure: Dict[str, Any]) -> bool:
        return failure["failure_class"] == FailureClass.NOT_FOUND

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        parts = command.split()
        if not parts:
            return RecoveryResult(success=False, error="Empty command")

        executable = parts[0]
        if os.path.exists(executable):
            resolved = os.path.abspath(executable)
            new_command = " ".join([f'"{resolved}"'] + parts[1:])
            return RecoveryResult(
                success=True,
                command=new_command,
                message=f"Resolved relative path to absolute: {resolved}"
            )
        return RecoveryResult(success=False, error="Relative file path does not exist")

RECOVERY_REGISTRY: List[BaseRecoveryStrategy] = [
    DeclassProcessStrategy(),
    SystemPathSearchStrategy(),
    RelativePathResolveStrategy()
]
