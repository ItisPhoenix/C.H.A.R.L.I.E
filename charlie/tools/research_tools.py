"""Pattern A research tools.

Migrated from ``charlie.tools.research_analyzer.AdvancedResearchToolkit``
after that wrapper class was found to be effectively dead: only
``analyze_dependencies`` was ever called (via
``charlie/brain/tool_handler.py:_tool_deps_analyze``), and the other
three ``@risk_tier`` methods plus 14 helper methods had no callers.

This module exposes the one still-useful operation as a discoverable
``@tool`` function so the LLM gets a proper JSON schema instead of an
empty ``{"type": "object", "properties": {}}``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from charlie.security.tiers import RiskTier
from charlie.tools.tool_decorator import tool

logger = logging.getLogger("charlie.tools.research_tools")


# ── Helpers (module-level, were private methods on AdvancedResearchToolkit)

def _analyze_requirements(req_file: Path) -> list[dict[str, Any]]:
    """Parse a ``requirements.txt`` file into ``[{name, version, type}]``."""
    deps: list[dict[str, Any]] = []
    try:
        with open(req_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "==" in line:
                    package, version = line.split("==", 1)
                    deps.append({"name": package.strip(), "version": version.strip(), "type": "python"})
    except Exception as e:
        logger.debug("analyze_requirements_failed | %s | %s", req_file, e)
    return deps


def _analyze_pyproject(pyproject_file: Path) -> list[dict[str, Any]]:
    """Extract dependencies from ``pyproject.toml``."""
    deps: list[dict[str, Any]] = []
    try:
        import tomllib

        with open(pyproject_file, "rb") as f:
            data = tomllib.load(f)

        # Walk nested keys (tomllib returns a real nested dict, so dotted
        # key access via ``data.get("project.dependencies")`` would never
        # match).
        project_section = data.get("project", {})
        poetry_section = data.get("tool", {}).get("poetry", {})

        for deps_section in (project_section.get("dependencies"), poetry_section.get("dependencies")):
            if not deps_section:
                continue
            # project.dependencies is a list, poetry is a dict.
            if isinstance(deps_section, list):
                for dep in deps_section:
                    deps.append({"name": dep, "version": "latest", "type": "python"})
            elif isinstance(deps_section, dict):
                for name, version in deps_section.items():
                    deps.append({"name": name, "version": str(version), "type": "python"})
    except Exception as e:
        logger.debug("analyze_pyproject_failed | %s | %s", pyproject_file, e)
    return deps


def _analyze_package_json(package_file: Path) -> list[dict[str, Any]]:
    """Extract dependencies from ``package.json`` (regular + dev)."""
    deps: list[dict[str, Any]] = []
    try:
        with open(package_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        all_deps: dict[str, Any] = {}
        all_deps.update(data.get("dependencies", {}))
        all_deps.update(data.get("devDependencies", {}))

        for name, version in all_deps.items():
            deps.append({"name": name, "version": str(version), "type": "javascript"})
    except Exception as e:
        logger.debug("analyze_package_json_failed | %s | %s", package_file, e)
    return deps


def _format_dependency_analysis(deps: dict[str, list[dict[str, Any]]]) -> str:
    """Render the dependency list as a human-readable report."""
    output = "📦 Dependency Analysis:\n\n"
    for lang, packages in deps.items():
        if not packages:
            continue
        output += f"{lang.upper()} Dependencies ({len(packages)}):\n"
        for pkg in packages[:20]:
            output += f"  {pkg['name']}: {pkg.get('version', 'latest')}\n"
        output += "\n"
    return output


# ── The one Pattern A tool ──────────────────────────────────────────────

@tool(
    name="analyze_dependencies",
    description=(
        "Scan a project directory for ``requirements.txt``, ``pyproject.toml``, "
        "and ``package.json`` and report the declared dependencies with their "
        "pinned versions. Use to audit what a project actually depends on."
    ),
    category="research",
    risk_tier=RiskTier.TIER_0,
    timeout=15,
)
def analyze_dependencies(path: str = ".") -> str:
    """Scan *path* for dependency manifests and return a formatted report.

    Parameters
    ----------
    path : str
        Project root to scan (default: current directory).
    """
    root = Path(path)
    if not root.exists():
        return f"Path does not exist: {path}"

    deps: dict[str, list[dict[str, Any]]] = {"python": [], "javascript": []}

    try:
        if (root / "requirements.txt").exists():
            deps["python"].extend(_analyze_requirements(root / "requirements.txt"))
        if (root / "pyproject.toml").exists():
            deps["python"].extend(_analyze_pyproject(root / "pyproject.toml"))
        if (root / "package.json").exists():
            deps["javascript"].extend(_analyze_package_json(root / "package.json"))
    except Exception as e:
        return f"Dependency analysis failed: {e}"

    if not any(deps.values()):
        return f"No dependency manifests (requirements.txt, pyproject.toml, package.json) found under {path}."

    return _format_dependency_analysis(deps)
