"""PMTiles Local Archive Helper for Air-gapped Mapping with Header Inspection and Range Serving.

Strictly follows the PMTiles v3 binary specification:
https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("charlie.geo.tiles.pmtiles")

TILE_TYPES = {
    0: "unknown",
    1: "vector",  # MVT (Mapbox Vector Tile)
    2: "raster_png",
    3: "raster_jpeg",
    4: "raster_webp",
    5: "raster_avif",
}

# PMTiles v3 Header constants
PMTILES_HEADER_SIZE = 127
PMTILES_MAGIC = b"PMTiles"
PMTILES_VERSION = 3


class PMTilesManager:
    """Manages local PMTiles archives with path containment and header capability detection."""

    def __init__(self, tiles_dir: Optional[str] = None) -> None:
        if tiles_dir:
            self.tiles_dir = Path(tiles_dir).resolve()
        else:
            self.tiles_dir = Path("data/tiles").resolve()
        self.tiles_dir.mkdir(parents=True, exist_ok=True)

    def resolve_safe_path(self, filename_or_path: str) -> Optional[Path]:
        """Resolve an archive path ensuring strict containment within the tiles directory."""
        clean_name = Path(filename_or_path).name
        candidate = (self.tiles_dir / clean_name).resolve()

        # Enforce path containment
        try:
            candidate.relative_to(self.tiles_dir)
        except ValueError:
            logger.warning(f"Security: Path traversal attempt blocked for '{filename_or_path}'")
            return None

        if candidate.is_file():
            return candidate

        return None

    def inspect_archive(self, file_path: Path) -> Dict[str, Any]:
        """Inspect PMTiles archive header per official v3 spec to extract capabilities."""
        info: Dict[str, Any] = {
            "name": file_path.name,
            "sizeBytes": file_path.stat().st_size,
            "valid": False,
            "tileType": "unknown",
            "minZoom": 0,
            "maxZoom": 0,
            "bounds": None,
            "center": None,
            "metadata": None,
        }

        if file_path.stat().st_size < PMTILES_HEADER_SIZE:
            return info

        try:
            with open(file_path, "rb") as f:
                header_bytes = f.read(PMTILES_HEADER_SIZE)
                if len(header_bytes) < PMTILES_HEADER_SIZE:
                    return info

                # Check magic & version
                magic = header_bytes[0:7]
                version = header_bytes[7]
                if magic != PMTILES_MAGIC or version != PMTILES_VERSION:
                    return info

                info["valid"] = True
                info["version"] = version

                # Header fields per v3 spec
                (
                    root_dir_offset,
                    root_dir_len,
                    json_meta_offset,
                    json_meta_len,
                    leaf_offset,
                    leaf_len,
                    tile_data_offset,
                    tile_data_len,
                    num_addressed,
                    num_tile_entries,
                    num_tile_contents,
                    clustered,
                    internal_comp,
                    tile_comp,
                    tile_type,
                    min_zoom,
                    max_zoom,
                    min_lon,
                    min_lat,
                    max_lon,
                    max_lat,
                    center_zoom,
                    center_lon,
                    center_lat,
                ) = struct.unpack("<11Q4B2B4iBii", header_bytes[8:127])

                info["tileType"] = TILE_TYPES.get(tile_type, "unknown")
                info["minZoom"] = min_zoom
                info["maxZoom"] = max_zoom
                info["bounds"] = [min_lon / 1e7, min_lat / 1e7, max_lon / 1e7, max_lat / 1e7]
                info["center"] = [center_lon / 1e7, center_lat / 1e7, center_zoom]

                # Try reading JSON metadata if uncompressed and valid offset
                if internal_comp == 0 and json_meta_len > 0:
                    f.seek(json_meta_offset)
                    meta_raw = f.read(json_meta_len)
                    try:
                        info["metadata"] = json.loads(meta_raw.decode("utf-8"))
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to inspect PMTiles header for {file_path.name}: {e}")

        return info

    def list_archives(self) -> List[Dict[str, Any]]:
        """List all available archives with browser-fetchable URLs and capability metadata."""
        archives: List[Dict[str, Any]] = []
        for file_path in sorted(self.tiles_dir.glob("*.pmtiles")):
            if file_path.is_file():
                meta = self.inspect_archive(file_path)
                meta["url"] = f"/api/geo/pmtiles/{file_path.name}"
                archives.append(meta)
        return archives
