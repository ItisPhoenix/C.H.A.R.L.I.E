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
        # Ensure a valid sample PMTiles archive exists for local/offline verification
        self._ensure_sample_archive()

    def _ensure_sample_archive(self) -> None:
        """Create a valid spec-compliant PMTiles v3 test fixture with valid metadata & TileJSON vector_layers."""
        sample_path = self.tiles_dir / "sample_regional.pmtiles"
        if not sample_path.exists():
            try:
                # Valid TileJSON-compliant metadata
                meta_dict = {
                    "name": "Regional Sample (Metadata Fixture)",
                    "attribution": "Charlie OS Offline Test",
                    "description": "Charlie OS regional test/metadata fixture",
                    "version": "1.0.0",
                    "vector_layers": [
                        {
                            "id": "places",
                            "description": "Points of interest and administrative hubs",
                            "fields": {"name": "String", "category": "String"},
                        }
                    ],
                }
                meta_json = json.dumps(meta_dict).encode("utf-8")

                # Root directory: varint 0 for 0 entries = single byte 0x00
                root_dir_bytes = b"\x00"

                root_dir_offset = PMTILES_HEADER_SIZE
                root_dir_len = len(root_dir_bytes)
                json_offset = root_dir_offset + root_dir_len
                json_len = len(meta_json)
                leaf_offset = 0
                leaf_len = 0
                tile_data_offset = json_offset + json_len
                tile_data_len = 0
                num_addressed = 0
                num_tile_entries = 0
                num_tile_contents = 0
                clustered = 1
                internal_comp = 0  # None
                tile_comp = 0      # None
                tile_type = 1      # MVT (vector)
                min_zoom = 0
                max_zoom = 14

                min_lon = int(139.0 * 1e7)
                min_lat = int(35.0 * 1e7)
                max_lon = int(140.0 * 1e7)
                max_lat = int(36.0 * 1e7)
                center_zoom = 10
                center_lon = int(139.69 * 1e7)
                center_lat = int(35.68 * 1e7)

                # Official PMTiles v3 header layout (exactly 127 bytes):
                # 0..6: 'PMTiles' (7 bytes)
                # 7: version (1 byte)
                # 8..95: 11 uint64 little-endian (88 bytes)
                # 96..99: 4 uint8 (4 bytes)
                # 100..101: 2 uint8 (2 bytes)
                # 102..117: 4 int32 little-endian (16 bytes)
                # 118: 1 uint8 (1 byte)
                # 119..126: 2 int32 little-endian (8 bytes)
                header_127 = struct.pack(
                    "<7sB11Q4B2B4iBii",
                    PMTILES_MAGIC,
                    PMTILES_VERSION,
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

                assert len(header_127) == PMTILES_HEADER_SIZE

                with open(sample_path, "wb") as f:
                    f.write(header_127)
                    f.write(root_dir_bytes)
                    f.write(meta_json)
                logger.info(f"Created valid PMTiles v3 fixture at {sample_path}")
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
