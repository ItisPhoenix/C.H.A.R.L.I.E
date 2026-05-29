import datetime
import logging
import os
import re
from typing import List, Dict, Optional

logger = logging.getLogger("charlie.intelligence.memory_graph")

# ── City geocode cache ─────────────────────────────────────────────────────────
_GEO_CITY_CACHE = {
    "tokyo": (35.6762, 139.6503), "new york": (40.7128, -74.0060),
    "london": (51.5074, -0.1278), "sydney": (-33.8688, 151.2093),
    "moscow": (55.7558, 37.6173), "dubai": (25.2048, 55.2708),
    "singapore": (1.3521, 103.8198), "mumbai": (19.0760, 72.8777),
    "cairo": (30.0444, 31.2357), "sao paulo": (-23.5505, -46.6333),
    "beijing": (39.9042, 116.4074), "paris": (48.8566, 2.3522),
    "berlin": (52.52, 13.405), "los angeles": (34.0522, -118.2437),
    "toronto": (43.6532, -79.3832), "seoul": (37.5665, 126.9780),
    "lagos": (6.5244, 3.3792), "chicago": (41.8781, -87.6298),
    "san francisco": (37.7749, -122.4194), "shanghai": (31.2304, 121.4737),
    "delhi": (28.7041, 77.1025), "bangkok": (13.7563, 100.5018),
    "jakarta": (-6.2088, 106.8456), "mexico city": (19.4326, -99.1332),
    "nairobi": (-1.2921, 36.8219), "johannesburg": (-26.2041, 28.0473),
}


def _geocode_city(text: str) -> Optional[tuple]:
    """Find first city mention in text and return (lat, lng)."""
    if not text:
        return None
    lowered = text.lower()
    for city, (lat, lng) in _GEO_CITY_CACHE.items():
        # Word boundary check — "tokyo" matches but not "instockholm"
        if re.search(rf'\b{re.escape(city)}\b', lowered):
            return (lat, lng)
    return None

class MemoryGraph:
    """
    MemoryGraph: Local knowledge base (Obsidian-style).
    Stores human-readable markdown notes for cognitive recall.
    """
    def __init__(self, root_dir: str = "memory/graph"):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)

    def save_node(self, title: str, content: str, tags: List[str] = None):
        """
        Saves a node as a markdown file.
        Format:
        ---
        title: Title
        date: ISO-8601
        tags: [tag1, tag2]
        ---
        Content
        """
        import uuid
        uid = str(uuid.uuid4())[:8]
        filename = f"{title.replace(' ', '_').lower()}_{uid}.md"
        path = os.path.join(self.root_dir, filename)

        now = datetime.datetime.now().isoformat()
        tag_str = ", ".join(tags) if tags else "none"

        md_content = f"""---
title: {title}
date: {now}
tags: [{tag_str}]
---

{content}
"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(md_content)
            logger.debug(f"graph_node_saved | {filename}")
        except Exception as e:
            logger.error(f"graph_save_failed | {title} | {e}")

    def query_recent(self, limit: int = 5) -> List[str]:
        """Returns contents of most recently modified nodes."""
        files = [os.path.join(self.root_dir, f) for f in os.listdir(self.root_dir) if f.endswith(".md")]
        files.sort(key=os.path.getmtime, reverse=True)

        recent_content = []
        for f in files[:limit]:
            try:
                with open(f, "r", encoding="utf-8") as file:
                    recent_content.append(file.read())
            except Exception: continue
        return recent_content

    def get_nodes_with_location(self) -> List[Dict]:
        """Return memory nodes that have an associated city/location."""
        if not os.path.isdir(self.root_dir):
            return []
        nodes = []
        for fname in os.listdir(self.root_dir):
            if not fname.endswith(".md"):
                continue
            try:
                path = os.path.join(self.root_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                coords = _geocode_city(content)
                if coords:
                    nodes.append({
                        "id": fname,
                        "content": content[:300],
                        "lat": coords[0],
                        "lng": coords[1],
                    })
            except Exception:
                continue
        return nodes
