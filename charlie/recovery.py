import enum
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("charlie.recovery")

class FailureClass(enum.Enum):
    TIMEOUT = "TIMEOUT"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION = "PERMISSION"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    UNKNOWN = "UNKNOWN"

from charlie.config import config

system_root: str = config.system_root
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
    """Verifies that the recovery action is safe to execute."""
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

async def query_big_llm(brain: Any, command: str, failure: Dict[str, Any]) -> Optional[str]:
    """Queries the fallback LLM to dynamically suggest a command modification."""
    has_fallback = (
        brain._big_client
        and brain.config.big_llm_key
        and brain.config.big_llm_key not in ("no-key", "no_key")
    )
    if not has_fallback:
        logger.info("Big LLM not configured, skipping LLM recovery")
        return None

    prompt = (
        "You are the fallback error recovery engine for Charlie.\n"
        "A command failed with a runtime exception. Analyze the command and failure details, "
        "then propose a fixed version of the command that satisfies the original intent without the failure.\n\n"
        f"Original Command: {command}\n"
        f"Exception Type: {failure['error_class']}\n"
        f"Error Message: {failure['message']}\n"
        f"Failure Classification: {failure['failure_class'].value}\n"
        f"Current OS: {sys.platform}\n\n"
        "You MUST return a JSON object with the following schema:\n"
        "{\n"
        "    \"fixed_command\": \"the corrected command to run\",\n"
        "    \"explanation\": \"brief explanation of why the original failed and how this fixes it\"\n"
        "}\n"
        "Return ONLY the raw JSON object, no Markdown syntax, no thinking blocks, and no extra text."
    )

    try:
        payload = {
            "model": brain.config.big_llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        response = await brain._big_client.post(
            "chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {brain.config.big_llm_key}"}
        )
        if response.status_code == 200:
            res_data = response.json()
            content = res_data["choices"][0]["message"]["content"].strip()
            if "{" in content:
                content = content[content.find("{"):content.rfind("}")+1]
            data = json.loads(content)
            fixed_cmd = data.get("fixed_command")
            logger.info(
                "Fallback LLM suggested fixed command: %s (Explanation: %s)",
                fixed_cmd,
                data.get("explanation")
            )
            return fixed_cmd
    except Exception as exc:
        logger.exception("Fallback LLM query failed: %s", exc)
    return None

