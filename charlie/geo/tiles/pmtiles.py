"""PMTiles Local Archive Helper for Air-gapped Mapping with Header Inspection and Range Serving."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import struct
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


class PMTilesManager:
    """Manages local PMTiles archives with path containment and header capability detection."""

    def __init__(self, tiles_dir: Optional[str] = None) -> None:
        if tiles_dir:
            self.tiles_dir = Path(tiles_dir).resolve()
        else:
            self.tiles_dir = Path("data/tiles").resolve()
        self.tiles_dir.mkdir(parents=True, exist_ok=True)
        # Ensure a valid sample PMTiles archive exists for local/offline verification
        self._ensure_sample_archive()

    def _ensure_sample_archive(self) -> None:
        """Create a valid synthetic PMTiles v3 test archive if none exists."""
        sample_path = self.tiles_dir / "sample_regional.pmtiles"
        if not sample_path.exists():
            try:
                magic = b"PM"
                version = 3
                meta_json = json.dumps({"name": "Regional Test Dataset", "attribution": "Charlie OS Offline"}).encode("utf-8")
                
                root_dir_offset = 127 + len(meta_json)
                root_dir_len = 0
                json_offset = 127
                json_len = len(meta_json)
                leaf_offset = 0
                leaf_len = 0
                tile_data_offset = 0
                tile_data_len = 0
                num_addressed = 1
                num_tile_entries = 1
                num_tile_contents = 1
                clustered = 1
                internal_comp = 0  # None
                tile_comp = 0      # None
                tile_type = 1      # Vector MVT
                min_zoom = 0
                max_zoom = 14
                
                min_lon = int(139.0 * 1e7)
                min_lat = int(35.0 * 1e7)
                max_lon = int(140.0 * 1e7)
                max_lat = int(36.0 * 1e7)
                center_zoom = 10
                center_lon = int(139.69 * 1e7)
                center_lat = int(35.68 * 1e7)

                # Exactly 122 bytes packed + 5 bytes reserved zeros = 127 bytes header
                header_122 = struct.pack(
                    "<2sB11Q6B4iBii",
                    magic,
                    version,
                    root_dir_offset,
                    root_dir_len,
                    json_offset,
                    json_len,
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
                )
                reserved_5 = b"\x00\x00\x00\x00\x00"
                header_127 = header_122 + reserved_5

                with open(sample_path, "wb") as f:
                    f.write(header_127)
                    f.write(meta_json)
                logger.info(f"Created sample PMTiles v3 archive at {sample_path}")
            except Exception as e:
                logger.warning(f"Could not generate sample PMTiles archive: {e}")

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
        """Inspect PMTiles archive header to extract capabilities, tile type, and bounds."""
        info: Dict[str, Any] = {
            "name": file_path.name,
            "sizeBytes": file_path.stat().st_size,
            "valid": False,
            "tileType": "unknown",
            "minZoom": 0,
            "maxZoom": 0,
            "bounds": None,
            "center": None,
        }

        if file_path.stat().st_size < 127:
            return info

        try:
            with open(file_path, "rb") as f:
                header_bytes = f.read(127)
                if len(header_bytes) < 127:
                    return info

                magic, version = struct.unpack("<2sB", header_bytes[0:3])
                if magic != b"PM" or version != 3:
                    return info

                info["valid"] = True
                info["version"] = version

                # Header byte offsets per PMTiles v3 spec
                tile_type_val = header_bytes[94]
                info["tileType"] = TILE_TYPES.get(tile_type_val, "unknown")
                info["minZoom"] = header_bytes[95]
                info["maxZoom"] = header_bytes[96]

                min_lon, min_lat, max_lon, max_lat = struct.unpack("<iiii", header_bytes[97:113])
                center_zoom = header_bytes[113]
                center_lon, center_lat = struct.unpack("<ii", header_bytes[114:122])

                info["bounds"] = [min_lon / 1e7, min_lat / 1e7, max_lon / 1e7, max_lat / 1e7]
                info["center"] = [center_lon / 1e7, center_lat / 1e7, center_zoom]
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
