"""Centralized Safety_Guard — input-safety policy for all tools (design §J, Reqs 12, 13).

This module provides path-boundary enforcement, SSRF prevention, volume clamping,
and dangerous-file-type detection. It is invoked by the tool catalog and individual
tool modules to enforce safety regardless of registration path.
"""

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from charlie.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Allowed roots for path-boundary checks (mirrors Guardian.TRUSTED_PATHS)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)

_user_home = os.path.expanduser("~")
_desktop = os.path.join(_user_home, "Desktop")
_documents = os.path.join(_user_home, "Documents")
_downloads = os.path.join(_user_home, "Downloads")

if os.name == "nt":
    try:
        import winreg

        _shell_folders = (
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _shell_folders) as _key:
            _desktop = os.path.expandvars(winreg.QueryValueEx(_key, "Desktop")[0])
            _documents = os.path.expandvars(winreg.QueryValueEx(_key, "Personal")[0])
            _downloads = os.path.expandvars(
                winreg.QueryValueEx(_key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
            )
    except Exception:
        pass  # Fall back to defaults

_DEFAULT_ALLOWED_ROOTS: list[str] = [
    os.path.realpath(_PROJECT_ROOT),
    os.path.realpath(_desktop),
    os.path.realpath(_documents),
    os.path.realpath(_downloads),
]

# ---------------------------------------------------------------------------
# Dangerous file extensions (Req 13.3)
# ---------------------------------------------------------------------------

_DANGEROUS_EXTENSIONS: set[str] = {".lnk", ".url", ".pif", ".hta"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_path_boundary(
    path: str, allowed_roots: list[str] | None = None
) -> tuple[bool, str]:
    """Verify that *path* resolves within an allowed root (Req 12).

    Canonicalizes with ``os.path.realpath`` (resolves ``..``, symlinks). On
    Windows, additionally attempts to resolve 8.3 short names via
    ``GetLongPathNameW`` so that e.g. ``C:\\PROGRA~1`` is expanded before the
    boundary check.

    Returns ``(True, "")`` when allowed, or ``(False, message)`` when the path
    falls outside all permitted boundaries.
    """
    # Canonicalize the path
    canonical = os.path.realpath(path)

    # On Windows, resolve 8.3 short names to long names
    if os.name == "nt":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(512)
            result = ctypes.windll.kernel32.GetLongPathNameW(canonical, buf, 512)
            if result > 0:
                canonical = buf.value
        except Exception:
            pass  # If resolution fails, proceed with realpath result

    roots = allowed_roots if allowed_roots is not None else _DEFAULT_ALLOWED_ROOTS

    # Normalize for comparison (case-insensitive on Windows)
    if os.name == "nt":
        canonical_lower = canonical.lower()
        for root in roots:
            root_canonical = os.path.realpath(root).lower()
            if canonical_lower.startswith(root_canonical):
                # Ensure it's a proper prefix (not just a substring of a longer name)
                remainder = canonical_lower[len(root_canonical):]
                if remainder == "" or remainder.startswith(os.sep) or remainder.startswith("/"):
                    return (True, "")
    else:
        for root in roots:
            root_canonical = os.path.realpath(root)
            if canonical.startswith(root_canonical):
                remainder = canonical[len(root_canonical):]
                if remainder == "" or remainder.startswith(os.sep):
                    return (True, "")

    return (False, f"Path '{path}' is outside permitted boundaries.")


def check_ssrf(url: str) -> tuple[bool, str]:
    """SSRF guard — reject URLs that resolve to private/loopback/reserved IPs (Req 13.1).

    Resolves the host via ``socket.getaddrinfo`` and checks each resolved IP
    with ``ipaddress.ip_address``. If ANY resolved IP is disallowed, the URL is
    rejected.

    Returns ``(True, "")`` for public URLs, or ``(False, message)`` for
    restricted addresses.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return (False, f"URL '{url}' uses a disallowed scheme.")

        host = parsed.hostname
        if not host:
            return (False, f"URL '{url}' has no hostname.")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # Resolve the host to IP addresses
        try:
            addr_infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            return (False, f"URL '{url}' could not be resolved.")

        for addr_info in addr_infos:
            ip_str = addr_info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                logger.warning(
                    "ssrf_blocked | url=%s | resolved_ip=%s", url, ip_str
                )
                return (False, f"URL '{url}' resolves to a restricted address.")

        return (True, "")
    except Exception as e:
        logger.error("ssrf_check_error | url=%s | error=%s", url, e)
        return (False, f"URL '{url}' failed SSRF validation: {e}")


def clamp_volume(level: int | float) -> int:
    """Clamp a volume level to the valid [0, 100] range (Req 13.2)."""
    return max(0, min(100, int(level)))


def check_dangerous_file_type(path: str) -> bool:
    """Return True if the path's extension is a dangerous file type (Req 13.3).

    Dangerous types: .lnk, .url, .pif, .hta — these can execute arbitrary
    code when opened and require explicit confirmation before launch.
    """
    ext = os.path.splitext(path)[1].lower()
    return ext in _DANGEROUS_EXTENSIONS
