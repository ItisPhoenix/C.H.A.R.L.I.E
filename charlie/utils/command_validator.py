"""
charlie/utils/command_validator.py

Shared command validation for shell execution.
Blocks dangerous patterns before subprocess calls.

The validator is a *defense-in-depth* layer; the real boundary is
``RiskGate`` (TIER_2/3) which requires explicit user approval before
``shell=True`` is even reached. The patterns here are tuned to catch
common bypass vectors against naive blacklists: backticks, ``$()``,
newlines, ``~`` expansion, and chained deletes that don't lead with
``rm`` (e.g. ``mv /etc/foo .`` or ``chmod -R 777``).
"""

import logging
import re

logger = logging.getLogger("charlie.utils.command_validator")

# Patterns that are always blocked. Each entry is (compiled_regex, human reason).
BLOCKED_PATTERNS = [
    # Destructive disk / system operations
    (re.compile(r"\brm\s+(-[a-z]*f[a-z]*\s+)?/(\s|$)", re.IGNORECASE), "Recursive root delete"),
    (re.compile(r"\brm\s+(-[a-z]*f[a-z]*\s+)?~/", re.IGNORECASE), "Recursive home delete"),
    (re.compile(r"\brm\s+--no-preserve-root", re.IGNORECASE), "Recursive root delete (--no-preserve-root)"),
    (re.compile(r"\brmdir\s+/s\s+/q", re.IGNORECASE), "Recursive directory delete"),
    (re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE), "Disk format"),
    (re.compile(r"\bmkfs\b", re.IGNORECASE), "Filesystem format"),
    (re.compile(r"\bdd\s+if=", re.IGNORECASE), "Raw disk write"),
    (re.compile(r">\s*/dev/", re.IGNORECASE), "Device write"),
    (re.compile(r"\b(chmod|chown)\s+(-[a-z]*R[a-z]*\s+)?777", re.IGNORECASE), "World-writable recursive permissions"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b", re.IGNORECASE), "System shutdown/reboot"),
    # Code execution / shell escape
    (re.compile(r"\|\s*(ba)?sh\b", re.IGNORECASE), "Pipe to shell"),
    (re.compile(r"\|\s*cmd\b", re.IGNORECASE), "Pipe to cmd"),
    (re.compile(r"\|\s*powershell", re.IGNORECASE), "Pipe to powershell"),
    (re.compile(r"\bcurl\b.*\|\s*(ba)?sh", re.IGNORECASE), "Curl pipe to shell"),
    (re.compile(r"\bwget\b.*\|\s*(ba)?sh", re.IGNORECASE), "Wget pipe to shell"),
    (re.compile(r"\b(curl|wget)\b[^\n]*\b-O\s+/\S+\s*;\s*chmod\s+\+x", re.IGNORECASE), "Download-then-execute"),
    (re.compile(r"`", re.IGNORECASE), "Backtick command substitution"),
    (re.compile(r"\$\(\s*rm\b", re.IGNORECASE), "Command substitution running rm"),
    (re.compile(r"\$\(\s*(curl|wget)\b", re.IGNORECASE), "Command substitution fetching remote code"),
    (re.compile(r"\beval\b", re.IGNORECASE), "eval (dynamic code execution)"),
    (re.compile(r"\bexec\b", re.IGNORECASE), "exec (process replacement)"),
    # Chained destructive file ops (the *previous* list only chained ``rm``)
    (re.compile(r"[;&|]\s*rm\s+", re.IGNORECASE), "Chained delete"),
    (re.compile(r"[;&|]\s*mv\s+", re.IGNORECASE), "Chained move"),
    (re.compile(r"[;&|]\s*cp\s+", re.IGNORECASE), "Chained copy"),
    (re.compile(r"[;&|]\s*chmod\b", re.IGNORECASE), "Chained chmod"),
    (re.compile(r"[;&|]\s*chown\b", re.IGNORECASE), "Chained chown"),
    (re.compile(r"[;&|]\s*dd\b", re.IGNORECASE), "Chained dd"),
    (re.compile(r"\bsudo\b", re.IGNORECASE), "sudo (privilege elevation)"),
    (re.compile(r"^\s*su\s+", re.IGNORECASE), "su (privilege elevation)"),
    # Newline-as-separator: blocks payloads like
    #   git status\n rm -rf /
    (re.compile(r"\n", re.IGNORECASE), "Newline in command (separator injection)"),
    (re.compile(r"\r", re.IGNORECASE), "Carriage return in command"),
]


def validate_command(command: str) -> str:
    """Validate a shell command for dangerous patterns.

    Args:
        command: The command string to validate.

    Returns:
        The command string if safe.

    Raises:
        ValueError: If a dangerous pattern is detected.
    """
    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(command):
            logger.warning(f"blocked_command | reason={reason} | command={command[:100]}")
            raise ValueError(f"Command blocked: {reason}. Command: {command[:80]}...")

    logger.info(f"command_validated | command={command[:200]}")
    return command
