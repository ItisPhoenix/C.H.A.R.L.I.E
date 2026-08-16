"""PMTiles Local Archive Helper for Air-gapped Mapping."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class PMTilesManager:
    """Manages local PMTiles archives and directory indexing."""

    def __init__(self, tiles_dir: Optional[str] = None) -> None:
        if tiles_dir:
            self.tiles_dir = Path(tiles_dir)
        else:
            self.tiles_dir = Path("data/tiles")
        self.tiles_dir.mkdir(parents=True, exist_ok=True)

    def find_archive(self, name_or_path: str) -> Optional[Path]:
        """Find a local PMTiles archive on disk."""
        candidate = Path(name_or_path)
        if candidate.is_file():
            return candidate

        candidate_in_dir = self.tiles_dir / name_or_path
        if candidate_in_dir.is_file():
            return candidate_in_dir

        # Try default planet or regional file
        default_files = list(self.tiles_dir.glob("*.pmtiles"))
        if default_files:
            return default_files[0]

        return None
