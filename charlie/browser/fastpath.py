"""Tier 0: resolve a task to a URL via a plain HTTP request, no browser at all.

YouTube embeds its search results as a JSON blob (ytInitialData) in the initial HTML response,
so a play/watch request never needs Chromium -- this is the ~0.3-0.6s path that makes "play X
on youtube" faster than opening a bare tab.
"""

import json
import logging
import re
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger("charlie.browser")

_MIN_DURATION_S = 60
_YT_INITIAL_DATA_RE = re.compile(r"var ytInitialData = (\{.*?\});</script>")


def _duration_to_seconds(text: str) -> Optional[int]:
    parts = text.split(":")
    if not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds


def youtube_play(query: str) -> Optional[str]:
    """Return the best matching /watch?v=... URL for query, or None to fall through to tier 1."""
    from scrapling.fetchers import Fetcher
    try:
        resp = Fetcher.get(f"https://www.youtube.com/results?search_query={quote(query)}", timeout=5.0)
    except Exception:
        logger.warning("Tier 0 youtube_play request failed for %r", query, exc_info=True)
        return None
    if resp.status != 200:
        logger.warning("Tier 0 youtube_play got HTTP %s for %r", resp.status, query)
        return None
    html = resp.body.decode("utf-8", "ignore") if isinstance(resp.body, bytes) else resp.body
    match = _YT_INITIAL_DATA_RE.search(html)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        sections = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"][
            "sectionListRenderer"]["contents"]
    except (KeyError, json.JSONDecodeError):
        logger.warning("Tier 0 youtube_play could not parse ytInitialData for %r", query)
        return None

    query_words = {w.lower() for w in query.split() if len(w) > 2}
    candidates = []
    for section in sections:
        for item in section.get("itemSectionRenderer", {}).get("contents", []):
            renderer = item.get("videoRenderer")
            if not renderer or not renderer.get("videoId"):
                continue
            length_text = renderer.get("lengthText", {}).get("simpleText")
            seconds = _duration_to_seconds(length_text) if length_text else None
            if seconds is None or seconds < _MIN_DURATION_S:
                continue
            channel = "".join(r.get("text", "") for r in renderer.get("longBylineText", {}).get("runs", []))
            candidates.append((renderer["videoId"], channel.lower()))

    if not candidates:
        return None
    for video_id, channel in candidates:
        if query_words & set(channel.split()):
            return f"https://www.youtube.com/watch?v={video_id}"
    return f"https://www.youtube.com/watch?v={candidates[0][0]}"
