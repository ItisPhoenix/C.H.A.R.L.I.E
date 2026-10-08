"""The Cua runtime must never start outside a bounded, manifest-pinned configuration.

These tests exist because an earlier bridge wrapped bounded construction in
``except Exception: return CuaDriver.create()``. That fallback was silent: it produced a
standard runtime while the code still claimed to be bounded.

They also pin the two-scope split. Cua refuses to combine origin-scoped browsing with
generic native tools, so native control and isolated browsing need separate ceilings and
therefore separate runtimes.
"""

from __future__ import annotations

import json

import pytest

from charlie.computer import manifest as manifest_mod
from charlie.computer.cua_bridge import CuaBridgeUnavailable, CuaRuntimeBridge

NATIVE_CANONICAL = {
    "get_window_state", "bring_to_front", "invoke_menu", "double_click", "move_cursor", "click", "drag",
    "scroll", "set_window_frame", "type_text", "set_value", "press_key", "hotkey",
    "check_permissions",
}
BROWSER_CANONICAL = {
    "list_windows", "browser_prepare", "get_browser_state", "browser_navigate",
    "browser_click", "browser_type", "browser_pointer", "end_session",
    "check_permissions",
}


class _FakeOptions:
    class ConfiguredDriverOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class RuntimeAuthorizationOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class SessionPermissionMode:
        BOUNDED = "bounded"


# ------------------------------------------------------------------ shape


def test_manifest_version_is_two_or_later():
    """Application identity grants require v2; v1 rejects them."""
    assert manifest_mod.MANIFEST_VERSION >= 2


def test_both_scopes_are_bounded():
    for scope in (manifest_mod.NATIVE_SCOPE, manifest_mod.BROWSER_SCOPE):
        kwargs = {"window_target": {"pid": 12, "window_id": 34}} if scope == manifest_mod.NATIVE_SCOPE else {}
        body = manifest_mod.manifest_body(scope, **kwargs)
        assert body["mode"] == "bounded", scope
        assert body["version"] >= 2, scope
        assert body["allow"]["tools"], scope


def test_unknown_scope_is_rejected():
    with pytest.raises(ValueError):
        manifest_mod.manifest_body("everything")


def test_no_scope_opts_into_unrestricted():
    for scope in (manifest_mod.NATIVE_SCOPE, manifest_mod.BROWSER_SCOPE):
        kwargs = {"window_target": {"pid": 12, "window_id": 34}} if scope == manifest_mod.NATIVE_SCOPE else {}
        blob = json.dumps(manifest_mod.manifest_body(scope, **kwargs)).lower()
        for forbidden in ("unrestricted", "dangerously", "existing_profile"):
            assert forbidden not in blob, f"{scope} manifest must not carry {forbidden}"


def test_native_window_grant_is_exact_and_never_grants_shared_host_apps():
    target = {"pid": 4242, "window_id": 8192}
    body = manifest_mod.native_body(window_target=target)

    assert body["resources"]["desktop"] == {
        "display": False,
        "windows": [target],
    }
    assert body["resources"].get("apps", []) == []
    assert not (set(body["allow"]["tools"]) & {"list_windows", "launch_app", "get_desktop_state"})


def test_native_manifest_rejects_missing_or_invalid_window_targets():
    with pytest.raises(ValueError, match="exact positive PID and window_id"):
        manifest_mod.native_body()
    with pytest.raises(ValueError, match="exact positive PID and window_id"):
        manifest_mod.native_body(window_target={"pid": 0, "window_id": 8192})


def test_native_manifest_targets_flow_through_scope_writer(tmp_path, monkeypatch):
    target = {"pid": 4242, "window_id": 8192}
    path = manifest_mod.scope_manifest_path(
        manifest_mod.NATIVE_SCOPE,
        window_target=target,
        directory=str(tmp_path),
    )
    written = json.loads((tmp_path / "bounded_manifest_native.json").read_text())
    assert path
    assert written["resources"]["desktop"]["windows"] == [target]
    assert written["resources"].get("apps", []) == []


