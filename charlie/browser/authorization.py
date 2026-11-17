"""Explicit, revocable authorization for sensitive browser operations.

This is deliberately separate from device standing consent: an always_allow
shell/screen rule must never authorize authenticated-site access or a state
changing submission. Grants are DPAPI-protected, scoped, expiring and audited.
Default is DENY. The CLI that issues a grant requires an explicit owner flag.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from charlie.utils import make_id

logger = logging.getLogger("charlie.browser.authorization")

# Scope -> what it permits. A grant lists one or more of these.
SCOPE_VISIBLE = "visible_session"     # use Charlie's persistent, logged-in browser profile
SCOPE_PRIVATE_DATA = "private_data"    # read content from an authenticated/private page
SCOPE_SENSITIVE_SUBMIT = "sensitive_submit"  # submit state-changing/payment/personal forms
ALL_SCOPES = frozenset({SCOPE_VISIBLE, SCOPE_PRIVATE_DATA, SCOPE_SENSITIVE_SUBMIT})

MAX_TTL_S = 3600
AUDIT_NAME = "browser-auth-audit.jsonl"


def grant_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "browser_grants.json"


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    from charlie.rakazo import _crypt

    return _crypt(data, protect=protect)


def _load() -> list[dict[str, Any]]:
    path = grant_path()
    if not path.exists():
        return []
    try:
        data = json.loads(_dpapi(path.read_bytes(), protect=False).decode("utf-8"))
        return [g for g in data if isinstance(g, dict)] if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("browser grants unreadable; failing closed (%s)", type(exc).__name__)
        return []


def _save(grants: list[dict[str, Any]]) -> None:
    path = grant_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(_dpapi(json.dumps(grants).encode("utf-8"), protect=True))
    tmp.replace(path)


def _audit(action: str, grant_id: str, domain: str, decision: bool, reason: str) -> None:
    path = Path(os.environ["LOCALAPPDATA"]) / "Charlie" / AUDIT_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": int(time.time()), "action": action, "grant_id": grant_id[:16],
               "domain": domain[:120], "allowed": decision, "reason": reason[:160]}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _host(url_or_domain: str) -> str:
    value = (url_or_domain or "").strip()
    if "://" in value:
        value = urlparse(value).hostname or ""
    return value.casefold().rstrip(".")


def _require_owner_console() -> None:
    """Grant issue must happen at a real interactive console, never from a shell tool.

    A general shell action runs with no attached console window. Requiring one
    keeps the gateway's `execute` path (and any other same-user process) from
    minting its own browser authority.
    """
    if sys.platform != "win32":
        if not sys.stdin.isatty():
            raise PermissionError("browser grants require an interactive owner console")
        return
    import ctypes
    if not ctypes.windll.kernel32.GetConsoleWindow():
        raise PermissionError("browser grants require an interactive owner console")
    if not sys.stdin.isatty():
        raise PermissionError("browser grants require an interactive owner console")


def issue(scope: list[str], *, domains: list[str], ttl_s: int = 600,
          granted_by: str = "owner") -> dict[str, Any]:
    """Create a grant only after a physical owner confirms at an interactive console."""
    _require_owner_console()
    scopes = sorted(set(str(s) for s in scope) & set(ALL_SCOPES))
    if not scopes:
        raise ValueError("no valid browser grant scope")
    clean_domains = sorted({_host(d) for d in domains if _host(d)})
    ttl = max(1, min(int(ttl_s), MAX_TTL_S))
    prompt = (f"Browser grant: scopes={','.join(scopes)} domains={','.join(clean_domains) or 'any'} "
              f"ttl={ttl}s. Type CONFIRM to issue: ")
    try:
        answer = input(prompt).strip()
    except (EOFError, OSError) as exc:
        raise PermissionError("owner confirmation could not be read") from exc
    if answer != "CONFIRM":
        _audit("issue", "", ",".join(clean_domains), False, "owner declined")
        raise PermissionError("owner did not confirm browser grant")
    grant = {
        "id": make_id(),
        "scopes": scopes,
        "domains": clean_domains,
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + ttl,
        "granted_by": granted_by[:64],
    }
    grants = _load() + [grant]
    _save(grants)
    _audit("issue", grant["id"], ",".join(clean_domains), True, f"scopes={scopes} ttl={ttl}")
    logger.info("browser grant issued | scopes=%s domains=%s ttl=%ds", scopes, clean_domains, ttl)
    return dict(grant)


def revoke(grant_id: str) -> bool:
    grants = _load()
    kept = [g for g in grants if str(g.get("id")) != grant_id]
    changed = len(kept) != len(grants)
    if changed:
        _save(kept)
        _audit("revoke", grant_id, "", True, "owner revoked")
    return changed


def list_grants() -> list[dict[str, Any]]:
    now = int(time.time())
    return [{k: g.get(k) for k in ("id", "scopes", "domains", "issued_at", "expires_at", "granted_by")}
            | {"active": bool(g.get("expires_at", 0) > now)}
            for g in _load()]


def authorize(grant_id: str, *, scope: str, url_or_domain: str = "") -> tuple[bool, str]:
    """Return (allowed, reason). Fails closed for missing/expired/wrong-scope/domain grants."""
    if not grant_id:
        _audit("authorize", "", url_or_domain, False, "no grant")
        return False, "explicit browser authorization required"
    domain = _host(url_or_domain)
    for grant in _load():
        if str(grant.get("id")) != grant_id:
            continue
        if int(grant.get("expires_at", 0)) <= int(time.time()):
            _audit("authorize", grant_id, domain, False, "expired")
            return False, "browser grant expired"
        if scope not in set(grant.get("scopes", [])):
            _audit("authorize", grant_id, domain, False, f"scope miss: {scope}")
            return False, f"browser grant does not cover {scope}"
        allowed_domains = set(grant.get("domains", []))
        if allowed_domains and domain and not any(
            domain == d or domain.endswith("." + d) for d in allowed_domains
        ):
            _audit("authorize", grant_id, domain, False, "domain miss")
            return False, "domain not covered by browser grant"
        _audit("authorize", grant_id, domain, True, f"scope={scope}")
        return True, f"authorized by browser grant ({scope})"
    _audit("authorize", grant_id, domain, False, "unknown grant")
    return False, "unknown browser grant"
