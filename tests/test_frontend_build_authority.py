import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

import run
from charlie import web_server


@pytest.fixture
def fixture_project():
    base = Path(__file__).parent.parent / ".codex-pytest-tmp"
    base.mkdir(exist_ok=True)
    project = base / f"build-authority-{uuid.uuid4().hex}"
    frontend = project / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "public").mkdir()
    (project / "shared").mkdir()
    (frontend / "src" / "main.tsx").write_text("export {}\n", encoding="utf-8")
    (frontend / "public" / "favicon.svg").write_text("<svg />\n", encoding="utf-8")
    (frontend / "index.html").write_text("<div id='root'></div>\n", encoding="utf-8")
    (frontend / "package.json").write_text("{}\n", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (frontend / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    (frontend / "tsconfig.app.json").write_text("{}\n", encoding="utf-8")
    (project / "shared" / "event_contract.json").write_text("{}\n", encoding="utf-8")
    (project / "shared" / "presentation_contract.json").write_text("{}\n", encoding="utf-8")
    try:
        yield project, frontend
    finally:
        shutil.rmtree(project, ignore_errors=True)


def _write_current_dist(frontend: Path) -> None:
    dist = frontend / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("built\n", encoding="utf-8")
    git_sha, dirty = run._git_build_identity(frontend.parent)
    (dist / "charlie-build.json").write_text(json.dumps({
        "build_id": "test",
        "input_fingerprint": run._frontend_inputs_fingerprint(frontend),
        "git_sha": git_sha,
        "dirty": dirty,
    }) + "\n", encoding="utf-8")


def test_missing_dist_is_stale(fixture_project):
    _, frontend = fixture_project
    assert run._frontend_build_is_stale(frontend, frontend / "dist")


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/main.tsx",
        "public/favicon.svg",
        "index.html",
        "vite.config.ts",
        "package-lock.json",
        "../shared/event_contract.json",
    ],
)
def test_relevant_input_change_invalidates_build(fixture_project, relative_path: str):
    _, frontend = fixture_project
    _write_current_dist(frontend)
    target = (frontend / relative_path).resolve()
    target.write_text(target.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    assert run._frontend_build_is_stale(frontend, frontend / "dist")


def test_unchanged_inputs_are_current(fixture_project):
    _, frontend = fixture_project
    _write_current_dist(frontend)
    assert not run._frontend_build_is_stale(frontend, frontend / "dist")


def test_failed_build_keeps_old_dist_and_does_not_publish_new_identity(fixture_project, monkeypatch):
    project, frontend = fixture_project
    _write_current_dist(frontend)
    old_manifest = (frontend / "dist" / "charlie-build.json").read_text(encoding="utf-8")
    (frontend / "src" / "main.tsx").write_text("changed\n", encoding="utf-8")

    monkeypatch.setattr(run.shutil, "which", lambda name: "npm.exe")

    def fail_build(*args, **kwargs):
        if args and args[0][-2:] == ["run", "build"]:
            raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "run", fail_build)
    with pytest.raises(RuntimeError, match="Frontend build failed"):
        run.check_and_build_frontend(project)
    assert (frontend / "dist" / "charlie-build.json").read_text(encoding="utf-8") == old_manifest
    assert not list(frontend.glob(".charlie-build-*"))


def test_status_exposes_safe_frontend_identity(fixture_project, monkeypatch):
    _, frontend = fixture_project
    _write_current_dist(frontend)
    monkeypatch.setattr(web_server, "_FRONTEND_DIST", frontend / "dist")
    result = asyncio.run(web_server.status())
    assert result["launch_id"] == web_server.LAUNCH_ID
    assert result["pid"] > 0
    assert result["frontend_build"]["build_id"] == "test"
    assert result["source_identity"] == run._git_build_identity(frontend.parent)[0]
