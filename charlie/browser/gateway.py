"""One Windows browser capability for Charlie's device gateway.

Deterministic Playwright/DOM control on the existing shared browser thread.
Public browsing uses an isolated private profile. Charlie's logged-in visible
profile and state-changing submissions require explicit, scoped browser grants
(charlie.browser.authorization) -- device standing consent is never treated as
blanket authorization for authenticated websites.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from charlie.browser import authorization as authz
from charlie.browser import controller

logger = logging.getLogger("charlie.browser.gateway")

PRIVATE = "private"
VISIBLE = "visible"
_HANDOFF_PHRASES = (
    "enter your password", "two-factor", "two factor", "2fa", "verify it's you",
    "verify your identity", "captcha", "sign in to continue", "one-time code",
)
_SENSITIVE_ACTIONS = ("submit", "pay", "purchase", "delete", "send", "confirm order", "place order")
_active_tab: dict[str, int] = {PRIVATE: 0, VISIBLE: 0}


def _ok(value: Any, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "value": value, **extra}


def _err(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": str(reason)[:400], **extra}


def _validate_url(url: str, allow_domains: Optional[list[str]] = None) -> tuple[str, str]:
    value = (url or "").strip()
    if not value:
        raise ValueError("url is required")
    parsed = urlparse(value if "://" in value else "https://" + value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http/https URLs are allowed")
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host:
        raise ValueError("url has no host")
    if allow_domains:
        allowed = {str(d).casefold().rstrip(".") for d in allow_domains}
        if not any(host == d or host.endswith("." + d) for d in allowed):
            raise ValueError("domain not in allowlist")
    return parsed.geturl(), host


def _session_auth(session: str, grant_id: str, *, host: str = "", need_data: bool = False) -> Optional[dict]:
    if session not in (PRIVATE, VISIBLE):
        return _err("unknown browser session")
    if session == PRIVATE:
        return None
    allowed, reason = authz.authorize(grant_id, scope=authz.SCOPE_VISIBLE, url_or_domain=host)
    if not allowed:
        return _err(reason)
    if need_data:
        allowed, reason = authz.authorize(grant_id, scope=authz.SCOPE_PRIVATE_DATA, url_or_domain=host)
        if not allowed:
            return _err(reason)
    return None


def _page_for(session: str):
    """Pick the active tab inside the context so tab switches survive controller.run."""
    def pick(page):
        pages = page.context.pages
        idx = max(0, min(_active_tab.get(session, 0), len(pages) - 1))
        return pages[idx]
    return pick


def _run(session: str, fn: Callable[[Any], Any], timeout: float = 90.0) -> Any:
    runner = controller.run_user if session == VISIBLE else controller.run

    def wrapped(page: Any) -> Any:
        return fn(_page_for(session)(page))

    return runner(wrapped, timeout=timeout)


def _handoff_reason(text: str) -> Optional[str]:
    low = (text or "").casefold()
    for phrase in _HANDOFF_PHRASES:
        if phrase in low:
            return f"login/2FA/challenge detected ({phrase}); hand control to the owner"
    return None


def _tabs(page: Any, session: str) -> list[dict[str, Any]]:
    out = []
    for i, p in enumerate(page.context.pages):
        try:
            out.append({"index": i, "url": str(p.url)[:300], "title": str(p.title())[:160],
                        "active": i == _active_tab.get(session, 0)})
        except Exception:
            out.append({"index": i, "url": "", "title": "(unavailable)",
                        "active": i == _active_tab.get(session, 0)})
    return out


def _locator(page: Any, spec: dict[str, Any]):
    by = str(spec.get("by", "text")).casefold()
    value = str(spec.get("value", ""))
    if not value:
        raise ValueError("locator value is required")
    if by == "role":
        name = spec.get("name")
        loc = page.get_by_role(value, name=name) if name else page.get_by_role(value)
    elif by == "text":
        loc = page.get_by_text(value, exact=bool(spec.get("exact", False)))
    elif by == "label":
        loc = page.get_by_label(value)
    elif by == "placeholder":
        loc = page.get_by_placeholder(value)
    elif by == "testid":
        loc = page.get_by_test_id(value)
    elif by == "css":
        loc = page.locator(value)
    else:
        raise ValueError(f"unsupported locator by={by}")
    return loc


def _locator_desc(spec: dict[str, Any]) -> str:
    return f"{spec.get('by', 'text')}:{spec.get('value', '')} name={spec.get('name', '')}".casefold()


def status() -> dict[str, Any]:
    from charlie.browser import BROWSER_AVAILABLE

    return _ok({
        "available": bool(BROWSER_AVAILABLE),
        "private_profile_isolated": True,
        "visible_profile_requires_grant": True,
        "active_grants": sum(1 for g in authz.list_grants() if g.get("active")),
        "supported_sessions": [PRIVATE, VISIBLE],
        "transport": "Playwright/DOM on shared browser thread (no CDP network listener)",
    })


def navigate(url: str, *, session: str = PRIVATE, grant_id: str = "",
             allow_domains: Optional[list[str]] = None, wait_until: str = "domcontentloaded",
             timeout_ms: int = 30000) -> dict[str, Any]:
    try:
        url, host = _validate_url(url, allow_domains)
    except ValueError as exc:
        return _err(str(exc))
    denied = _session_auth(session, grant_id, host=host)
    if denied:
        return denied

    def op(page: Any) -> dict[str, Any]:
        page.goto(url, wait_until=wait_until, timeout=max(1, int(timeout_ms)))
        return {"url": str(page.url), "title": str(page.title()),
                "tabs": _tabs(page, session), "status": "navigated"}

    try:
        return _ok(_run(session, op, timeout=(int(timeout_ms) / 1000) + 30), session=session, host=host)
    except Exception as exc:
        logger.warning("browser navigate failed: %s: %s", type(exc).__name__, str(exc)[:160])
        return _err(f"{type(exc).__name__}: {exc}", session=session)


def observe(*, session: str = PRIVATE, grant_id: str = "", max_marks: int = 40,
            text_budget: int = 600, host: str = "") -> dict[str, Any]:
    denied = _session_auth(session, grant_id, host=host, need_data=(session == VISIBLE))
    if denied:
        return denied

    def op(page: Any) -> dict[str, Any]:
        from charlie.browser.observation import observe as _observe
        block, marks, text = _observe(page, max_marks=max_marks, text_budget=text_budget)
        handoff = _handoff_reason(text)
        return {
            "url": str(page.url), "title": str(page.title()),
            "observation": block,
            "marks": [{"id": m.mark_id, "role": m.role, "name": m.name[:140], "href": m.href}
                      for m in marks],
            "text": text[:text_budget],
            "tabs": _tabs(page, session),
            "handoff_required": bool(handoff),
            "handoff_reason": handoff or "",
        }

    try:
        return _ok(_run(session, op), session=session)
    except Exception as exc:
        logger.warning("browser observe failed: %s: %s", type(exc).__name__, str(exc)[:160])
        return _err(f"{type(exc).__name__}: {exc}", session=session)


def find(locator: dict[str, Any], *, session: str = PRIVATE, grant_id: str = "",
         limit: int = 10) -> dict[str, Any]:
    denied = _session_auth(session, grant_id, need_data=(session == VISIBLE))
    if denied:
        return denied

    def op(page: Any) -> dict[str, Any]:
        loc = _locator(page, locator)
        count = loc.count()
        matches = []
        for i in range(min(int(count), max(1, int(limit)))):
            el = loc.nth(i)
            try:
                matches.append({
                    "index": i,
                    "tag": el.evaluate("e => e.tagName.toLowerCase()"),
                    "role": el.get_attribute("role") or "",
                    "name": el.get_attribute("aria-label") or "",
                    "text": (el.inner_text() or "")[:200],
                    "href": el.get_attribute("href") or "",
                })
            except Exception:
                continue
        return {"count": int(count), "matches": matches, "url": str(page.url)}

    try:
        return _ok(_run(session, op), session=session)
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}", session=session)


def _sensitive_check(action: dict[str, Any], grant_id: str, session: str) -> Optional[dict]:
    kind = str(action.get("kind", "")).casefold()
    desc = _locator_desc(action.get("locator", {}))
    sensitive = kind in _SENSITIVE_ACTIONS or any(term in desc for term in _SENSITIVE_ACTIONS)
    if not sensitive:
        return None
    allowed, reason = authz.authorize(grant_id, scope=authz.SCOPE_SENSITIVE_SUBMIT)
    if not allowed:
        return _err(reason)
    if session == VISIBLE:
        denied = _session_auth(session, grant_id, need_data=True)
        if denied:
            return denied
    return None


def act(actions: list[dict[str, Any]], *, session: str = PRIVATE, grant_id: str = "") -> dict[str, Any]:
    if not isinstance(actions, list) or not (1 <= len(actions) <= 24):
        return _err("actions must be a list of 1..24 items")
    denied = _session_auth(session, grant_id, need_data=(session == VISIBLE))
    if denied:
        return denied

    def op(page: Any) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        expect = {}
        for action in actions:
            kind = str(action.get("kind", "")).casefold()
            entry: dict[str, Any] = {"kind": kind, "ok": False}
            try:
                if kind == "wait":
                    page.wait_for_timeout(max(0, min(int(action.get("ms", 0)), 10000)))
                    entry.update(ok=True, detail=f"waited {action.get('ms', 0)}ms")
                elif kind == "wait_for_text":
                    page.get_by_text(str(action.get("text", "")), exact=False).wait_for(
                        state="visible", timeout=max(100, int(action.get("timeout_ms", 10000))))
                    entry.update(ok=True, detail="text visible")
                elif kind == "tab_list":
                    entry.update(ok=True, detail=f"{len(page.context.pages)} tabs",
                                 tabs=_tabs(page, session))
                elif kind == "tab_new":
                    p = page.context.new_page()
                    url = action.get("url")
                    if url:
                        u, _ = _validate_url(str(url))
                        p.goto(u, wait_until="domcontentloaded", timeout=30000)
                    _active_tab[session] = len(page.context.pages) - 1
                    entry.update(ok=True, detail=f"tab {_active_tab[session]}", url=str(p.url))
                elif kind == "tab_close":
                    idx = int(action.get("index", _active_tab.get(session, 0)))
                    pages = page.context.pages
                    if len(pages) <= 1:
                        raise ValueError("refusing to close the last tab")
                    pages[max(0, min(idx, len(pages) - 1))].close()
                    _active_tab[session] = max(0, min(_active_tab.get(session, 0), len(pages) - 2))
                    entry.update(ok=True, detail="tab closed")
                elif kind == "tab_switch":
                    idx = int(action.get("index", 0))
                    pages = page.context.pages
                    if not (0 <= idx < len(pages)):
                        raise ValueError("tab index out of range")
                    _active_tab[session] = idx
                    pages[idx].bring_to_front()
                    entry.update(ok=True, detail=f"active tab {idx}", url=str(pages[idx].url))
                elif kind == "download":
                    loc = _locator(page, action.get("locator", {}))
                    folder = Path(os.environ["LOCALAPPDATA"]) / "Charlie" / "downloads"
                    folder.mkdir(parents=True, exist_ok=True)
                    with page.expect_download(timeout=max(1000, int(action.get("timeout_ms", 30000)))) as info:
                        loc.first.click()
                    download = info.value
                    dest = folder / (download.suggested_filename or f"download-{int(time.time())}")
                    download.save_as(str(dest))
                    entry.update(ok=True, detail=str(dest), filename=download.suggested_filename)
                elif kind in {"click", "fill", "type", "select", "check", "submit"}:
                    sensitive = _sensitive_check(action, grant_id, session)
                    if sensitive:
                        results.append(entry | sensitive)
                        break
                    loc = _locator(page, action.get("locator", {}))
                    target = loc.first
                    if kind in {"click", "submit"}:
                        target.click(timeout=max(100, int(action.get("timeout_ms", 10000))))
                    elif kind == "fill":
                        target.fill(str(action.get("text", "")))
                    elif kind == "type":
                        target.press_sequentially(str(action.get("text", "")), delay=int(action.get("delay_ms", 0)))
                    elif kind == "select":
                        if action.get("label"):
                            target.select_option(label=str(action["label"]))
                        else:
                            target.select_option(str(action.get("value", "")))
                    elif kind == "check":
                        target.check()
                    if action.get("expect_url_contains"):
                        page.wait_for_url(f"**{action['expect_url_contains']}**",
                                          timeout=max(100, int(action.get("timeout_ms", 15000))))
                    entry.update(ok=True, detail=f"{kind} on {_locator_desc(action.get('locator', {}))}")
                elif kind == "press":
                    page.keyboard.press(str(action.get("key", "Enter")))
                    entry.update(ok=True, detail=f"pressed {action.get('key')}")
                else:
                    raise ValueError(f"unsupported action kind {kind}")
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            if action.get("expect_text_contains"):
                expect["text"] = str(action["expect_text_contains"])
            results.append(entry)

        # Re-observe the *currently active* tab for an independent postcondition.
        from charlie.browser.observation import observe as _observe
        active = _page_for(session)(page)
        _block, _marks, text = _observe(active, max_marks=20, text_budget=400)
        verified = None
        if expect.get("text"):
            verified = expect["text"].casefold() in text.casefold()
        failures = [r for r in results if not r.get("ok")]
        return {
            "results": results,
            "completed": len(results) - len(failures),
            "verification": {
                "url": str(active.url), "title": str(active.title()),
                "expected_text": expect.get("text"), "text_found": verified,
                "text_excerpt": text[:300],
            },
            "handoff_required": bool(_handoff_reason(text)),
            "handoff_reason": _handoff_reason(text) or "",
        }

    try:
        result = _run(session, op)
        if any(not r.get("ok") for r in result.get("results", [])):
            return dict(_err("one or more browser actions failed"), value=result, session=session)
        return _ok(result, session=session)
    except Exception as exc:
        logger.warning("browser act failed: %s: %s", type(exc).__name__, str(exc)[:160])
        return _err(f"{type(exc).__name__}: {exc}", session=session)


def tabs(op: str = "list", *, session: str = PRIVATE, index: int = 0,
         url: str = "", grant_id: str = "") -> dict[str, Any]:
    denied = _session_auth(session, grant_id, need_data=(session == VISIBLE))
    if denied:
        return denied
    return act([{"kind": f"tab_{op}", "index": index, "url": url}], session=session, grant_id=grant_id)


def reset(*, session: str = PRIVATE) -> dict[str, Any]:
    _active_tab[session] = 0
    if session == PRIVATE:
        try:
            controller.shutdown()
        except Exception as exc:
            logger.debug("browser reset: %s", exc)
    return _ok({"reset": session})


# --------------------------------------------------------------------------- shared dispatch
def dispatch_command(command: str, data: dict[str, Any]) -> dict[str, Any]:
    """One dispatch table shared by the CLI client and the local browser service."""
    session = str(data.get("session", PRIVATE))
    grant_id = str(data.get("grantId", ""))
    if command == "sequence":
        steps = data.get("steps", [])
        if not isinstance(steps, list) or not (1 <= len(steps) <= 16):
            return _err("steps must be a list of 1..16 items")
        results = []
        for step in steps:
            if not isinstance(step, dict) or step.get("command") == "sequence":
                results.append(_err("invalid or nested sequence step"))
                break
            sub = dict(step.get("data") or {})
            sub.setdefault("session", session)
            sub.setdefault("grantId", grant_id)
            results.append(dispatch_command(str(step.get("command", "")), sub))
        if any(not r.get("ok") for r in results):
            return dict(_err("one or more sequence steps failed"), value={"results": results})
        return _ok({"results": results, "completed": len(results)})
    if command == "status":
        return status()
    if command == "navigate":
        return navigate(data.get("url", ""), session=session, grant_id=grant_id,
                        allow_domains=data.get("allowDomains"),
                        wait_until=data.get("waitUntil", "domcontentloaded"),
                        timeout_ms=int(data.get("timeoutMs", 30000)))
    if command == "observe":
        return observe(session=session, grant_id=grant_id,
                       max_marks=int(data.get("maxMarks", 40)),
                       text_budget=int(data.get("textBudget", 600)),
                       host=str(data.get("host", "")))
    if command == "find":
        return find(data.get("locator", {}), session=session, grant_id=grant_id,
                    limit=int(data.get("limit", 10)))
    if command == "act":
        return act(data.get("actions", []), session=session, grant_id=grant_id)
    if command == "tabs":
        return tabs(data.get("op", "list"), session=session, index=int(data.get("index", 0)),
                    url=str(data.get("url", "")), grant_id=grant_id)
    if command == "reset":
        return reset(session=session)
    if command == "grant":
        try:
            return _ok(authz.issue(list(data.get("scopes", [])),
                                   domains=list(data.get("domains", [])),
                                   ttl_s=int(data.get("ttlS", 600)),
                                   granted_by=str(data.get("grantedBy", "owner"))))
        except Exception as exc:
            return _err(f"{type(exc).__name__}: {exc}")
    if command == "grants":
        return _ok(authz.list_grants())
    if command == "revoke":
        return _ok({"revoked": authz.revoke(str(data.get("grantId", grant_id)))})
    return _err("unknown command")


# --------------------------------------------------------------------------- CLI
def _cli() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(prog="charlie.browser.gateway")
    parser.add_argument("command", choices=["status", "navigate", "observe", "find", "act",
                                            "tabs", "reset", "sequence", "grant", "grants", "revoke"])
    parser.add_argument("--file", help="path to a JSON args file")
    parser.add_argument("json", nargs="?", help="inline JSON args")
    parser.add_argument("--session", default=PRIVATE, choices=[PRIVATE, VISIBLE])
    parser.add_argument("--grant-id", default="")
    cli = parser.parse_args()

    data: dict[str, Any] = {}
    if cli.file:
        data = json.loads(Path(cli.file).read_text(encoding="utf-8-sig"))
    elif cli.json:
        data = json.loads(cli.json)
    if not isinstance(data, dict):
        print(json.dumps(_err("args must be a JSON object")))
        return 2

    data.setdefault("session", cli.session)
    data.setdefault("grantId", cli.grant_id)

    stateful = {"navigate", "observe", "find", "act", "tabs", "reset", "sequence"}
    if cli.command in stateful and not data.get("noService"):
        from charlie.browser.service import call

        result = call(cli.command, data, timeout_ms=int(data.get("timeoutMs", 120000)) + 30000)
    else:
        result = dispatch_command(cli.command, data)

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
