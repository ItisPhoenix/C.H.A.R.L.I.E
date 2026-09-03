import asyncio
import json
import os
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


def _write_current_dist(frontend: Path, dist: Path | None = None) -> None:
    dist = dist or frontend / "dist"
    dist.mkdir(parents=True)
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


def test_runtime_cache_is_authority_even_when_canonical_dist_is_current(fixture_project, monkeypatch, tmp_path):
    project, frontend = fixture_project
    runtime_dist = tmp_path / "runtime" / "frontend-dist"
    _write_current_dist(frontend, runtime_dist)
    runtime_manifest = json.loads((runtime_dist / "charlie-build.json").read_text(encoding="utf-8"))
    runtime_manifest["authority"] = "user_runtime_cache"
    (runtime_dist / "charlie-build.json").write_text(json.dumps(runtime_manifest), encoding="utf-8")
    _write_current_dist(frontend)
    monkeypatch.setattr(run, "_persistent_frontend_dist", lambda _: runtime_dist)

    run.check_and_build_frontend(project)

    assert Path(os.environ[run._FRONTEND_DIST_ENV]) == runtime_dist
    os.environ.pop(run._FRONTEND_DIST_ENV, None)


def test_acl_protected_preferred_cache_uses_stable_user_fallback(fixture_project, monkeypatch, tmp_path):
    project, frontend = fixture_project
    preferred = tmp_path / "preferred" / "frontend-dist"
    fallback = tmp_path / "fallback" / "frontend-dist"
    monkeypatch.setattr(run, "_persistent_frontend_dist", lambda _: preferred)
    monkeypatch.setattr(run, "_temporary_frontend_dist", lambda _: fallback)
    monkeypatch.setattr(run, "_frontend_dist_is_user_accessible", lambda path: path != preferred.parent)
    monkeypatch.setattr(run.shutil, "which", lambda name: "npm.exe")

    def fake_build(args, *, cwd, check, env=None):
        if args[-2:] == ["run", "build"]:
            staging = Path(env["CHARLIE_FRONTEND_OUT_DIR"])
            (staging / "index.html").write_text("built\n", encoding="utf-8")
            (staging / "charlie-build.json").write_text(json.dumps({"build_id": "fallback"}), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", fake_build)
    run.check_and_build_frontend(project)

    assert Path(os.environ[run._FRONTEND_DIST_ENV]) == fallback
    assert (fallback / "index.html").is_file()
    os.environ.pop(run._FRONTEND_DIST_ENV, None)


def test_failed_build_keeps_old_dist_and_does_not_publish_new_identity(fixture_project, monkeypatch, tmp_path):
    project, frontend = fixture_project
    runtime_dist = tmp_path / "runtime" / "frontend-dist"
    monkeypatch.setattr(run, "_persistent_frontend_dist", lambda _: runtime_dist)
    _write_current_dist(frontend, runtime_dist)
    old_manifest = (runtime_dist / "charlie-build.json").read_text(encoding="utf-8")
    (frontend / "src" / "main.tsx").write_text("changed\n", encoding="utf-8")

    monkeypatch.setattr(run.shutil, "which", lambda name: "npm.exe")

    def fail_build(*args, **kwargs):
        if args and args[0][-2:] == ["run", "build"]:
            raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "run", fail_build)
    with pytest.raises(RuntimeError, match="Frontend build failed"):
        run.check_and_build_frontend(project)
    assert (runtime_dist / "charlie-build.json").read_text(encoding="utf-8") == old_manifest
    assert not list(frontend.glob(".charlie-build-*"))


