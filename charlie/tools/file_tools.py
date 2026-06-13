"""File tools — file search, read, write, clipboard operations."""

import os
import glob
from pathlib import Path

from charlie.tools.tool_decorator import tool, RiskTier


# Compute project root at module load time (CWD-independent)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _is_within_project(path: str) -> bool:
    """Check that a path resolves within the project root."""
    try:
        p = Path(path).resolve()
        return p.is_relative_to(_PROJECT_ROOT)
    except (ValueError, TypeError):
        return False


@tool(
    name="read_file",
    description="Read the contents of a file",
    category="file",
)
def read_file(path: str, max_lines: int = 100) -> str:
    if not _is_within_project(path):
        return f"Error: Path '{path}' is outside the project directory."
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        content = "".join(lines[:max_lines])
        if len(lines) > max_lines:
            content += f"\n... ({len(lines) - max_lines} more lines)"
        return content
    except Exception as e:
        return f"Error reading {path}: {e}"


@tool(
    name="write_file",
    description="Write content to a file",
    risk_tier=RiskTier.TIER_1,
    category="file",
)
def write_file(path: str, content: str) -> str:
    if not _is_within_project(path):
        return f"Error: Path '{path}' is outside the project directory."
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


@tool(
    name="list_files",
    description="List files in a directory",
    category="file",
)
def list_files(path: str = ".", pattern: str = "*") -> str:
    """List files matching a pattern."""
    if not _is_within_project(path):
        return f"Error: Path '{path}' is outside the project directory."
    try:
        full_pattern = os.path.join(path, pattern)
        files = glob.glob(full_pattern, recursive=True)
        if not files:
            return f"No files matching {pattern} in {path}"
        return "\n".join(files[:50])
    except Exception as e:
        return f"Error listing files: {e}"


@tool(
    name="search_files",
    description="Search for files by name pattern",
    category="file",
)
def search_files(pattern: str, root: str = ".") -> str:
    """Search for files recursively."""
    if not _is_within_project(root):
        return f"Error: Root '{root}' is outside the project directory."
    try:
        matches = []
        for dirpath, dirnames, filenames in os.walk(root):
            for f in filenames:
                if pattern.lower() in f.lower():
                    matches.append(os.path.join(dirpath, f))
            if len(matches) > 50:
                break
        return "\n".join(matches) if matches else f"No files matching '{pattern}'"
    except Exception as e:
        return f"Error searching: {e}"


@tool(
    name="copy_to_clipboard",
    description="Copy text to the system clipboard",
    category="file",
)
def copy_to_clipboard(text: str) -> str:
    try:
        import pyperclip

        pyperclip.copy(text)
        return f"Copied {len(text)} chars to clipboard"
    except ImportError:
        return "pyperclip not installed"
    except Exception as e:
        return f"Clipboard error: {e}"


@tool(
    name="read_clipboard",
    description="Read text from the system clipboard",
    category="file",
)
def read_clipboard() -> str:
    try:
        import pyperclip

        text = pyperclip.paste()
        return text if text else "Clipboard is empty"
    except ImportError:
        return "pyperclip not installed"
    except Exception as e:
        return f"Clipboard error: {e}"


@tool(
    name="get_file_info",
    description="Get metadata about a file (size, modified date, etc.)",
    category="file",
)
def get_file_info(path: str) -> str:
    if not _is_within_project(path):
        return f"Error: Path '{path}' is outside the project directory."
    try:
        stat = os.stat(path)
        size = stat.st_size
        if size > 1024 * 1024:
            size_str = f"{size / (1024 * 1024):.1f} MB"
        elif size > 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size} bytes"

        import time

        modified = time.ctime(stat.st_mtime)

        return f"Path: {path}\nSize: {size_str}\nModified: {modified}\nType: {'Directory' if os.path.isdir(path) else 'File'}"
    except Exception as e:
        return f"Error getting file info: {e}"


@tool(
    name="code_analyze",
    description="Analyze code structure and provide insights",
    category="file",
)
def code_analyze(path: str) -> str:
    if not _is_within_project(path):
        return f"Error: Path '{path}' is outside the project directory."
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        lines = content.split("\n")
        total = len(lines)
        blanks = sum(1 for l in lines if not l.strip())
        comments = sum(1 for l in lines if l.strip().startswith("#"))

        imports = [l.strip() for l in lines if l.strip().startswith(("import ", "from "))]

        return (
            f"File: {path}\n"
            f"Lines: {total} ({total - blanks} code, {blanks} blank, {comments} comments)\n"
            f"Imports: {len(imports)}\n" + "\n".join(f"  - {i}" for i in imports[:10])
        )
    except Exception as e:
        return f"Error analyzing {path}: {e}"


@tool(
    name="code_search",
    description="Search for patterns in code files",
    category="file",
)
def code_search(pattern: str, path: str = ".", file_types: str = ".py,.js,.ts") -> str:
    from charlie.security.safety_guard import check_path_boundary

    allowed, msg = check_path_boundary(path)
    if not allowed:
        return msg

    import re

    try:
        extensions = [t.strip() for t in file_types.split(",")]
        matches = []
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                if any(f.endswith(ext) for ext in extensions):
                    filepath = os.path.join(dirpath, f)
                    try:
                        with open(filepath, "r", encoding="utf-8") as fh:
                            for i, line in enumerate(fh, 1):
                                if re.search(re.escape(pattern), line, re.IGNORECASE):
                                    matches.append(f"{filepath}:{i}: {line.strip()}")
                    except Exception:
                        continue
            if len(matches) > 30:
                break
        return "\n".join(matches) if matches else f"No matches for '{pattern}'"
    except Exception as e:
        return f"Error searching code: {e}"


@tool(
    name="find_sensitive_data",
    description="Scan a file for sensitive data (API keys, passwords, etc.)",
    category="file",
)
def find_sensitive_data(path: str) -> str:
    if not _is_within_project(path):
        return f"Error: Path '{path}' is outside the project directory."
    import re

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        patterns = {
            "API Key": r'(?i)(api[_-]?key|apikey)\s*[:=]\s*["\'][^"\']{10,}["\']',
            "Password": r'(?i)(password|passwd|pwd)\s*[:=]\s*["\'][^"\']+["\']',
            "Token": r'(?i)(token|secret)\s*[:=]\s*["\'][^"\']{10,}["\']',
            "AWS Key": r"AKIA[0-9A-Z]{16}",
            "Private Key": r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----",
        }

        findings = []
        for name, pattern in patterns.items():
            if re.search(pattern, content):
                findings.append(f"  WARNING: {name} detected")

        if findings:
            return f"Sensitive data found in {path}:\n" + "\n".join(findings)
        return f"No sensitive data detected in {path}"
    except Exception as e:
        return f"Error scanning {path}: {e}"
