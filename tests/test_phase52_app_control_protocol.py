"""Phase 5.2 regression: typed personal-device app control (isolated, TEST/MOCK).

No test launches a real window or calls a real shell. All native/window/crypto
boundaries are replaced with explicit fakes.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from charlie import app_control
from charlie import consent_policy as cp
from charlie import device_gateway as dg
from charlie import native_executor as ne

APP_METHODS = ("appDiscover", "appLaunch", "appFocus", "appVerify")


class FakeWS:
    closed = False

    def __init__(self):
        self.frames: list[dict] = []

    async def send(self, raw: str):
        self.frames.append(json.loads(raw))


class FakeOutcome:
    def __init__(self, value, ok=True, error=None):
        self.value = value
        self.ok = ok
        self.error = error
        self.tier = "native"
        self.duration_ms = 0.0


class FakeExecutor:
    def __init__(self, value=None, ok=True, error=None):
        self.calls: list[tuple] = []
        self.value = value or {"ok": True, "state": "launched", "focus_verified": True}
        self.ok = ok
        self.error = error

    def dispatch(self, method, params):
        self.calls.append((method, params))
        return FakeOutcome(self.value, self.ok, self.error)


def gateway(monkeypatch, consent=None):
    monkeypatch.setattr(dg, "load_device_state", lambda: {"deviceId": "dev-1", "url": "wss://x", "token": "t"})
    if consent is not None:
        monkeypatch.setattr(dg, "ConsentProvider", lambda: consent)
    return dg.DeviceGateway()


def test_gateway_routes_every_app_method_to_native_executor(monkeypatch):
    ex = FakeExecutor()
    monkeypatch.setattr(ne, "get_executor", lambda: ex)
    gw = gateway(monkeypatch)
    for method in APP_METHODS:
        value = asyncio.run(gw._dispatch(method, {"app": "Calculator"}))
        assert value == ex.value
    assert ex.calls == [
        ("app_discover", {"app": "Calculator"}),
        ("app_launch", {"app": "Calculator"}),
        ("app_focus", {"app": "Calculator"}),
        ("app_verify", {"app": "Calculator"}),
    ]


@pytest.mark.parametrize("label", [
    "C:\\Windows\\System32\\calc.exe",
    "/usr/bin/calc",
    "..\\calc",
    "calc; whoami",
    "calc | powershell",
    "",
    "x" * 121,
])
def test_gateway_rejects_path_like_or_oversized_label(monkeypatch, label):
    ex = FakeExecutor()
    monkeypatch.setattr(ne, "get_executor", lambda: ex)
    gw = gateway(monkeypatch)
    for method in APP_METHODS:
        with pytest.raises(ValueError, match="app label"):
            asyncio.run(gw._dispatch(method, {"app": label}))
    assert ex.calls == []


def test_app_methods_have_consent_and_deadline_classification():
    for method in ("appDiscover", "appVerify"):
        assert method in dg.READONLY_METHODS
    for method in ("appLaunch", "appFocus"):
        assert method in dg.SENSITIVE_METHODS
    assert method not in {"stop", "destroy"}
    for method in APP_METHODS:
        assert dg.deadline_ms({"method": method}) == 120_000


def test_launch_requires_consent_and_emits_error_without_dispatch(monkeypatch):
    ex = FakeExecutor()
    monkeypatch.setattr(ne, "get_executor", lambda: ex)

    class Deny:
        async def decide(self, request):
            return dg.ConsentDecision(False, "revoked")

    gw = gateway(monkeypatch, consent=Deny())
    ws = FakeWS()
    asyncio.run(gw._execute_request(ws, "req-1", {
        "method": "appLaunch", "app": "Calculator", "botId": "bot-1", "runId": "run-1",
    }))
    errors = [f["frame"].get("error", "") for f in ws.frames if "error" in f.get("frame", {})]
    assert errors and "consent denied" in errors[0]
    assert ex.calls == []


def test_native_executor_app_launch_uses_resolver_not_shell(monkeypatch):
    result = {"ok": True, "name": "Calculator", "state": "focused", "focus_verified": True}
    monkeypatch.setattr(app_control, "discover", lambda name: {"ok": True, "state": "found"})
    monkeypatch.setattr(app_control, "launch", lambda name: result)
    monkeypatch.setattr(app_control, "focus", lambda name: result)
    monkeypatch.setattr(app_control, "verify", lambda name: result)
    monkeypatch.setattr(ne.subprocess, "run", lambda *a, **k: pytest.fail("shell must not be used"))
    out = ne.NativeExecutor().dispatch("app_launch", {"app": "Calculator"})
    assert out.ok
    assert out.value == result


def test_consent_apps_scope_allows_launch_until_revoked(monkeypatch, tmp_path):
    path = tmp_path / "consent.json"
    monkeypatch.setattr(cp, "consent_path", lambda: path)
    monkeypatch.setattr(cp, "_dpapi", lambda data, *, protect: data)
    audits: list[tuple] = []
    monkeypatch.setattr(cp, "audit_decision", lambda req, allowed, reason: audits.append((req, allowed, reason)))

    cp.set_standing(["apps"])
    allowed, reason = cp.decide({"method": "appLaunch", "app": "Calculator"})
    assert allowed and "consent" in reason
    cp.revoke()
    allowed, _ = cp.decide({"method": "appLaunch", "app": "Calculator"})
    assert not allowed
    assert audits  # every decision is audited


def test_preserved_helper_rejects_bad_names_and_reports_focus(monkeypatch, tmp_path):
    class Resolution:
        def __init__(self, candidates=(), window_title=""):
            self.candidates = tuple(candidates)
            self.window_title = window_title
            self.source = "test"
            self.launch_target = "" if window_title else "target"

    calls = []

    def fake_resolve(name):
        calls.append(("resolve", name))
        if name == "Ambiguous":
            return Resolution(candidates=("One", "Two"))
        if name == "Calculator":
            return Resolution(window_title="Calculator")
        return None

    monkeypatch.setattr(app_control, "_resolution", fake_resolve)
    monkeypatch.setattr(app_control, "_bring_to_front", lambda title: calls.append(("focus", title)) or True)

    bad = app_control.discover("C:\\Windows\\System32\\calc.exe")
    assert bad["state"] == "invalid" and not bad["ok"]
    amb = app_control.discover("Ambiguous")
    assert amb["state"] == "ambiguous" and amb["candidates"] == ["One", "Two"]
    missing = app_control.discover("NoSuchApp")
    assert missing["state"] == "not_found"

    # launch_and_verify is reached through the real module; stub only the native boundary.
    apps_mod = __import__("charlie.desktop.apps", fromlist=["launch_and_verify"])
    monkeypatch.setattr(apps_mod, "launch_and_verify", lambda resolution: True)
    launched = app_control.launch("Calculator")
    assert launched["ok"] and launched["focus_verified"] is True
    assert ("focus", "Calculator") in calls