async def recover_tool(
    brain: Any,
    tool_name: str,
    arguments: Dict[str, Any],
    e: Exception
) -> Optional[str]:
    """Universal recovery coordinator. Tries cache, strategies, then fallback LLM.
    Returns the result of the successful recovery, or None if failed.
    """
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
        try:
            old_path = arguments.get("path", "")
            if old_path:
                home_dir = os.path.expanduser("~")
                docs_dir = os.path.join(home_dir, "Documents")

                # Extract file name and build new safe path in Documents
                file_name = os.path.basename(old_path)
                new_path = os.path.join(docs_dir, file_name)

                logger.info("Redirecting file_write from %s to safe path %s", old_path, new_path)

                # Execute the write tool with new safe path
                from charlie.tools import file_write
                res = file_write(new_path, arguments.get("content", ""))
                if not res.startswith("Error"):
                    return (
                        f"Redirected save: I couldn't write to the system folder due to "
                        f"permissions, so I saved the file to '{new_path}' instead."
                    )
        except Exception as redirect_exc:
            logger.warning("Failed to redirect file_write: %s", redirect_exc)

    # 2. Handle shell_execute (command recovery logic)
    if tool_name == "shell_execute":
        command = arguments.get("command", "")
        if not command:
            return None

        # Check local cache
        from charlie.recovery_cache import get_cached_resolution, set_cached_resolution
        cached_cmd = get_cached_resolution(command, failure_class.value, error_msg)
        if cached_cmd:
            if not is_safe_to_recover(cached_cmd):
                return "Error: Cached recovery command blocked by safety guardrails."
            try:
                logger.info("Executing cached recovery command: %s", cached_cmd)
                if failure_class == FailureClass.TIMEOUT:
                    subprocess.Popen(
                        f'start "" {cached_cmd}', shell=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
                        close_fds=True
                    )
                    return "Command succeeded (exit code 0). launched in background via cached recovery."

                res = run_command_safe(cached_cmd)
                if res.returncode == 0:
                    parts = []
                    if res.stdout:
                        parts.append(res.stdout.strip())
                    if res.stderr:
                        parts.append(res.stderr.strip())
                    return "\n".join(parts) if parts else "Command succeeded (exit code 0)."
            except Exception as cache_exc:
                logger.warning("Cached recovery command failed: %s", cache_exc)

        # Try strategies
        for strategy in RECOVERY_REGISTRY:
            if strategy.can_handle(failure):
                logger.info("Attempting strategy: %s", type(strategy).__name__)
                try:
                    res = await strategy.recover(command, failure)
                    if res.success and res.command:
                        if not is_safe_to_recover(res.command):
                            logger.warning("Strategy proposed unsafe command: %s", res.command)
                            continue

                        if type(strategy).__name__ == "DeclassProcessStrategy":
                            set_cached_resolution(command, failure_class.value, error_msg, res.command)
                            return res.message or "Command launched in background via recovery strategy."

                        exec_res = run_command_safe(res.command)
                        if exec_res.returncode == 0:
                            logger.info("Strategy %s succeeded!", type(strategy).__name__)
                            set_cached_resolution(command, failure_class.value, error_msg, res.command)
                            parts = []
                            if exec_res.stdout:
                                parts.append(exec_res.stdout.strip())
                            if exec_res.stderr:
                                parts.append(exec_res.stderr.strip())
                            return "\n".join(parts) if parts else "Command succeeded."
                except Exception as strat_exc:
                    logger.warning("Strategy execution failed: %s", strat_exc)

        # Fallback LLM query
        logger.info("All strategies exhausted. Querying big LLM for recovery command.")
        fixed_cmd = await query_big_llm(brain, command, failure)
        if fixed_cmd:
            if not is_safe_to_recover(fixed_cmd):
                return "Error: Fallback LLM suggested an unsafe command blocked by guardrails."
            try:
                logger.info("Executing fallback LLM command: %s", fixed_cmd)
                exec_res = run_command_safe(fixed_cmd)
                if exec_res.returncode == 0:
                    logger.info("Fallback LLM command execution succeeded!")
                    set_cached_resolution(command, failure_class.value, error_msg, fixed_cmd)
                    parts = []
                    if exec_res.stdout:
                        parts.append(exec_res.stdout.strip())
                    if exec_res.stderr:
                        parts.append(exec_res.stderr.strip())
                    return "\n".join(parts) if parts else "Command succeeded via fallback LLM correction."
            except Exception as llm_exc:
                logger.warning("Fallback LLM command execution failed: %s", llm_exc)


class BaseRecoveryStrategy:
    def can_handle(self, failure: Dict[str, Any]) -> bool:
        raise NotImplementedError()

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        raise NotImplementedError()

class DeclassProcessStrategy(BaseRecoveryStrategy):
    """Strategy for TIMEOUT: runs process detached via Popen."""
    def can_handle(self, failure: Dict[str, Any]) -> bool:
        return failure["failure_class"] == FailureClass.TIMEOUT

    async def recover(self, command: str, failure: Dict[str, Any]) -> RecoveryResult:
        try:
            logger.info("DeclassProcessStrategy: Retrying command detached: %s", command)
            full_cmd = f'start "" {command}'
            subprocess.Popen(
                full_cmd, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
                close_fds=True
            )
            return RecoveryResult(success=True, command=command, message="Process launched in background.")
        except Exception as e:
            return RecoveryResult(success=False, error=str(e))

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
