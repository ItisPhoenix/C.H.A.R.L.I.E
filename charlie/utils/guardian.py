from __future__ import annotations

import difflib
import os
import re
import time
from typing import Any, Callable, Optional

from charlie.security.tiers import CONFIRMATION_PENDING, RiskTier, get_tool_tier
from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class Guardian:
    def __init__(self) -> None:
        # Establish BASE_ROOT relative to this file's location (charlie/utils/guardian.py -> project root)
        self.BASE_ROOT = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

        # User-centric trusted paths (Localized via environment)
        user_home = os.path.expanduser("~")

        # Determine localized Desktop/Documents/Downloads via known environment vars or registry fallback
        desktop = os.path.join(user_home, "Desktop")  # Default fallback
        documents = os.path.join(user_home, "Documents")
        downloads = os.path.join(user_home, "Downloads")

        # Windows-specific dynamic lookup
        if os.name == "nt":
            try:
                import winreg

                shell_folders = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, shell_folders) as key:
                    desktop = winreg.QueryValueEx(key, "Desktop")[0]
                    documents = winreg.QueryValueEx(key, "Personal")[0]
                    downloads = winreg.QueryValueEx(
                        key, "{374DE290-123F-4565-9164-39C4925E467B}"
                    )[0]

                    # Expand environment variables in registry strings (e.g. %USERPROFILE%\Desktop)
                    desktop = os.path.expandvars(desktop)
                    documents = os.path.expandvars(documents)
                    downloads = os.path.expandvars(downloads)
            except Exception:
                logger.debug("guardian_win_registry_lookup_failed_using_defaults")

        self.TRUSTED_PATHS = [
            self.BASE_ROOT.lower(),
            desktop.lower(),
            documents.lower(),
            downloads.lower(),
        ]

        # Strict Allowlist for run_command
        self.allowlist_commands = [
            r"^git(\s+(status|diff|log|branch|checkout|pull|push))?$",
            r"^uv\s+run\s+(ruff\s+(check|format(\s+--check)?)\s+\S+|pytest\s+.*)$",
            r"^notepad(\s+.*)?$",
            r"^calc(\s+.*)?$",
            r"^explorer(\s+.*)?$",
            r"^chrome$",
            r"^msedge$",
            r"^code(\s+.*)?$",
            r"^taskmgr(\s+.*)?$",
            r"^control(\s+.*)?$",
            r"^mspaint(\s+.*)?$",
            r"^write(\s+.*)?$",
            r"^winword(\s+.*)?$",
            r"^excel(\s+.*)?$",
            r"^powerpnt(\s+.*)?$",
            r"^(stop|kill|terminate)(\s+(notepad|calc|chrome|msedge|code|mspaint|winword|excel|powerpnt))(\.exe)?$",
        ]

        self.sensitive_patterns = [
            r"\.env",
            r"\.key",
            r"id_rsa",
            r"shadow",
            r"passwd",
            r"credentials",
            r"config\.json",
            r"master\.key",
            re.escape(os.environ.get("WINDIR", "C:\\Windows")),
            re.escape(os.environ.get("APPDATA", "AppData")),
        ]

        # Rate limiting to prevent tool-loops
        self.last_tool_calls = {}
        self.rate_limit_seconds = 2.0

    def verify_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        sir_input: str,
        tool_func: Optional[Callable] = None,
    ) -> tuple[bool | str, str]:
        """Main security gate for tool execution."""
        # Get tier (default to TIER 1 for safety if func not provided)
        tier = get_tool_tier(tool_func) if tool_func else RiskTier.TIER_1

        # Mask sensitive data before any processing or logging
        masked_args = self.mask_sensitive_data(str(args))
        logger.info("guardian_verifying | tool=%s | args=%s", tool_name, masked_args)

        # 1. Rate Limit Check
        current_time = time.time()
        if tool_name in self.last_tool_calls:
            elapsed = current_time - self.last_tool_calls[tool_name]
            if elapsed < self.rate_limit_seconds:
                # Destructive tools have stricter limits
                if tool_name in ["kill_process", "write_file", "run_command"]:
                    return (
                        False,
                        f"Guardian: Tool '{tool_name}' is being called too rapidly. Action throttled.",
                    )

        self.last_tool_calls[tool_name] = current_time

        # 2. Command Allowlist Check
        if tool_name == "run_command":
            cmd = (args.get("cmd") or args.get("command") or "").strip()
            if not cmd:
                return False, "Guardian: Command cannot be empty."

            import shlex
            try:
                tokens = shlex.split(cmd)
            except Exception:
                return False, "Guardian: Command contains syntax or quoting errors."

            if not tokens:
                return False, "Guardian: Command contains no tokens."

            allowed_utilities = {"git", "uv", "pytest", "python", "notepad", "calc", "explorer", "chrome", "msedge", "code"}
            base_util = tokens[0].lower()
            if base_util.endswith(".exe"):
                base_util = base_util[:-4]

            if base_util not in allowed_utilities:
                logger.warning("guardian_blocked_unauthorized_cmd: %s", cmd)
                return (
                    False,
                    f"Guardian: Command utility '{tokens[0]}' is not in the safe allowlist.",
                )

            # High-risk command verbs require verbal confirmation (word boundary match)
            import re as _re
            if any(_re.search(r'\b' + _re.escape(verb) + r'\b', cmd.lower()) for verb in ["kill", "stop", "terminate"]):
                return (
                    CONFIRMATION_PENDING,
                    f"Sir, I require your confirmation to terminate '{cmd.split()[-1]}'.",
                )

        # 3. Path Security
        if tool_name in ["read_file", "write_file", "save_report"]:
            path = str(args.get("path", "")).lower()
            if not path:
                return False, "Guardian: Path cannot be empty."

            abs_path = os.path.abspath(path).lower()
            is_trusted = any(
                abs_path.startswith(trusted) for trusted in self.TRUSTED_PATHS
            )

            if not is_trusted:
                logger.warning("guardian_blocked_out_of_bounds_file: %s", abs_path)
                return (
                    False,
                    "Guardian: File access restricted to local project and standard user folders for safety.",
                )

            # Sensitive file patterns check
            for pattern in self.sensitive_patterns:
                if re.search(pattern, abs_path):
                    logger.warning("guardian_blocked_sensitive_access: %s", pattern)
                    return (
                        False,
                        f"Guardian: Access to sensitive path pattern ({pattern}) is restricted.",
                    )

            # AST verification on python files
            if path.endswith((".py", ".pending")):
                code_content = args.get("content") or args.get("code") or args.get("text") or ""
                if code_content:
                    valid, reason = self.verify_python_ast(code_content)
                    if not valid:
                        logger.warning("guardian_blocked_ast_failure: %s | reason=%s", path, reason)
                        return False, reason

            # File overwriting requires confirmation
            if tool_name == "write_file" and os.path.exists(abs_path):
                # Self-modification exception: if writing within 'charlie/' and confirmed as TIER 1
                if abs_path.startswith(os.path.join(self.BASE_ROOT, "charlie").lower()):
                    # Prevent writing to sensitive files
                    if any(
                        x in abs_path
                        for x in [".git", "pyproject.toml", "settings.toml"]
                    ):
                        return (
                            False,
                            f"Guardian: Modification of '{os.path.basename(abs_path)}' is strictly restricted.",
                        )
                    return (
                        CONFIRMATION_PENDING,
                        f"Sir, I require your confirmation to modify internal file '{os.path.basename(abs_path)}'.",
                    )
                return (
                    CONFIRMATION_PENDING,
                    f"Sir, the file '{os.path.basename(abs_path)}' already exists. Shall I overwrite it?",
                )

        if tool_name == "apply_edit":
            path = args.get("path")
            new_content = args.get("content", "")
            full_path = os.path.join(self.BASE_ROOT, path)

            # Protection (match path components, not substrings)
            parts = path.replace("\\", "/").split("/")
            basename = parts[-1] if parts else path
            if ".git" in parts or basename in ("pyproject.toml", "settings.toml"):
                return (
                    False,
                    f"Guardian: Modification of '{path}' is strictly restricted.",
                )

            # AST verification on python files
            if path.endswith((".py", ".pending")):
                if new_content:
                    valid, reason = self.verify_python_ast(new_content)
                    if not valid:
                        logger.warning("guardian_blocked_ast_failure: %s | reason=%s", path, reason)
                        return False, reason

            # Generate diff for confirmation
            old_content = ""
            if os.path.exists(full_path):
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    old_content = f.read()

            diff = "".join(
                difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
            )
            args["diff"] = diff  # Inject diff back into args for visibility

            return (
                CONFIRMATION_PENDING,
                f"Sir, I have prepared an edit for '{path}'. Review the diff.",
            )

        # 4. Protected Processes
        if tool_name == "kill_process":
            target = str(args.get("name", "")).lower()
            protected = [
                "explorer",
                "system",
                "svchost",
                "charlie",
                "python",
                "main.py",
                "watchdog.py",
            ]
            if any(p in target for p in protected):
                logger.warning("guardian_blocked_protected_process_kill: %s", target)
                return (
                    False,
                    f"Guardian: Terminating protected system process '{target}' is strictly forbidden.",
                )

            return (
                CONFIRMATION_PENDING,
                f"Are you certain you wish to terminate the '{target}' process, Sir?",
            )

        # 5. Suspicious Intent Check (word boundary matching to avoid false positives)
        malicious_keywords = ["delete all", "wipe", "spy", "steal", "hack into"]
        if any(re.search(r'\b' + re.escape(kw) + r'\b', sir_input.lower()) for kw in malicious_keywords):
            logger.warning("guardian_suspicious_intent: %s", sir_input)
            return (
                False,
                "Guardian: High-risk intent detected. Action blocked for security review.",
            )

        # 6. Tier-based routing
        if tier == RiskTier.TIER_1:
            from charlie.config import settings

            if not getattr(settings.security, "require_confirmation_tier1", True):
                logger.info("guardian_tier1_bypass_per_settings | tool=%s", tool_name)
                return True, "Verified"

            return (
                CONFIRMATION_PENDING,
                f"Sir, I require your confirmation to execute '{tool_name}'.",
            )

        if tier == RiskTier.TIER_2:
            return (
                CONFIRMATION_PENDING,
                f"Sir, this is a high-risk operation ({tool_name}). Confirm execution?",
            )

        if tier == RiskTier.TIER_3:
            return (
                CONFIRMATION_PENDING,
                f"Sir, this is a DESTRUCTIVE action ({tool_name}). Please type 'CONFIRM {tool_name.upper()}' to proceed.",
            )

        return True, "Verified"

    def mask_sensitive_data(self, text: str) -> str:
        """Redacts potential keys/passwords from strings."""
        text = re.sub(
            r'(?i)(api[_-]?key|token|password|secret|pwd)["\s:=]+[a-zA-Z0-9_\-\.]{12,}',
            r"\1: [REDACTED]",
            text,
        )
        return text

    def verify_python_ast(self, code: str) -> tuple[bool, str]:
        """Scan python source code to block dangerous subprocess or network socket imports."""
        try:
            import ast
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.lower() in ("subprocess", "socket", "ctypes"):
                            return False, f"Guardian: Dangerous import '{alias.name}' detected in written script. Operation blocked."
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.lower() in ("subprocess", "socket", "ctypes"):
                        return False, f"Guardian: Dangerous module import '{node.module}' detected in written script. Operation blocked."
            return True, "AST Verified"
        except SyntaxError as se:
            logger.warning("ast_parse_syntax_error | %s", se)
            return False, f"Guardian: Code has syntax errors and cannot be verified: {se}"
        except Exception as e:
            logger.error("ast_verification_failed | %s", e)
            return False, f"Guardian: AST inspection failure: {e}"
