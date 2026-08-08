"""User-declared project workspaces: one markdown memory file per project, plus an active pointer."""

import logging
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("charlie.projects")

MAX_CHARS = 1600
_ACTIVE_POINTER = ".active"
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Mirrors charlie.tools._MEMORY_SEP; duplicated (not imported) to dodge a tools.py/projects.py import cycle.
_ENTRY_SEP = "§"


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("name must contain at least one letter or digit")
    return slug


def _parse_entries(text: str) -> List[str]:
    if not text.strip():
        return []
    if _ENTRY_SEP not in text:
        return [text.strip()]
    return [e.strip() for e in text.split(_ENTRY_SEP) if e.strip()]


class Projects:
    # Active pointer is a plain-text file (not just in-memory) so the Brain and web-server processes agree with no IPC.
    def __init__(self, projects_dir: str):
        self.dir = Path(projects_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, slug: str) -> Path:
        return self.dir / f"{slug}.md"

    def list(self) -> List[str]:
        return sorted(p.stem for p in self.dir.glob("*.md"))

    def create(self, name: str) -> str:
        slug = slugify(name)
        path = self._path(slug)
        if path.exists():
            raise ValueError(f"project '{slug}' already exists")
        path.write_text("", encoding="utf-8")
        return slug

    def get_active(self) -> Optional[str]:
        pointer = self.dir / _ACTIVE_POINTER
        if not pointer.exists():
            return None
        slug = pointer.read_text(encoding="utf-8").strip()
        return slug or None

    def set_active(self, slug: Optional[str]) -> None:
        if slug is not None and not self._path(slug).exists():
            raise ValueError(f"no such project '{slug}'")
        (self.dir / _ACTIVE_POINTER).write_text(slug or "", encoding="utf-8")

    def read_entries(self, slug: str) -> List[str]:
        path = self._path(slug)
        if not path.exists():
            raise ValueError(f"no such project '{slug}'")
        return _parse_entries(path.read_text(encoding="utf-8"))

    def add_entry(self, slug: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            raise ValueError("text is required")
        entries = self.read_entries(slug)
        current_len = sum(len(e) for e in entries) + (len(entries) - 1 if entries else 0)
        new_len = len(text) + (1 if entries else 0)
        if current_len + new_len > MAX_CHARS:
            raise ValueError(f"project '{slug}' memory full ({current_len}/{MAX_CHARS} chars)")
        entries.append(text)
        self._path(slug).write_text(_ENTRY_SEP.join(entries), encoding="utf-8")
