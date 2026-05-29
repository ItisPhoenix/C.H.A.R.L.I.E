"""
charlie/utils/command_validator.py

Shared command validation for shell execution.
Blocks dangerous patterns before subprocess calls.
"""

import logging
import re

logger = logging.getLogger("charlie.utils.command_validator")

# Patterns that are always blocked
BLOCKED_PATTERNS = [
    (re.compile(r'\brm\s+(-[a-z]*f[a-z]*\s+)?/(\s|$)', re.IGNORECASE), "Recursive root delete"),
    (re.compile(r'\brmdir\s+/s\s+/q', re.IGNORECASE), "Recursive directory delete"),
    (re.compile(r'\bformat\s+[a-z]:', re.IGNORECASE), "Disk format"),
    (re.compile(r'\bshutdown\b', re.IGNORECASE), "System shutdown"),
    (re.compile(r'\breboot\b', re.IGNORECASE), "System reboot"),
    (re.compile(r'>\s*/dev/', re.IGNORECASE), "Device write"),
    (re.compile(r'\|\s*(ba)?sh\b', re.IGNORECASE), "Pipe to shell"),
    (re.compile(r'\|\s*cmd\b', re.IGNORECASE), "Pipe to cmd"),
    (re.compile(r'\bmkfs\b', re.IGNORECASE), "Filesystem format"),
    (re.compile(r'\bdd\s+if=', re.IGNORECASE), "Raw disk write"),
    (re.compile(r'\bchmod\s+777', re.IGNORECASE), "World-writable permissions"),
    (re.compile(r'\bcurl\b.*\|\s*(ba)?sh', re.IGNORECASE), "Curl pipe to shell"),
    (re.compile(r'\bwget\b.*\|\s*(ba)?sh', re.IGNORECASE), "Wget pipe to shell"),
    (re.compile(r';\s*rm\s+', re.IGNORECASE), "Chained delete"),
    (re.compile(r'&&\s*rm\s+', re.IGNORECASE), "Chained delete"),
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