# ------------------------------------------------------- scope separation


def test_browser_scope_grants_no_native_input():
    granted = set(manifest_mod.BROWSER_TOOLS)
    native_only = {
        "type_text", "press_key", "hotkey", "click", "drag", "scroll",
        "bring_to_front", "move_cursor", "get_window_state",
    }
    assert not (granted & native_only), (
        f"origin-scoped browser scope must not carry native input: {granted & native_only}"
    )


def test_native_scope_grants_no_browser_tools():
    granted = set(manifest_mod.NATIVE_TOOLS)
    assert not (granted & {"browser_prepare", "browser_navigate", "browser_click"})


def test_tool_names_are_canonical():
    """An unreviewed name makes the driver reject the whole manifest."""
    assert not (set(manifest_mod.NATIVE_TOOLS) - NATIVE_CANONICAL)
    assert not (set(manifest_mod.BROWSER_TOOLS) - BROWSER_CANONICAL)


def test_browser_scope_denies_existing_profiles():
    body = manifest_mod.manifest_body(manifest_mod.BROWSER_SCOPE)
    assert "existing_profiles" not in body["resources"]["browser"]


def test_browser_scope_grants_only_declared_origins():
    body = manifest_mod.manifest_body(
        manifest_mod.BROWSER_SCOPE, origins=["about:blank", "https://example.com"]
    )
    assert body["resources"]["browser"]["origins"] == ["about:blank", "https://example.com"]
    assert body["resources"]["browser"]["profiles"] == []


def test_browser_scope_requires_about_blank():
    """The origin check runs against the loaded page, which starts as about:blank."""
    assert "about:blank" in manifest_mod.DEFAULT_ORIGINS


# ------------------------------------------------- runtime path resolution


def test_executable_paths_are_resolved_not_hardcoded():
    chrome = manifest_mod.resolve_browser_executable("chrome")
    brave = manifest_mod.resolve_browser_executable("brave")
    for name, path in (("chrome", chrome), ("brave", brave)):
        if path is None:
            continue  # not installed here; nothing to assert
        assert "\\" in path or "/" in path, f"{name} path is not absolute: {path}"
        assert "?" not in path, f"{name} path looks guessed: {path}"


def test_unknown_browser_resolves_to_nothing():
    assert manifest_mod.resolve_browser_executable("netscape") is None


def test_browser_scope_grants_the_resolved_executable():
    chrome = manifest_mod.resolve_browser_executable("chrome")
    body = manifest_mod.manifest_body(manifest_mod.BROWSER_SCOPE)
    apps = body["resources"].get("apps") or []
    if chrome:
        assert apps == [{"executable": chrome, "windows": "all"}]
    else:
        assert apps == []


# ------------------------------------------------------------ persistence


def test_scope_paths_are_written_outside_the_repository(tmp_path):
    for scope in (manifest_mod.NATIVE_SCOPE, manifest_mod.BROWSER_SCOPE):
        kwargs = {"window_target": {"pid": 12, "window_id": 34}} if scope == manifest_mod.NATIVE_SCOPE else {}
        path = manifest_mod.scope_manifest_path(scope, directory=str(tmp_path), **kwargs)
        assert path
        name = f"bounded_manifest_{scope}.json"
        written = json.loads((tmp_path / name).read_text())
        assert written["mode"] == "bounded"


def test_scope_write_is_idempotent(tmp_path):
    window_target = {"pid": 12, "window_id": 34}
    manifest_mod.scope_manifest_path(
        manifest_mod.NATIVE_SCOPE,
        directory=str(tmp_path),
        window_target=window_target,
    )
    path = tmp_path / "bounded_manifest_native.json"
    stamp = path.stat().st_mtime_ns
    manifest_mod.scope_manifest_path(
        manifest_mod.NATIVE_SCOPE,
        directory=str(tmp_path),
        window_target=window_target,
    )
    assert path.stat().st_mtime_ns == stamp


