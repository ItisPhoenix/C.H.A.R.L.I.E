"""Incremental CodeIndex for Charlie V1 repository code introspection.

Provides deterministic, AST-grounded indexing for Charlie's Python backend
and TypeScript/React frontend source with incremental refresh, path-safe excerpts,
symbol discovery, and secret file exclusion.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("charlie.code_index")

# Ignored directory names
_IGNORED_DIRS: frozenset[str] = frozenset({
    ".git",
    ".venv",
    "venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".gemini",
    "artifacts",
    "scratch",
    "temp",
})

# Ignored file patterns and prefixes
_IGNORED_FILE_PREFIXES: Tuple[str, ...] = (
    ".env",
    "id_rsa",
    "credentials",
)

_IGNORED_FILE_EXTENSIONS: frozenset[str] = frozenset({
    ".key",
    ".pem",
    ".onnx",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".mp3",
    ".wav",
    ".ogg",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".exe",
    ".dll",
    ".pyd",
    ".so",
    ".dylib",
    ".pdf",
    ".bin",
})

_SUPPORTED_EXTENSIONS: Dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
}


@dataclass
class SymbolInfo:
    """Represents one indexed code symbol (class, function, method, interface, etc.)."""

    name: str
    kind: str  # class, method, function, component, interface, type, store, const
    file_path: str  # relative to repo root
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    container: Optional[str] = None
    is_exported: bool = True
    language: str = "python"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FileIndexInfo:
    """Metadata and indexed symbols for one source file."""

    file_path: str
    language: str
    mtime: float
    size_bytes: int
    content_hash: str
    symbols: List[SymbolInfo] = field(default_factory=list)
    docstring: Optional[str] = None
    last_indexed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["symbols"] = [s.to_dict() for s in self.symbols]
        return d


class CodeIndex:
    """Incremental, AST-grounded repository index for Charlie's Python and TypeScript code."""

    def __init__(self, repo_root: Optional[Path | str] = None) -> None:
        if repo_root is None:
            # Default to root of current Charlie project
            repo_root = Path(__file__).resolve().parent.parent
        self.repo_root = Path(repo_root).resolve()
        self._files: Dict[str, FileIndexInfo] = {}
        self._symbols_by_name: Dict[str, List[SymbolInfo]] = {}
        self._last_refresh: float = 0.0

    # -------------------------------------------------------------------------
    # Path & Security Boundaries
    # -------------------------------------------------------------------------

    def _resolve_safe_path(self, rel_or_abs_path: str | Path) -> Path:
        """Resolve a path and verify it is strictly within self.repo_root.

        Raises ValueError if path attempts traversal escaping repo_root.
        """
        p = Path(rel_or_abs_path)
        if not p.is_absolute():
            p = (self.repo_root / p).resolve()
        else:
            p = p.resolve()

        try:
            p.relative_to(self.repo_root)
        except ValueError:
            raise ValueError(f"Access denied: Path '{rel_or_abs_path}' escapes repository boundary.")

        # Check symlink target if it exists
        if p.is_symlink():
            target = p.resolve()
            try:
                target.relative_to(self.repo_root)
            except ValueError:
                raise ValueError(f"Access denied: Symlink '{rel_or_abs_path}' points outside repository boundary.")

        return p

    def is_indexable_file(self, rel_path_str: str) -> bool:
        """Check whether a relative file path is safe and indexable."""
        path_obj = Path(rel_path_str)
        parts = path_obj.parts

        # Check directory exclusions
        for part in parts[:-1]:
            if part in _IGNORED_DIRS or part.startswith("."):
                return False

        filename = parts[-1] if parts else ""
        if filename.startswith(".") and filename not in (".gitignore", ".env.example"):
            return False

        for prefix in _IGNORED_FILE_PREFIXES:
            if filename.startswith(prefix):
                return False

        ext = path_obj.suffix.lower()
        if ext in _IGNORED_FILE_EXTENSIONS:
            return False

        return ext in _SUPPORTED_EXTENSIONS

    # -------------------------------------------------------------------------
    # AST and Source Parsing
    # -------------------------------------------------------------------------

    def _parse_python_source(self, rel_path: str, content: str) -> Tuple[Optional[str], List[SymbolInfo]]:
        """Parse Python source using stdlib AST."""
        symbols: List[SymbolInfo] = []
        module_doc: Optional[str] = None

        try:
            tree = ast.parse(content, filename=rel_path)
        except SyntaxError as e:
            logger.debug("Syntax error in %s: %s", rel_path, e)
            return None, []
        except Exception as e:
            logger.debug("Failed parsing %s: %s", rel_path, e)
            return None, []

        module_doc = ast.get_docstring(tree)

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                cls_doc = ast.get_docstring(node)
                end_line = getattr(node, "end_lineno", node.lineno)
                symbols.append(
                    SymbolInfo(
                        name=node.name,
                        kind="class",
                        file_path=rel_path,
                        start_line=node.lineno,
                        end_line=end_line,
                        docstring=cls_doc,
                        language="python",
                    )
                )

                # Class methods
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_doc = ast.get_docstring(sub)
                        sub_end = getattr(sub, "end_lineno", sub.lineno)
                        symbols.append(
                            SymbolInfo(
                                name=sub.name,
                                kind="method",
                                file_path=rel_path,
                                start_line=sub.lineno,
                                end_line=sub_end,
                                docstring=method_doc,
                                container=node.name,
                                language="python",
                            )
                        )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_doc = ast.get_docstring(node)
                end_line = getattr(node, "end_lineno", node.lineno)
                symbols.append(
                    SymbolInfo(
                        name=node.name,
                        kind="function",
                        file_path=rel_path,
                        start_line=node.lineno,
                        end_line=end_line,
                        docstring=fn_doc,
                        language="python",
                    )
                )

        return module_doc, symbols

    def _parse_typescript_source(self, rel_path: str, content: str) -> Tuple[Optional[str], List[SymbolInfo]]:
        """Extract symbols from TypeScript / TSX files deterministically."""
        symbols: List[SymbolInfo] = []
        lines = content.splitlines()
        lang = "tsx" if rel_path.endswith(".tsx") else "typescript"

        # Look for leading JSDoc / module docstring
        module_doc: Optional[str] = None
        m_doc = re.search(r"/\*\*(.*?)\*/", content, re.DOTALL)
        if m_doc and content.startswith(m_doc.group(0)):
            module_doc = m_doc.group(1).strip()

        # Regex patterns for TS/TSX declarations
        re_iface = re.compile(r"^(?:export\s+)?interface\s+([A-Za-z0-9_]+)")
        re_type = re.compile(r"^(?:export\s+)?type\s+([A-Za-z0-9_]+)\s*=")
        re_class = re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_]+)")
        re_func = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)")
        re_const = re.compile(r"^export\s+const\s+([A-Za-z0-9_]+)\s*(?::\s*[^=]+)?\s*=\s*(.*)")

        for idx, line in enumerate(lines):
            lineno = idx + 1
            line_s = line.strip()

            # Find matching docstring in preceding lines
            doc_str: Optional[str] = None
            if idx > 0 and lines[idx - 1].strip().endswith("*/"):
                doc_lines = []
                back_idx = idx - 1
                while back_idx >= 0:
                    b_line = lines[back_idx].strip()
                    doc_lines.insert(0, b_line)
                    if b_line.startswith("/**"):
                        break
                    back_idx -= 1
                if doc_lines and doc_lines[0].startswith("/**"):
                    doc_str = "\n".join(doc_lines).replace("/**", "").replace("*/", "").replace("*", "").strip()

            # 1. Interface
            m = re_iface.match(line_s)
            if m:
                name = m.group(1)
                symbols.append(
                    SymbolInfo(
                        name=name,
                        kind="interface",
                        file_path=rel_path,
                        start_line=lineno,
                        end_line=lineno,
                        docstring=doc_str,
                        language=lang,
                    )
                )
                continue

            # 2. Type
            m = re_type.match(line_s)
            if m:
                name = m.group(1)
                symbols.append(
                    SymbolInfo(
                        name=name,
                        kind="type",
                        file_path=rel_path,
                        start_line=lineno,
                        end_line=lineno,
                        docstring=doc_str,
                        language=lang,
                    )
                )
                continue

            # 3. Class
            m = re_class.match(line_s)
            if m:
                name = m.group(1)
                symbols.append(
                    SymbolInfo(
                        name=name,
                        kind="class",
                        file_path=rel_path,
                        start_line=lineno,
                        end_line=lineno,
                        docstring=doc_str,
                        language=lang,
                    )
                )
                continue

            # 4. Function
            m = re_func.match(line_s)
            if m:
                name = m.group(1)
                kind = "component" if name[0].isupper() else "function"
                symbols.append(
                    SymbolInfo(
                        name=name,
                        kind=kind,
                        file_path=rel_path,
                        start_line=lineno,
                        end_line=lineno,
                        docstring=doc_str,
                        language=lang,
                    )
                )
                continue

            # 5. Const declarations (React.FC, create Zustand store, hooks)
            m = re_const.match(line_s)
            if m:
                name = m.group(1)
                rhs = m.group(2)
                kind = "const"
                if name.startswith("use") and "create" in rhs:
                    kind = "store"
                elif name.startswith("use"):
                    kind = "hook"
                elif name[0].isupper():
                    kind = "component"
                elif "create(" in rhs or "createStore(" in rhs:
                    kind = "store"

                symbols.append(
                    SymbolInfo(
                        name=name,
                        kind=kind,
                        file_path=rel_path,
                        start_line=lineno,
                        end_line=lineno,
                        docstring=doc_str,
                        language=lang,
                    )
                )
                continue

        return module_doc, symbols

    def _index_file(self, rel_path: str, abs_path: Path) -> Optional[FileIndexInfo]:
        """Read and index a single source file."""
        try:
            stat = abs_path.stat()
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.debug("Failed reading %s: %s", rel_path, e)
            return None

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        ext = abs_path.suffix.lower()
        language = _SUPPORTED_EXTENSIONS.get(ext, "unknown")

        if language == "python":
            docstring, symbols = self._parse_python_source(rel_path, content)
        elif language in ("typescript", "tsx", "javascript", "jsx"):
            docstring, symbols = self._parse_typescript_source(rel_path, content)
        else:
            docstring, symbols = None, []

        return FileIndexInfo(
            file_path=rel_path,
            language=language,
            mtime=stat.st_mtime,
            size_bytes=stat.st_size,
            content_hash=content_hash,
            symbols=symbols,
            docstring=docstring,
            last_indexed_at=time.time(),
        )

    # -------------------------------------------------------------------------
    # Refresh & Lifecycle
    # -------------------------------------------------------------------------

    def refresh(self, force: bool = False) -> Dict[str, Any]:
        """Perform an incremental scan and index refresh across repo source files."""
        start_t = time.perf_counter()
        current_rel_paths: Set[str] = set()
        updated_count = 0
        removed_count = 0

        # Scan repository root
        for root, dirs, files in os.walk(self.repo_root):
            # Prune ignored directory trees
            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS and not d.startswith(".")]

            for file in files:
                abs_p = Path(root) / file
                try:
                    rel_p = str(abs_p.relative_to(self.repo_root)).replace("\\", "/")
                except ValueError:
                    continue

                if not self.is_indexable_file(rel_p):
                    continue

                current_rel_paths.add(rel_p)

                # Check if unchanged
                existing = self._files.get(rel_p)
                if not force and existing is not None:
                    try:
                        stat = abs_p.stat()
                        if stat.st_mtime == existing.mtime and stat.st_size == existing.size_bytes:
                            # Unchanged file skip
                            continue
                    except OSError:
                        pass

                # Index / Re-index file
                info = self._index_file(rel_p, abs_p)
                if info is not None:
                    self._files[rel_p] = info
                    updated_count += 1

        # Remove deleted files
        for old_path in list(self._files.keys()):
            if old_path not in current_rel_paths:
                del self._files[old_path]
                removed_count += 1

        # Rebuild symbol lookup index
        self._symbols_by_name.clear()
        total_symbols = 0
        for f_info in self._files.values():
            for sym in f_info.symbols:
                self._symbols_by_name.setdefault(sym.name, []).append(sym)
                total_symbols += 1

        self._last_refresh = time.time()
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        return {
            "indexed_files": len(self._files),
            "updated_files": updated_count,
            "removed_files": removed_count,
            "total_symbols": total_symbols,
            "duration_ms": round(elapsed_ms, 2),
            "last_indexed_at": self._last_refresh,
        }

    def is_stale(self) -> bool:
        """Check whether any tracked file or new file needs indexing."""
        if not self._files and self._last_refresh == 0.0:
            return True

        current_paths: Set[str] = set()
        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS and not d.startswith(".")]
            for file in files:
                abs_p = Path(root) / file
                try:
                    rel_p = str(abs_p.relative_to(self.repo_root)).replace("\\", "/")
                except ValueError:
                    continue

                if not self.is_indexable_file(rel_p):
                    continue

                current_paths.add(rel_p)
                existing = self._files.get(rel_p)
                if existing is None:
                    return True
                try:
                    stat = abs_p.stat()
                    if stat.st_mtime != existing.mtime or stat.st_size != existing.size_bytes:
                        return True
                except OSError:
                    return True

        # Check for deleted files
        for p in self._files:
            if p not in current_paths:
                return True

        return False

    # -------------------------------------------------------------------------
    # Search & Retrieval APIs
    # -------------------------------------------------------------------------

    def get_symbol(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Exact lookup of a symbol name across all indexed files."""
        syms = self._symbols_by_name.get(symbol_name, [])
        return [s.to_dict() for s in syms]

    def search_symbols(
        self, query: str, limit: int = 20, kind: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search symbols with exact > prefix > token > substring ranking."""
        q_lower = query.lower().strip()
        if not q_lower:
            all_syms: List[Dict[str, Any]] = []
            for sym_list in self._symbols_by_name.values():
                for s in sym_list:
                    if kind is None or s.kind == kind:
                        all_syms.append(s.to_dict())
            return all_syms[:limit]

        scored_results: List[Tuple[int, SymbolInfo]] = []

        for name, sym_list in self._symbols_by_name.items():
            name_lower = name.lower()
            score = 0
            if name == query:
                score = 100
            elif name_lower == q_lower:
                score = 90
            elif name_lower.startswith(q_lower):
                score = 80
            elif q_lower in name_lower.split("_") or q_lower in re.findall(
                r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+", name
            ):
                score = 60
            elif q_lower in name_lower:
                score = 40

            if score > 0:
                for s in sym_list:
                    if kind is None or s.kind == kind:
                        scored_results.append((score, s))

        scored_results.sort(key=lambda item: (-item[0], item[1].name, item[1].file_path))
        return [s.to_dict() for _, s in scored_results[:limit]]

    def search_files(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search indexed files by path or module name."""
        q_lower = query.lower().strip()
        results: List[Tuple[int, FileIndexInfo]] = []

        for path, info in self._files.items():
            path_lower = path.lower()
            score = 0
            if not q_lower:
                score = 10
            elif path_lower == q_lower:
                score = 100
            elif path_lower.endswith(q_lower):
                score = 80
            elif q_lower in path_lower:
                score = 50

            if score > 0:
                results.append((score, info))

        results.sort(key=lambda item: (-item[0], item[1].file_path))
        return [
            {
                "file_path": info.file_path,
                "language": info.language,
                "size_bytes": info.size_bytes,
                "symbol_count": len(info.symbols),
                "docstring": info.docstring,
            }
            for _, info in results[:limit]
        ]

    def get_excerpt(self, file_path: str, start_line: int, end_line: int) -> Optional[Dict[str, Any]]:
        """Retrieve a line-bounded code excerpt safely within repository files."""
        safe_path = self._resolve_safe_path(file_path)

        if not safe_path.is_file():
            return None

        # Verify not in sensitive/ignored patterns
        rel_str = str(safe_path.relative_to(self.repo_root)).replace("\\", "/")
        if not self.is_indexable_file(rel_str):
            raise ValueError(f"Access denied: File '{rel_str}' is an excluded non-source file.")

        try:
            content = safe_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.debug("Failed reading excerpt from %s: %s", file_path, e)
            return None

        lines = content.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return {
                "file_path": rel_str,
                "start_line": 1,
                "end_line": 1,
                "total_lines": 0,
                "content": "",
            }

        # Safe line bounds clamping
        s_line = max(1, min(start_line, total_lines))
        e_line = max(s_line, min(end_line, total_lines))

        excerpt_content = "\n".join(lines[s_line - 1 : e_line])

        return {
            "file_path": rel_str,
            "start_line": s_line,
            "end_line": e_line,
            "total_lines": total_lines,
            "content": excerpt_content,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return operational summary of CodeIndex."""
        by_lang: Dict[str, int] = {}
        for f in self._files.values():
            by_lang[f.language] = by_lang.get(f.language, 0) + 1

        return {
            "total_files": len(self._files),
            "total_symbols": len(self._symbols_by_name),
            "by_language": by_lang,
            "last_refresh": self._last_refresh,
            "is_stale": self.is_stale(),
        }