def test_inaccessible_dist_uses_persistent_user_runtime_build(fixture_project, monkeypatch, tmp_path):
    project, frontend = fixture_project
    runtime_dist = tmp_path / "C.H.A.R.L.I.E" / "frontend-dist"
    build_calls = []
    monkeypatch.setattr(run, "_persistent_frontend_dist", lambda _: runtime_dist)
    monkeypatch.setattr(run, "_frontend_dist_is_user_accessible", lambda path: path != frontend / "dist")
    monkeypatch.setattr(run.shutil, "which", lambda name: "npm.exe")

    def fake_build(args, *, cwd, check, env=None):
        if args[-2:] != ["run", "build"]:
            return
        assert env is not None
        build_calls.append(args)
        staging = Path(env["CHARLIE_FRONTEND_OUT_DIR"])
        (staging / "assets").mkdir(parents=True)
        (staging / "index.html").write_text("built\n", encoding="utf-8")
        (staging / "assets" / "bundle.js").write_text("bundle\n", encoding="utf-8")
        (staging / "charlie-build.json").write_text(
            json.dumps({"build_id": "runtime-fallback"}) + "\n", encoding="utf-8"
        )

    monkeypatch.setattr(subprocess, "run", fake_build)
    run.check_and_build_frontend(project)

    assert Path(os.environ[run._FRONTEND_DIST_ENV]) == runtime_dist
    assert (runtime_dist / "index.html").read_text(encoding="utf-8") == "built\n"
    assert len(build_calls) == 1

    run.check_and_build_frontend(project)
    assert len(build_calls) == 1
    os.environ.pop(run._FRONTEND_DIST_ENV, None)


def test_frontend_source_change_rebuilds_persistent_user_runtime_build(fixture_project, monkeypatch, tmp_path):
    project, frontend = fixture_project
    runtime_dist = tmp_path / "runtime" / "frontend-dist"
    build_calls = []
    monkeypatch.setattr(run, "_persistent_frontend_dist", lambda _: runtime_dist)
    monkeypatch.setattr(run, "_frontend_dist_is_user_accessible", lambda path: path != frontend / "dist")
    monkeypatch.setattr(run.shutil, "which", lambda name: "npm.exe")

    def fake_build(args, *, cwd, check, env=None):
        if args[-2:] == ["run", "build"]:
            build_calls.append(args)
            staging = Path(env["CHARLIE_FRONTEND_OUT_DIR"])
            (staging / "assets").mkdir(parents=True)
            (staging / "index.html").write_text("built\n", encoding="utf-8")
            (staging / "charlie-build.json").write_text(
                json.dumps({"build_id": str(len(build_calls))}), encoding="utf-8"
            )

    monkeypatch.setattr(subprocess, "run", fake_build)
    run.check_and_build_frontend(project)
    (frontend / "src" / "main.tsx").write_text("export const changed = true;\n", encoding="utf-8")
    run.check_and_build_frontend(project)

    assert len(build_calls) == 2
    os.environ.pop(run._FRONTEND_DIST_ENV, None)


def test_shared_contract_change_rebuilds_persistent_user_runtime_build(fixture_project, monkeypatch, tmp_path):
    project, frontend = fixture_project
    runtime_dist = tmp_path / "runtime" / "frontend-dist"
    build_calls = []
    monkeypatch.setattr(run, "_persistent_frontend_dist", lambda _: runtime_dist)
    monkeypatch.setattr(run, "_frontend_dist_is_user_accessible", lambda path: path != frontend / "dist")
    monkeypatch.setattr(run.shutil, "which", lambda name: "npm.exe")

    def fake_build(args, *, cwd, check, env=None):
        if args[-2:] == ["run", "build"]:
            build_calls.append(args)
            staging = Path(env["CHARLIE_FRONTEND_OUT_DIR"])
            (staging / "index.html").write_text("built\n", encoding="utf-8")
            (staging / "charlie-build.json").write_text(
                json.dumps({"build_id": str(len(build_calls))}), encoding="utf-8"
            )

    monkeypatch.setattr(subprocess, "run", fake_build)
    run.check_and_build_frontend(project)
    (project / "shared" / "event_contract.json").write_text('{"changed": true}\n', encoding="utf-8")
    run.check_and_build_frontend(project)

    assert len(build_calls) == 2
    os.environ.pop(run._FRONTEND_DIST_ENV, None)


