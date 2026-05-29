"""Coding tools — run code, git, project analysis."""

import os
import subprocess

from charlie.tools.tool_decorator import tool, RiskTier


@tool(
    name="run_code",
    description="Execute code in a sandboxed environment",
    risk_tier=RiskTier.TIER_2,
    category="coding",
)
def run_code(language: str, code: str, timeout: int = 30) -> str:
    """Execute code and return output."""
    if language == "python":
        try:
            result = subprocess.run(
                ["python", "-c", code],
                capture_output=True, text=True, timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output[:2000]
        except subprocess.TimeoutExpired:
            return f"Code timed out after {timeout}s"
        except Exception as e:
            return f"Execution failed: {e}"
    return f"Language '{language}' not supported (only Python)"


@tool(
    name="git_status",
    description="Get git repository status",
    category="coding",
)
def git_status(repo_path: str = ".") -> str:
    """Get git status of a repository."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        return result.stdout or "Clean working tree"
    except Exception as e:
        return f"Git error: {e}"


@tool(
    name="git_log",
    description="Get recent git commit history",
    category="coding",
)
def git_log(repo_path: str = ".", count: int = 10) -> str:
    """Get recent git commits."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{count}"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        return result.stdout or "No commits found"
    except Exception as e:
        return f"Git error: {e}"


@tool(
    name="git_diff",
    description="Get git diff for staged or unstaged changes",
    category="coding",
)
def git_diff(repo_path: str = ".", staged: bool = False) -> str:
    """Get git diff."""
    try:
        cmd = ["git", "diff", "--stat"]
        if staged:
            cmd.append("--staged")
        result = subprocess.run(
            cmd, cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        return result.stdout or "No changes"
    except Exception as e:
        return f"Git error: {e}"


@tool(
    name="git_commit",
    description="Create a git commit with a message",
    risk_tier=RiskTier.TIER_2,
    category="coding",
)
def git_commit(message: str, repo_path: str = ".") -> str:
    """Create a git commit."""
    try:
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        return result.stdout or result.stderr
    except Exception as e:
        return f"Git error: {e}"


@tool(
    name="project_analyze",
    description="Analyze a project directory structure and tech stack",
    category="coding",
)
def project_analyze(path: str = ".") -> str:
    """Analyze a project's structure."""
    try:
        files = []
        for root, dirs, filenames in os.walk(path):
            # Skip hidden dirs and common non-project dirs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv")]
            for f in filenames:
                files.append(os.path.join(root, f))

        # Count by extension
        exts = {}
        for f in files:
            ext = os.path.splitext(f)[1] or "no-ext"
            exts[ext] = exts.get(ext, 0) + 1

        # Detect tech stack
        stack = []
        if any(f.endswith(".py") for f in files):
            stack.append("Python")
        if any(f.endswith((".js", ".ts")) for f in files):
            stack.append("JavaScript/TypeScript")
        if any("package.json" in f for f in files):
            stack.append("Node.js")
        if any("requirements.txt" in f or "pyproject.toml" in f for f in files):
            stack.append("Python (pip/uv)")
        if any("Cargo.toml" in f for f in files):
            stack.append("Rust")
        if any("go.mod" in f for f in files):
            stack.append("Go")

        return (
            f"Project: {path}\n"
            f"Files: {len(files)}\n"
            f"Tech stack: {', '.join(stack) or 'Unknown'}\n"
            f"Extensions: {', '.join(f'{ext}({count})' for ext, count in sorted(exts.items(), key=lambda x: -x[1])[:10])}"
        )
    except Exception as e:
        return f"Analysis failed: {e}"


@tool(
    name="read_dependencies",
    description="Read and parse project dependencies from requirements.txt or pyproject.toml",
    category="coding",
)
def read_dependencies(path: str = ".") -> str:
    """Read project dependencies."""
    req_file = os.path.join(path, "requirements.txt")
    pyproject = os.path.join(path, "pyproject.toml")

    if os.path.exists(req_file):
        with open(req_file, "r") as f:
            return f.read()
    elif os.path.exists(pyproject):
        with open(pyproject, "r") as f:
            content = f.read()
        # Extract dependencies section
        if "[project.dependencies]" in content:
            start = content.index("[project.dependencies]")
            return content[start:start + 1000]
        return content[:1000]
    return "No dependency file found"


@tool(
    name="terminal_command",
    description="Run a terminal/shell command",
    risk_tier=RiskTier.TIER_2,
    category="coding",
)
def terminal_command(command: str, timeout: int = 30) -> str:
    """Run a shell command."""
    from charlie.utils.command_validator import validate_command
    try:
        validate_command(command)
    except ValueError as e:
        return str(e)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"
        return output[:2000]
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Command failed: {e}"


@tool(
    name="parse_json",
    description="Parse and format a JSON string",
    category="coding",
)
def parse_json(json_string: str) -> str:
    """Parse and pretty-print JSON."""
    import json
    try:
        data = json.loads(json_string)
        return json.dumps(data, indent=2, ensure_ascii=False)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"


@tool(
    name="format_code",
    description="Format Python code using ruff",
    category="coding",
)
def format_code(path: str) -> str:
    """Format Python code."""
    try:
        result = subprocess.run(
            ["ruff", "format", path],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout or "Formatted"
    except Exception as e:
        return f"Format failed: {e}"
