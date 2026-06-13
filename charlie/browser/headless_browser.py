"""
C.H.A.R.L.I.E. — Browser Process using trafilatura + duckduckgo.
Real-time progress streaming via status_q.
Multi-tier fetch: trafilatura HTTP → DDG snippet fallback.

CloakBrowser removed — DLL/greenlet ABI issues on Windows.
Research degrades gracefully to trafilatura HTTP + DDG snippets.
"""

import asyncio
import multiprocessing
import re
import time
from pathlib import Path

import trafilatura

from charlie.utils.logger import get_logger

logger = get_logger("Browser")

# ── Error-cap state ─────────────────────────────────────────────────────────
_err_window: list[float] = []
_ERR_WINDOW_SECONDS = 60
_ERR_WINDOW_THRESHOLD = 5
# ────────────────────────────────────────────────────────────────────────────


def _send_heartbeat() -> None:
    """No-op stub so callers don't reach into instance attributes."""
    return None


class HeadlessBrowserProcess(multiprocessing.Process):
    """Isolated process for web research and news monitoring via trafilatura."""

    def __init__(
        self,
        req_q: multiprocessing.Queue,
        res_q: multiprocessing.Queue,
        heartbeat: multiprocessing.Value,
        status_q: multiprocessing.Queue = None,
    ):
        super().__init__(daemon=True, name="BrowserProcess")
        self.req_q = req_q
        self.res_q = res_q
        self.heartbeat = heartbeat
        self.status_q = status_q
        self.root_dir = Path(__file__).parent.parent.parent.resolve()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _push_status(self, text: str):
        if self.status_q:
            try:
                self.status_q.put_nowait({"type": "RESEARCH_STATUS", "content": text})
            except Exception as e:
                logger.debug(f"push_status_failed | {e}")

    def _push_log(self, text: str):
        if self.status_q:
            try:
                self.status_q.put_nowait({"type": "RESEARCH_LOG", "content": text})
            except Exception as e:
                logger.debug(f"push_log_failed | {e}")

    def _push_partial(self, text: str):
        if self.status_q:
            try:
                self.status_q.put_nowait({"type": "RESEARCH_PARTIAL", "content": text})
            except Exception as e:
                logger.debug(f"push_partial_failed | {e}")

    # ── Process entry ─────────────────────────────────────────────────────────

    def run(self):
        logger.info("browser_process_ignited")
        exit_code = 0
        try:
            asyncio.run(self._main_loop())
        except KeyboardInterrupt:
            logger.info("browser_process_interrupted")
        except Exception as e:
            logger.error(f"browser_process_fatal | {e}")
            exit_code = 1
        if exit_code:
            import sys
            sys.exit(exit_code)

    async def _main_loop(self):
        self._push_status("Browser (trafilatura + DuckDuckGo)")

        try:
            while True:
                self.heartbeat.value = time.time()
                try:
                    if not self.req_q.empty():
                        req = self.req_q.get_nowait()
                        await self._handle_request(req)
                    _err_window.clear()
                except (EOFError, ConnectionResetError, BrokenPipeError) as e:
                    logger.info(f"browser_loop_closed | {type(e).__name__}: {e}")
                    _err_window.clear()
                    return
                except Exception as e:
                    now = time.time()
                    _err_window.append(now)
                    while _err_window and _err_window[0] < now - _ERR_WINDOW_SECONDS:
                        _err_window.pop(0)

                    err_str = f"{type(e).__name__}: {e}"
                    if "closed" in err_str.lower() or "pipe" in err_str.lower():
                        logger.info(f"browser_loop_closed | {err_str}")
                        _err_window.clear()
                        return

                    if len(_err_window) >= _ERR_WINDOW_THRESHOLD:
                        logger.error(f"browser_loop_fatal | errs_in_60s={len(_err_window)} | exiting_for_respawn")
                        raise RuntimeError(f"browser_loop_fatal: {len(_err_window)} errors in {_ERR_WINDOW_SECONDS}s")

                    logger.error(f"browser_loop_err | {err_str}")
                    _send_heartbeat()
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"browser_loop_exited | {e}")

    # ── Request dispatcher ────────────────────────────────────────────────────

    async def _handle_request(self, req):
        req_type = req.get("type")
        req_id = req.get("id")
        data = req.get("data", {})
        silent = data.get("silent", False)

        if not hasattr(self, "speculative_cache"):
            self.speculative_cache = {}

        logger.info(f"browser_req_received | type={req_type} | id={req_id} | silent={silent}")

        try:
            if req_type == "PREDICTIVE":
                query = data.get("query")
                asyncio.create_task(self._pre_fetch_speculative(query))
                return

            if req_type == "RESEARCH":
                query = data.get("query")
                cached = self.speculative_cache.get(query)
                if cached and (time.time() - cached["time"] < 60):
                    logger.info(f"speculative_cache_hit | query='{query}'")
                    result = cached["data"]
                else:
                    result = await self._research(query)
            elif req_type == "FETCH":
                result = await self._fetch_page(data.get("url"))
            elif req_type == "NEWS":
                result = "News feature removed."
            else:
                result = {"error": "Unknown request type"}

            self.res_q.put({"id": req_id, "success": True, "data": result})

        except Exception as e:
            logger.error(f"browser_task_failed | {e}")
            self.res_q.put({"id": req_id, "success": False, "error": str(e)})

    # ── Research pipeline ─────────────────────────────────────────────────────

    def _smart_prune(self, text: str, query: str, max_chars: int = 1200) -> str:
        if not text or len(text) <= max_chars:
            return text

        paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
        query_words = set(re.findall(r"\w+", query.lower()))

        scored_paras = []
        for p in paragraphs:
            p_lower = p.lower()
            score = sum(1 for w in query_words if w in p_lower)
            if re.search(r"\d+", p):
                score += 1
            scored_paras.append((score, p))

        scored_paras.sort(key=lambda x: x[0], reverse=True)

        pruned = []
        current_len = 0
        for score, p in scored_paras:
            if score < 1 and len(pruned) > 3:
                continue
            if current_len + len(p) > max_chars:
                break
            pruned.append(p)
            current_len += len(p)

        return "\n\n".join(pruned)

    async def _pre_fetch_speculative(self, query: str):
        try:
            if not query or len(query) < 3:
                return
            logger.info(f"speculative_pre_fetch_started | query='{query}'")
            result = await self._research(query)
            self.speculative_cache[query] = {"data": result, "time": time.time()}
            logger.info(f"speculative_pre_fetch_complete | query='{query}'")
        except Exception as e:
            logger.debug(f"speculative_pre_fetch_failed | {e}")

    async def _research(self, query: str):
        from duckduckgo_search import DDGS

        logger.info(f"research_start | query='{query}'")
        self._push_status(f"🔍  Querying DuckDuckGo: {query}")

        results = []

        # ── Tier 1: DDG text search ───────────────────────────────────────────
        try:
            self._push_log(f"Scanning web for LATEST references (past week): '{query}'")
            with DDGS(timeout=15) as ddgs:
                results = list(ddgs.text(query, max_results=6, timelimit="w"))

            if not results:
                self._push_log("No results in past week. Expanding search to ALL TIME...")
                with DDGS(timeout=15) as ddgs:
                    results = list(ddgs.text(query, max_results=6))

            self._push_log(f"Identified {len(results)} potential sources")
            logger.info(f"ddg_text_results | count={len(results)}")
        except Exception as e:
            self._push_log("Search interface briefly unavailable. Retrying...")
            logger.warning(f"ddg_text_failed | {e}")

        # ── Tier 2: DDG news fallback ─────────────────────────────────────────
        if not results:
            self._push_status("⚡  DDG text empty — trying news index...")
            try:
                with DDGS(timeout=10) as ddgs:
                    news = list(ddgs.news(query, max_results=6))
                results = [{"title": n["title"], "href": n["url"], "body": n.get("body", "")} for n in news]
                logger.info(f"ddg_news_fallback | count={len(results)}")
            except Exception as e:
                logger.warning(f"ddg_news_failed | {e}")

        if not results:
            self._push_status("❌  No results from DuckDuckGo. Rate-limited or query too specific.")
            return "No search results found. DuckDuckGo may be rate-limiting. Retry in 30 seconds."

        # ── Tier 3: Parallel Content Extraction ───────────────────────────────
        self._push_status("⚡  Warp Speed: Concurrently scraping top 8 sources...")

        fetch_tasks = []
        target_results = results[:8]

        async def _wrap_snippet(s):
            return s

        for r in target_results:
            url = r.get("href") or r.get("url")
            snippet = r.get("body", "")
            if url:
                fetch_tasks.append(self._fetch_page_smart(url, snippet))
            else:
                fetch_tasks.append(_wrap_snippet(snippet))

        contents = await asyncio.gather(*fetch_tasks)

        pages = []
        for i, content in enumerate(contents):
            res = target_results[i]
            title = res.get("title", "Untitled")
            url = res.get("href") or res.get("url", "")

            pruned_meat = self._smart_prune(str(content), query, max_chars=1200)
            pages.append({"title": title, "url": url, "content": pruned_meat})
            self._push_partial(f"### {title}\n{pruned_meat[:400]}\n\n")

        self._push_status(f"✅  Pruning Complete — Optimized {len(pages)} sources")
        return pages

    async def _fetch_page_smart(self, url: str, snippet: str = "") -> str:
        if not url:
            return snippet or "No URL provided."

        # trafilatura HTTP (fast, no browser overhead)
        try:
            self._push_log("Establishing fast-path connection...")
            html = await asyncio.to_thread(trafilatura.fetch_url, url, timeout=10)
            if not html:
                self._push_log("Fetch returned empty HTML.")
            else:
                self._push_log("Parsing primary content layer...")
                text = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,
                )
                if text and len(text) > 200:
                    self._push_log(f"Successfully retrieved {len(text)} characters")
                    logger.debug(f"trafilatura_http_success | url={url} | chars={len(text)}")
                    return text
                self._push_log("Content density low. Trying extended parsing...")
                text = trafilatura.extract(html, include_comments=True, include_tables=True, favor_precision=True)
                if text and len(text) > 100:
                    logger.debug(f"trafilatura_extended_success | url={url} | chars={len(text)}")
                    return text
        except Exception as e:
            self._push_log(f"Connection failed: {str(e)[:60]}...")
            logger.debug(f"trafilatura_http_failed | url={url} | {e}")

        self._push_log("Protocols exhausted. Using search engine metadata.")
        return snippet or "Insufficient content available at source."

    async def _fetch_page(self, url: str) -> str:
        return await self._fetch_page_smart(url)


# Aliases
CloakBrowser = HeadlessBrowserProcess
HeadlessBrowser = HeadlessBrowserProcess