def test_scopes_write_separate_files(tmp_path):
    target = {"pid": 12, "window_id": 34}
    native = manifest_mod.scope_manifest_path(
        manifest_mod.NATIVE_SCOPE, directory=str(tmp_path), window_target=target
    )
    browser = manifest_mod.scope_manifest_path(
        manifest_mod.BROWSER_SCOPE, directory=str(tmp_path)
    )
    assert native != browser


# --------------------------------------------------------- fail-closed


def test_manifest_failure_reports_unavailable(monkeypatch):
    monkeypatch.setattr(manifest_mod, "scope_manifest_path", lambda *a, **k: None)

    with pytest.raises(CuaBridgeUnavailable):
        CuaRuntimeBridge._build_bounded_driver(_FakeOptions())

    with pytest.raises(CuaBridgeUnavailable):
        CuaRuntimeBridge._build_bounded_driver(
            _FakeOptions(), manifest_mod.BROWSER_SCOPE
        )


@pytest.mark.asyncio
async def test_native_bridge_passes_its_exact_target_to_manifest(monkeypatch, tmp_path):
    seen = {}

    def write_manifest(scope, **kwargs):
        seen.update(scope=scope, **kwargs)
        return str(tmp_path / "bounded.json")

    monkeypatch.setattr(manifest_mod, "scope_manifest_path", write_manifest)

    class _Configured(_FakeOptions):
        CuaDriver = type("CuaDriver", (), {
            "create_configured": staticmethod(lambda options: type("Driver", (), {"call_tool": None})())
        })

    target = {"pid": 4242, "window_id": 8192}
    driver = await CuaRuntimeBridge._build_bounded_driver(
        _Configured(), window_target=target
    )
    assert seen == {
        "scope": manifest_mod.NATIVE_SCOPE,
        "origins": None,
        "window_target": target,
    }
    assert hasattr(driver, "call_tool")


def test_missing_configured_factory_reports_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        manifest_mod,
        "scope_manifest_path",
        lambda *a, **k: str(tmp_path / "m.json"),
    )

    class _NoConfigured(_FakeOptions):
        CuaDriver = type(
            "CuaDriver", (), {"create": staticmethod(lambda options=None: object())}
        )

    with pytest.raises(CuaBridgeUnavailable):
        CuaRuntimeBridge._build_bounded_driver(
            _NoConfigured(), window_target={"pid": 1, "window_id": 2}
        )


def test_wrong_shaped_construction_is_refused(monkeypatch, tmp_path):
    """A plain string is not a driver, and must not be accepted as one."""
    monkeypatch.setattr(
        manifest_mod,
        "scope_manifest_path",
        lambda *a, **k: str(tmp_path / "m.json"),
    )

    class _Bad(_FakeOptions):
        CuaDriver = type(
            "CuaDriver",
            (),
            {
                "create": staticmethod(
                    lambda options=None: (_ for _ in ()).throw(
                        AssertionError("plain create() must never be used")
                    )
                ),
                "create_configured": staticmethod(lambda options: "not-a-driver"),
            },
        )

    with pytest.raises(CuaBridgeUnavailable):
        CuaRuntimeBridge._build_bounded_driver(
            _Bad(), window_target={"pid": 1, "window_id": 2}
        )


def test_start_failure_surfaces_reason(monkeypatch):
    bridge = CuaRuntimeBridge()

    async def _boom():
        raise CuaBridgeUnavailable("bounded manifest is corrupt")

    monkeypatch.setattr(bridge, "_create_driver", _boom)
    assert bridge.start() is False
    assert bridge.startup_error is not None
    assert "bounded manifest is corrupt" in str(bridge.startup_error)
    assert bridge.driver is None


def test_bridge_carries_its_scope():
    assert CuaRuntimeBridge(scope=manifest_mod.BROWSER_SCOPE)._scope == "browser"
    assert CuaRuntimeBridge()._scope == "native"
