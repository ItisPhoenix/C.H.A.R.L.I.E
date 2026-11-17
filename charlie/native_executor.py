"""Fast native Windows computer-use engine.

One adaptive dispatcher over existing Windows primitives:
  Tier 1 Win32/native -> Tier 2 UIA -> Tier 3 browser/DOM -> Tier 4 Cua/visual.

Execution endpoint only. No Brain, planner, or local LLM. All authority is
supplied by the caller (device gateway) via policy + consent.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("charlie.native_executor")


@dataclass
class ExecOutcome:
    ok: bool
    value: Any = None
    evidence: dict = field(default_factory=dict)
    tier: str = "native"
    error: Optional[str] = None
    duration_ms: float = 0.0


class _Cache:
    """Small TTL cache with explicit invalidation for window/control metadata."""

    def __init__(self, ttl_s: float = 0.5) -> None:
        self.ttl = ttl_s
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if item and time.monotonic() - item[0] <= self.ttl:
                return item[1]
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)

    def invalidate(self, prefix: str = "") -> None:
        with self._lock:
            if not prefix:
                self._data.clear()
            else:
                for k in [k for k in self._data if k.startswith(prefix)]:
                    self._data.pop(k, None)


class NativeExecutor:
    """Reusable, warm execution components with cached metadata."""

    def __init__(self) -> None:
        self._cache = _Cache(ttl_s=0.5)
        self._uia_ready = False
        self._backend = None
        self._lock = threading.RLock()

    # ---------- Tier 1: native Win32 / process / file ----------

    def windows(self) -> ExecOutcome:
        t = time.perf_counter()
        try:
            from charlie.desktop.windows import list_windows

            cached = self._cache.get("windows")
            if cached is not None:
                return ExecOutcome(True, cached, {"cached": True}, "native",
                                   duration_ms=(time.perf_counter() - t) * 1000)
            wins = list_windows()
            self._cache.set("windows", wins)
            return ExecOutcome(True, wins, {"count": len(wins)}, "native",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def find_window(self, title: str) -> ExecOutcome:
        t = time.perf_counter()
        try:
            from charlie.desktop.windows import find_window

            w = find_window(title)
            return ExecOutcome(w is not None, w, {"query": title}, "native",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def focus_window(self, title: str) -> ExecOutcome:
        t = time.perf_counter()
        try:
            from charlie.desktop.windows import focus_window

            result = focus_window(title)
            self._cache.invalidate("windows")
            return ExecOutcome(True, result, {"query": title}, "native",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def app_open(self, name: str) -> ExecOutcome:
        t = time.perf_counter()
        try:
            from charlie.desktop.apps import resolve_local_app, launch_and_verify

            resolved = resolve_local_app(name)
            if not resolved:
                return ExecOutcome(False, tier="native", error=f"app not found: {name}",
                                   duration_ms=(time.perf_counter() - t) * 1000)
            ok = launch_and_verify(resolved)
            self._cache.invalidate("windows")
            return ExecOutcome(bool(ok), {"name": name, "resolved": str(resolved)[:200]},
                               {"launched": bool(ok)}, "native",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def app_close(self, name: str) -> ExecOutcome:
        t = time.perf_counter()
        try:
            from charlie.desktop.windows import close_window_and_verify

            ok = close_window_and_verify(name)
            self._cache.invalidate("windows")
            return ExecOutcome(ok, {"closed": ok}, {"query": name}, "native",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def shell(self, argv: list[str], cwd: Optional[str] = None,
              timeout_ms: int = 60_000, env: Optional[dict] = None) -> ExecOutcome:
        t = time.perf_counter()
        if not argv or len(argv) > 1024:
            return ExecOutcome(False, tier="native", error="invalid argv")
        try:
            result = subprocess.run(
                [str(a) for a in argv],
                cwd=cwd or str(Path.home()),
                capture_output=True,
                text=True,
                timeout=max(1, min(timeout_ms, 600_000)) / 1000,
                shell=False,
                env=env,
            )
            events = []
            if result.stdout:
                events.append({"type": "stdout", "data": result.stdout[:100_000]})
            if result.stderr:
                events.append({"type": "stderr", "data": result.stderr[:100_000]})
            events.append({"type": "exit", "code": result.returncode})
            return ExecOutcome(True, events,
                               {"argv_count": len(argv), "exit": result.returncode}, "native",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except subprocess.TimeoutExpired:
            return ExecOutcome(False, [{"type": "stderr", "data": "timeout"}, {"type": "exit", "code": 124}],
                               {"timeout_ms": timeout_ms}, "native", "timeout",
                               (time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def list_files(self, path: str) -> ExecOutcome:
        t = time.perf_counter()
        try:
            p = Path(path).expanduser()
            if not p.is_dir():
                return ExecOutcome(False, tier="native", error="not a directory")
            entries = [str(x) for x in sorted(p.iterdir())[:500]]
            return ExecOutcome(True, {"entries": entries}, {"path": str(p)}, "native",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def read_file(self, path: str, max_bytes: int = 8 * 1024 * 1024) -> ExecOutcome:
        t = time.perf_counter()
        try:
            p = Path(path).expanduser()
            raw = p.read_bytes()[:max_bytes]
            return ExecOutcome(True, {"content": raw.decode("utf-8", errors="replace"), "bytes": len(raw)},
                               {"path": str(p)}, "native", duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def write_file(self, path: str, content: str) -> ExecOutcome:
        t = time.perf_counter()
        try:
            from charlie.tools import file_write

            result = file_write(path, content)
            return ExecOutcome(True, {"text": str(result)[:5000]}, {"path": path}, "native",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="native", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    # ---------- Tier 2: UIA ----------

    def uia_observe(self, query: str = "") -> ExecOutcome:
        t = time.perf_counter()
        try:
            from charlie.desktop.uia import snapshot_tree, serialize_marks

            cached = self._cache.get("uia_tree")
            if cached is not None and not query:
                return ExecOutcome(True, cached, {"cached": True}, "uia",
                                   duration_ms=(time.perf_counter() - t) * 1000)
            tree = snapshot_tree()
            text = serialize_marks(tree) if not isinstance(tree, str) else tree
            self._cache.set("uia_tree", text)
            return ExecOutcome(True, text, {"query": query, "chars": len(text)}, "uia",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="uia", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    def uia_invoke(self, mark_id: int, action: str = "invoke", text: str = "") -> ExecOutcome:
        t = time.perf_counter()
        try:
            from charlie.desktop.uia import resolve_mark

            el = resolve_mark(mark_id)
            if el is None:
                return ExecOutcome(False, tier="uia", error="mark not found")
            if action == "click":
                el.Click()
            elif action == "type" and text:
                el.SetValue(text) if hasattr(el, "SetValue") else el.SendKeys(text)
            elif action == "invoke":
                el.Invoke() if hasattr(el, "Invoke") else el.Click()
            else:
                return ExecOutcome(False, tier="uia", error=f"unsupported action {action}")
            self._cache.invalidate("uia_tree")
            return ExecOutcome(True, {"mark": mark_id, "action": action}, {}, "uia",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="uia", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    # ---------- Tier 3: browser / DOM ----------

    def browser(self, method: str, params: dict) -> ExecOutcome:
        """Delegate to the one Playwright browser capability (no second agent/brain)."""
        t = time.perf_counter()
        try:
            from charlie.browser import gateway as bg

            fn = {
                "status": bg.status,
                "navigate": lambda: bg.navigate(
                    params.get("url", ""), session=params.get("session", bg.PRIVATE),
                    grant_id=params.get("grant_id", ""),
                    allow_domains=params.get("allow_domains"),
                    wait_until=params.get("wait_until", "domcontentloaded"),
                    timeout_ms=int(params.get("timeout_ms", 30000))),
                "observe": lambda: bg.observe(
                    session=params.get("session", bg.PRIVATE), grant_id=params.get("grant_id", ""),
                    max_marks=int(params.get("max_marks", 40)),
                    text_budget=int(params.get("text_budget", 600)),
                    host=str(params.get("host", ""))),
                "find": lambda: bg.find(
                    params.get("locator", {}), session=params.get("session", bg.PRIVATE),
                    grant_id=params.get("grant_id", ""), limit=int(params.get("limit", 10))),
                "act": lambda: bg.act(
                    params.get("actions", []), session=params.get("session", bg.PRIVATE),
                    grant_id=params.get("grant_id", "")),
                "tabs": lambda: bg.tabs(
                    params.get("op", "list"), session=params.get("session", bg.PRIVATE),
                    index=int(params.get("index", 0)), url=str(params.get("url", "")),
                    grant_id=params.get("grant_id", "")),
                "reset": lambda: bg.reset(session=params.get("session", bg.PRIVATE)),
            }.get(method)
            if fn is None:
                return ExecOutcome(False, tier="browser", error=f"unknown browser method {method}",
                                   duration_ms=(time.perf_counter() - t) * 1000)
            result = fn()
            evidence = {k: v for k, v in result.items() if k not in {"ok", "value", "error"}}
            return ExecOutcome(bool(result.get("ok")), result.get("value"),
                               evidence, "browser", result.get("error"),
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="browser", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    # ---------- Tier 4: screenshot (visual observation) ----------

    def screenshot(self) -> ExecOutcome:
        t = time.perf_counter()
        try:
            import base64
            import io

            import mss
            from PIL import Image

            with mss.mss() as sct:
                raw = sct.grab(sct.monitors[0])
                img = Image.frombytes("RGB", raw.size, raw.rgb)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = base64.b64encode(buf.getvalue()).decode("ascii")
            return ExecOutcome(True, {"mimeType": "image/png", "data": data,
                                      "width": raw.size[0], "height": raw.size[1]},
                               {"bytes": len(data)}, "visual",
                               duration_ms=(time.perf_counter() - t) * 1000)
        except Exception as exc:
            return ExecOutcome(False, tier="visual", error=f"{type(exc).__name__}: {exc}",
                               duration_ms=(time.perf_counter() - t) * 1000)

    # ---------- Adaptive dispatcher ----------

    def dispatch(self, method: str, params: dict) -> ExecOutcome:
        """Select the fastest reliable tier for an operation, with escalation."""
        if method == "windows":
            return self.windows()
        if method == "find_window":
            return self.find_window(params.get("title", ""))
        if method == "focus_window":
            return self.focus_window(params.get("title", ""))
        if method == "app_open":
            return self.app_open(params.get("name", ""))
        if method == "app_close":
            return self.app_close(params.get("name", ""))
        if method == "shell":
            return self.shell(params.get("argv", []), params.get("cwd"),
                              params.get("timeout_ms", 60_000), params.get("env"))
        if method == "list_files":
            return self.list_files(params.get("path", ""))
        if method == "read_file":
            return self.read_file(params.get("path", ""), params.get("max_bytes", 8 * 1024 * 1024))
        if method == "write_file":
            return self.write_file(params.get("path", ""), params.get("content", ""))
        if method == "uia_observe":
            return self.uia_observe(params.get("query", ""))
        if method == "uia_invoke":
            return self.uia_invoke(params.get("mark_id", 0), params.get("action", "invoke"),
                                   params.get("text", ""))
        if method.startswith("browser_"):
            return self.browser(method[len("browser_"):], params)
        if method == "screenshot":
            return self.screenshot()
        return ExecOutcome(False, tier="none", error=f"unknown method {method}")


_executor: Optional[NativeExecutor] = None


def get_executor() -> NativeExecutor:
    """Warm singleton — reuse expensive initialized resources."""
    global _executor
    with threading.Lock():
        if _executor is None:
            _executor = NativeExecutor()
    return _executor
