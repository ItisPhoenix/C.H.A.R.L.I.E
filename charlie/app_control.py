"""Deterministic app discovery/launch/focus for the Windows device gateway.

Thin integration over charlie.desktop.apps and the native executor. Names are
validated as app labels only; paths and shell/command text are rejected so this
entry point cannot be used as an arbitrary command runner.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Optional

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&'_+-]{0,119}$")


def _invalid(name: str) -> bool:
    return not name or not _LABEL.match(name) or len(name) > 120


def _resolution(name: str):
    from charlie.desktop.apps import resolve_local_app

    return resolve_local_app(name)


def _result(name: str, state: str, resolution=None, error: str = "",
            focus_verified: Optional[bool] = None) -> dict:
    out: dict[str, Any] = {"ok": state in {"found", "launched", "focused", "verified"},
                           "name": name, "state": state}
    if resolution is not None:
        out["source"] = resolution.source
        out["window_title"] = resolution.window_title or ""
        if resolution.candidates:
            out["candidates"] = list(resolution.candidates[:8])
    if focus_verified is not None:
        out["focus_verified"] = focus_verified
    if error:
        out["error"] = error[:300]
    return out


def _bring_to_front(title: str) -> bool:
    """Raise the window and confirm it is the real foreground window."""
    from charlie.desktop.windows import focus_window

    return "verified" in str(focus_window(title))


def discover(name: str) -> dict:
    if _invalid(name):
        return _result(name, "invalid", error="app label contains disallowed characters")
    resolution = _resolution(name)
    if resolution is None:
        return _result(name, "not_found")
    if resolution.candidates:
        return _result(name, "ambiguous", resolution)
    return _result(name, "found", resolution)


def launch(name: str) -> dict:
    if _invalid(name):
        return _result(name, "invalid", error="app label contains disallowed characters")
    resolution = _resolution(name)
    if resolution is None:
        return _result(name, "not_found")
    if resolution.candidates:
        return _result(name, "ambiguous", resolution)

    from charlie.desktop.apps import launch_and_verify

    state = "focused" if resolution.window_title else "launched"
    if not launch_and_verify(resolution):
        return _result(name, "failed", resolution, error="launch could not be verified")
    observed = _resolution(name)
    if observed is None or not observed.window_title:
        return _result(name, "failed", resolution, error="no window after launch")
    focused = _bring_to_front(observed.window_title)
    return _result(name, state, observed, focus_verified=focused)


def focus(name: str) -> dict:
    if _invalid(name):
        return _result(name, "invalid", error="app label contains disallowed characters")
    resolution = _resolution(name)
    if resolution is None:
        return _result(name, "not_found")
    if resolution.candidates:
        return _result(name, "ambiguous", resolution)
    if not resolution.window_title:
        return launch(name)
    from charlie.desktop.windows import find_window

    focused = _bring_to_front(resolution.window_title)
    observed = find_window(resolution.window_title)
    if not observed:
        return _result(name, "failed", resolution, error="window could not be focused")
    return _result(name, "focused", resolution, focus_verified=focused)


def verify(name: str) -> dict:
    if _invalid(name):
        return _result(name, "invalid", error="app label contains disallowed characters")
    resolution = _resolution(name)
    if resolution is None:
        return _result(name, "not_found")
    if resolution.candidates:
        return _result(name, "ambiguous", resolution)
    if not resolution.window_title:
        return _result(name, "not_visible", resolution)
    return _result(name, "verified", resolution)


_ACTIONS = {"discover": discover, "launch": launch, "focus": focus, "verify": verify}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="charlie.app_control", allow_abbrev=False)
    parser.add_argument("action", choices=sorted(_ACTIONS))
    parser.add_argument("name")
    args = parser.parse_args(argv)
    result = _ACTIONS[args.action](args.name)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
