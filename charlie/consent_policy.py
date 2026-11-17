"""Owner-controlled, durable, revocable device consent policy.

This is NOT an authentication bypass. It is explicit standing authority granted
by the owner, stored per-user via DPAPI, scoped, audited, and instantly revocable.
Default remains DENY. Nothing here elevates privileges or touches credentials.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("charlie.consent_policy")

REQUIRED_KEYS = ("mode", "granted_by", "granted_at", "scopes", "version")
MODE_DENY = "deny"
MODE_BOUNDED = "bounded"
MODE_STANDING = "standing"


def consent_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "consent.json"


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    from charlie.rakazo import _crypt

    return _crypt(data, protect=protect)


def load_policy() -> dict:
    path = consent_path()
    if not path.exists():
        return {"mode": MODE_DENY, "granted_by": "", "granted_at": 0,
                "scopes": [], "version": 1}
    try:
        data = json.loads(_dpapi(path.read_bytes(), protect=False).decode("utf-8"))
        if not isinstance(data, dict) or not all(k in data for k in REQUIRED_KEYS):
            return {"mode": MODE_DENY, "granted_by": "", "granted_at": 0,
                    "scopes": [], "version": 1}
        return data
    except Exception as exc:
        logger.warning("consent policy unreadable; failing closed (%s)", type(exc).__name__)
        return {"mode": MODE_DENY, "granted_by": "", "granted_at": 0,
                "scopes": [], "version": 1}


def save_policy(policy: dict) -> None:
    path = consent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(_dpapi(json.dumps(policy).encode("utf-8"), protect=True))
    tmp.replace(path)


def set_standing(scopes: list[str], *, granted_by: str = "owner") -> dict:
    """Enable standing consent for explicit scopes. Owner action only."""
    policy = {
        "mode": MODE_STANDING,
        "granted_by": granted_by,
        "granted_at": int(time.time()),
        "scopes": sorted(set(str(s) for s in scopes)),
        "version": 1,
    }
    save_policy(policy)
    logger.info("standing consent enabled | scopes=%s by=%s", policy["scopes"], granted_by)
    return policy


def set_bounded(scopes: list[str], *, granted_by: str = "owner") -> dict:
    policy = {
        "mode": MODE_BOUNDED,
        "granted_by": granted_by,
        "granted_at": int(time.time()),
        "scopes": sorted(set(str(s) for s in scopes)),
        "version": 1,
    }
    save_policy(policy)
    return policy


def revoke() -> None:
    """Instantly revoke standing consent. Next request fails closed."""
    path = consent_path()
    if path.exists():
        path.unlink()
    logger.info("device consent revoked")


def audit_decision(request: dict, allowed: bool, reason: str) -> None:
    path = Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "consent-audit.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": int(time.time()), "method": request.get("method"),
                  "bot_id_len": len(str(request.get("botId", ""))),
                  "run_id_len": len(str(request.get("runId", ""))),
                  "allowed": allowed, "reason": reason[:200]}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        pass


def decide(request: dict) -> tuple[bool, str]:
    """Return (allowed, reason). Fails closed on anything unknown/sensitive."""
    method = request.get("method", "")
    policy = load_policy()
    mode = policy.get("mode", MODE_DENY)

    # Absolute refusals regardless of mode — never autonomous.
    if method in {"stop", "destroy", "exportWorkspace"}:
        return False, "destructive method always requires fresh consent"
    argv = " ".join(str(a).lower() for a in request.get("command", {}).get("argv", []))
    if any(token in argv for token in ("uac", "runas", "bypassuac", "-enc", "-encodedcommand", "invoke-expression", "iex ")):
        return False, "elevation/encoded-command always refused"
    content = str(request.get("file", {}).get("content", "")).lower()
    if "password" in content or "api_key" in content or "token=" in content:
        return False, "credential-like file content refused"

    if mode == MODE_STANDING:
        scopes = set(policy.get("scopes", []))
        allowed_methods = {"environment", "isRunning", "snapshot", "provision", "prepare",
                           "listFiles", "readFile", "screenObserve"}
        if "shell" in scopes and method == "execute":
            allowed_methods.add("execute")
        if "files" in scopes:
            allowed_methods.add("writeFile")
        if "screen" in scopes:
            allowed_methods.add("screenAct")
        if "apps" in scopes:
            allowed_methods.add("execute")
        # Browser is an explicit opt-in scope: shell/screen/apps never imply it.
        if "browser" in scopes:
            allowed_methods.update({"browserObserve", "browserFind", "browserAct", "browserSession"})
        if method in allowed_methods:
            audit_decision(request, True, f"standing:{','.join(sorted(scopes))}")
            return True, f"owner standing consent ({','.join(sorted(scopes))})"
        audit_decision(request, False, "standing scope miss")
        return False, "not in standing consent scopes"

    if mode == MODE_BOUNDED:
        scopes = set(policy.get("scopes", []))
        if method in {"environment", "isRunning", "snapshot", "provision", "prepare",
                      "listFiles", "readFile", "screenObserve", "screenAct"} and "screen" in scopes:
            audit_decision(request, True, "bounded:screen")
            return True, "bounded consent"
        if method == "execute" and ("shell" in scopes or "apps" in scopes):
            audit_decision(request, True, "bounded:shell")
            return True, "bounded consent"
        if method == "writeFile" and "files" in scopes:
            audit_decision(request, True, "bounded:files")
            return True, "bounded consent"
        if "browser" in scopes and method.startswith("browser"):
            audit_decision(request, True, "bounded:browser")
            return True, "bounded browser consent"
        audit_decision(request, False, "bounded scope miss")
        return False, "not in bounded consent scopes"

    audit_decision(request, False, "no policy")
    return False, "no owner consent configured (default deny)"