def test_backend_only_git_identity_does_not_invalidate_frontend_build(fixture_project, monkeypatch):
    _, frontend = fixture_project
    _write_current_dist(frontend)
    monkeypatch.setattr(run, "_git_build_identity", lambda _: ("different-backend-commit", True))
    assert not run._frontend_build_is_stale(frontend, frontend / "dist")


def test_invalid_persistent_cache_is_rebuilt(fixture_project, monkeypatch, tmp_path):
    project, frontend = fixture_project
    runtime_dist = tmp_path / "runtime" / "frontend-dist"
    runtime_dist.mkdir(parents=True)
    (runtime_dist / "index.html").write_text("stale\n", encoding="utf-8")
    build_calls = []
    monkeypatch.setattr(run, "_persistent_frontend_dist", lambda _: runtime_dist)
    monkeypatch.setattr(run, "_frontend_dist_is_user_accessible", lambda path: path != frontend / "dist")
    monkeypatch.setattr(run.shutil, "which", lambda name: "npm.exe")

    def fake_build(args, *, cwd, check, env=None):
        if args[-2:] == ["run", "build"]:
            build_calls.append(args)
            staging = Path(env["CHARLIE_FRONTEND_OUT_DIR"])
            (staging / "index.html").write_text("rebuilt\n", encoding="utf-8")
            (staging / "charlie-build.json").write_text(json.dumps({"build_id": "rebuilt"}), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", fake_build)
    run.check_and_build_frontend(project)

    assert len(build_calls) == 1
    assert (runtime_dist / "index.html").read_text(encoding="utf-8") == "rebuilt\n"
    os.environ.pop(run._FRONTEND_DIST_ENV, None)


def test_failed_runtime_rebuild_preserves_last_verified_cache(fixture_project, monkeypatch, tmp_path):
    project, frontend = fixture_project
    runtime_dist = tmp_path / "runtime" / "frontend-dist"
    build_calls = []
    monkeypatch.setattr(run, "_persistent_frontend_dist", lambda _: runtime_dist)
    monkeypatch.setattr(run, "_frontend_dist_is_user_accessible", lambda path: path != frontend / "dist")
    monkeypatch.setattr(run.shutil, "which", lambda name: "npm.exe")

    def successful_build(args, *, cwd, check, env=None):
        if args[-2:] == ["run", "build"]:
            build_calls.append(args)
            staging = Path(env["CHARLIE_FRONTEND_OUT_DIR"])
            (staging / "index.html").write_text("verified\n", encoding="utf-8")
            (staging / "charlie-build.json").write_text(json.dumps({"build_id": "verified"}), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", successful_build)
    run.check_and_build_frontend(project)
    old_manifest = (runtime_dist / "charlie-build.json").read_text(encoding="utf-8")
    (frontend / "src" / "main.tsx").write_text("changed\n", encoding="utf-8")

    def failed_build(args, *, cwd, check, env=None):
        if args[-2:] == ["run", "build"]:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(subprocess, "run", failed_build)
    with pytest.raises(RuntimeError, match="Frontend build failed"):
        run.check_and_build_frontend(project)

    assert len(build_calls) == 1
    assert (runtime_dist / "charlie-build.json").read_text(encoding="utf-8") == old_manifest
    os.environ.pop(run._FRONTEND_DIST_ENV, None)


def test_status_exposes_safe_frontend_identity(fixture_project, monkeypatch):
    _, frontend = fixture_project
    _write_current_dist(frontend)
    monkeypatch.setattr(web_server, "_FRONTEND_DIST", frontend / "dist")
    result = asyncio.run(web_server.status())
    assert result["launch_id"] == web_server.LAUNCH_ID
    assert result["pid"] > 0
    assert result["frontend_build"]["build_id"] == "test"
    assert result["source_identity"] == run._git_build_identity(frontend.parent)[0]
