"""Media result shaping; playback remains owned by Browser Executor."""

from __future__ import annotations

from typing import Iterable, List

from charlie.research.models import MediaResult, SearchResult


def media_results(results: Iterable[SearchResult]) -> List[MediaResult]:
    output: List[MediaResult] = []
    for result in results:
        if "youtube.com" not in result.domain and "youtu.be" not in result.domain:
            continue
        output.append(MediaResult(result.title, "YouTube", result.url, None, None))
    return output[:8]
