"""
C.H.A.R.L.I.E. — Headless Browser Process v2.5 (CloakBrowser)
Real-time progress streaming via status_q.
Multi-tier fetch: trafilatura HTTP → CloakBrowser → DDG snippet fallback.
"""

import asyncio
import multiprocessing
import os
import re
import time
from pathlib import Path

import feedparser
import trafilatura
import yaml
from cloakbrowser import launch_persistent_context_async

from charlie.utils.logger import get_logger

logger = get_logger("Browser")

# ── Error-cap state ─────────────────────────────────────────────────────────
# Module-level so tests can reset it and so the sliding window survives
# the entire lifetime of the browser process.
_err_window: list[float] = []
_ERR_WINDOW_SECONDS = 60
_ERR_WINDOW_THRESHOLD = 5
# ────────────────────────────────────────────────────────────────────────────


def _send_heartbeat() -> None:
    """Module-level no-op heartbeat stub.

    The actual per-iteration heartbeat is set inline on
    ``self.heartbeat.value = time.time()`` in :py:meth:`HeadlessBrowserProcess._main_loop`.
    This stub exists so callers and tests can patch a single importable name
    without reaching into instance attributes.
    """
    return None
# ────────────────────────────────────────────────────────────────────────────



class HeadlessBrowserProcess(multiprocessing.Process):
    """
    Isolated process for CloakBrowser-based web research and news monitoring.
    Uses source-level stealth and anti-fingerprinting.
    """

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
        self.context = None
        self.root_dir = Path(__file__).parent.parent.parent.resolve()
        self.cached_news = "Retrieving latest news feed, Sir. Please stand by."
        self._silent = False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _push_status(self, text: str):
        """Update the status label."""
        if self.status_q and not self._silent:
            try:
                self.status_q.put_nowait({"type": "RESEARCH_STATUS", "content": text})
            except Exception as e:
                logger.debug(f"push_status_failed | {e}")

    def _push_log(self, text: str):
        """Append a real-time log entry to the research feed."""
        if self.status_q and not self._silent:
            try:
                self.status_q.put_nowait({"type": "RESEARCH_LOG", "content": text})
            except Exception as e:
                logger.debug(f"push_log_failed | {e}")

    def _push_partial(self, text: str):
        """Stream content to the research text area."""
        if self.status_q and not self._silent:
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
            exit_code = 1  # Non-zero so supervisor restarts instead of shutting down
        finally:
            if self.context:
                try:
                    asyncio.run(self.context.close())
                except Exception:
                    pass
        if exit_code:
            import sys
            sys.exit(exit_code)

    async def _main_loop(self):
        profile_dir = self.root_dir / "scratch" / "browser_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"launching_cloakbrowser | profile={profile_dir}")

        # CloakBrowser replaces the async_playwright context manager
        # Retry up to 3 times — Chromium can fail on first launch with stale profile
        for attempt in range(1, 4):
            try:
                self.context = await asyncio.wait_for(
                    launch_persistent_context_async(
                        user_data_dir=str(profile_dir),
                        headless=True,
                        humanize=False,  # DISABLED: May cause visible window on Windows
                        args=["--headless=new", "--disable-gpu", "--disable-software-rasterizer"],
                        viewport={"width": 1280, "height": 720},
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/122.0.0.0 Safari/537.36"
                        ),
                    ),
                    timeout=30.0,
                )
                break  # Success
            except asyncio.TimeoutError:
                logger.error(f"cloakbrowser_launch_timeout | attempt={attempt}/3")
                if attempt == 3:
                    raise RuntimeError("CloakBrowser failed to launch after 3 attempts (timeout)")
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"cloakbrowser_launch_failed | attempt={attempt}/3 | {e}")
                if attempt == 3:
                    raise RuntimeError(f"CloakBrowser failed to launch after 3 attempts: {e}")
                # Clean up stale profile lock on retry
                for lock_file in profile_dir.glob("Singleton*"):
                    try:
                        lock_file.unlink()
                    except Exception:
                        pass
                await asyncio.sleep(2)

        # Trigger background sweep on boot
        asyncio.create_task(self._background_news_sync())

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
                    # Slide the window: drop entries older than 60s
                    while _err_window and _err_window[0] < now - _ERR_WINDOW_SECONDS:
                        _err_window.pop(0)

                    err_str = f"{type(e).__name__}: {e}"
                    if "closed" in err_str.lower() or "pipe" in err_str.lower():
                        logger.info(f"browser_loop_closed | {err_str}")
                        _err_window.clear()
                        return

                    if len(_err_window) >= _ERR_WINDOW_THRESHOLD:
                        logger.error(
                            f"browser_loop_fatal | errs_in_60s={len(_err_window)}"
                            f" | exiting_for_respawn"
                        )
                        # Raise so the outer run() exits with non-zero and the
                        # supervisor respawns the process.
                        raise RuntimeError(
                            f"browser_loop_fatal: {len(_err_window)} errors in "
                            f"{_ERR_WINDOW_SECONDS}s"
                        )

                    logger.error(f"browser_loop_err | {err_str}")
                    _send_heartbeat()
                await asyncio.sleep(0.5)
        finally:
            await self.context.close()

    async def _background_news_sync(self):
        """Pre-fetches news on startup and caches it for instant retrieval."""
        try:
            logger.info("news_sync_initiated")
            res = await self._sweep_news(silent=True)
            self.cached_news = res
            logger.info(f"news_sync_complete | cached={len(str(res))} chars")
        except Exception as e:
            logger.error(f"news_sync_failed | {e}")

    # ── Request dispatcher ────────────────────────────────────────────────────

    async def _handle_request(self, req):
        req_type = req.get("type")
        req_id = req.get("id")
        data = req.get("data", {})
        self._silent = data.get("silent", False)

        if not hasattr(self, "speculative_cache"):
            self.speculative_cache = {}

        logger.info(
            f"browser_req_received | type={req_type} | id={req_id} | silent={self._silent}"
        )

        try:
            if req_type == "PREDICTIVE":
                # Start search in background, do not wait
                query = data.get("query")
                asyncio.create_task(self._pre_fetch_speculative(query))
                return # Silent background task

            if req_type == "RESEARCH":
                query = data.get("query")
                # Check speculative cache (valid for 60s)
                cached = self.speculative_cache.get(query)
                if cached and (time.time() - cached["time"] < 60):
                    logger.info(f"speculative_cache_hit | query='{query}'")
                    result = cached["data"]
                else:
                    result = await self._research(query)
            elif req_type == "FETCH":
                result = await self._fetch_page(data.get("url"))
            elif req_type == "NEWS":
                result = self.cached_news
            else:
                result = {"error": "Unknown request type"}

            # Reset silent flag after task
            self._silent = False
            self.res_q.put({"id": req_id, "success": True, "data": result})

        except Exception as e:
            self._silent = False
            logger.error(f"browser_task_failed | {e}")
            self.res_q.put({"id": req_id, "success": False, "error": str(e)})

    # ── Research pipeline ─────────────────────────────────────────────────────

    def _smart_prune(self, text: str, query: str, max_chars: int = 1500) -> str:
        """Prunes text to retain only query-relevant 'meat' using density scoring."""
        if not text or len(text) <= max_chars:
            return text

        paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
        query_words = set(re.findall(r"\w+", query.lower()))

        scored_paras = []
        for p in paragraphs:
            p_lower = p.lower()
            score = sum(1 for w in query_words if w in p_lower)
            # Boost score for paragraphs with numbers (dates, stats)
            if re.search(r"\d+", p): score += 1
            scored_paras.append((score, p))

        # Sort by score (desc)
        scored_paras.sort(key=lambda x: x[0], reverse=True)

        pruned = []
        current_len = 0
        for score, p in scored_paras:
            if score < 1 and len(pruned) > 3: continue # Drop irrelevant fluff after we have some content
            if current_len + len(p) > max_chars: break
            pruned.append(p)
            current_len += len(p)

        return "\n\n".join(pruned)

    async def _pre_fetch_speculative(self, query: str):
        """Background worker for speculative search."""
        try:
            if not query or len(query) < 3:
                return

            logger.info(f"speculative_pre_fetch_started | query='{query}'")
            result = await self._research(query)
            self.speculative_cache[query] = {
                "data": result,
                "time": time.time()
            }
            logger.info(f"speculative_pre_fetch_complete | query='{query}'")
        except Exception as e:
            logger.debug(f"speculative_pre_fetch_failed | {e}")

    async def _research(self, query: str):
        """
        Multi-tier research pipeline:
        1. DDG text search (primary)
        2. DDG news search (fallback if text empty)
        3. Per-page: trafilatura HTTP → Playwright → DDG snippet
        Streams live progress via status_q.
        """
        from duckduckgo_search import DDGS

        logger.info(f"research_start | query='{query}'")
        self._push_status(f"🔍  Querying DuckDuckGo: {query}")

        results = []

        # ── Tier 1: DDG text search (Latest first) ───────────────────────────
        try:
            self._push_log(f"Scanning web for LATEST references (past week): '{query}'")
            with DDGS(timeout=15) as ddgs:
                # Try with timelimit='w' (past week) to force recency
                results = list(ddgs.text(query, max_results=6, timelimit="w"))

            if not results:
                self._push_log(
                    "No results in past week. Expanding search to ALL TIME..."
                )
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
                results = [
                    {"title": n["title"], "href": n["url"], "body": n.get("body", "")}
                    for n in news
                ]
                logger.info(f"ddg_news_fallback | count={len(results)}")
            except Exception as e:
                logger.warning(f"ddg_news_failed | {e}")

        if not results:
            self._push_status(
                "❌  No results from DuckDuckGo. Rate-limited or query too specific."
            )
            return "No search results found. DuckDuckGo may be rate-limiting. Retry in 30 seconds."

        # ── Tier 3: Parallel Content Extraction (The Neural Warp) ─────────────
        self._push_status("⚡  Warp Speed: Concurrently scraping top 8 sources...")

        fetch_tasks = []
        target_results = results[:8]

        async def _wrap_snippet(s): return s

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

            # Smart pruning before sending to Brain
            pruned_meat = self._smart_prune(str(content), query, max_chars=1200)

            pages.append({
                "title": title,
                "url": url,
                "content": pruned_meat
            })
            self._push_partial(f"### {title}\n{pruned_meat[:400]}\n\n")

        self._push_status(f"✅  Pruning Complete — Optimized {len(pages)} sources")
        return pages

    async def _fetch_page_smart(self, url: str, snippet: str = "") -> str:
        """
        3-tier fetch:
        1. trafilatura via HTTP (fast, no browser overhead)
        2. Playwright headless (for JS-heavy / SPA pages)
        3. DDG snippet (always available as last resort)
        """
        if not url:
            return snippet or "No URL provided."

        # ── Tier 1: trafilatura HTTP (fastest, no browser) ────────────────────
        try:
            self._push_log("Establishing fast-path connection...")
            html = await asyncio.to_thread(trafilatura.fetch_url, url, timeout=10)
            if html:
                self._push_log("Parsing primary content layer...")
                text = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,
                )
                if text and len(text) > 200:
                    self._push_log(f"Successfully retrieved {len(text)} characters")
                    logger.debug(
                        f"trafilatura_http_success | url={url} | chars={len(text)}"
                    )
                    return text
            self._push_log("Content density low. Activating advanced neural parsing...")
        except Exception as e:
            self._push_log(
                f"Connection failed: {str(e)[:40]}... Retrying via secondary protocol."
            )
            logger.debug(f"trafilatura_http_failed | url={url} | {e}")

        # ── Tier 2: Playwright (JS rendering) ────────────────────────────────
        try:
            self._push_log("Initializing simulated environment...")
            page = await self.context.new_page()
            try:
                self._push_log("Bypassing client-side scripts...")
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(0.8)
                self._push_log("Compiling dynamic data points...")
                html = await page.content()
                text = trafilatura.extract(
                    html, include_comments=False, include_tables=True
                )
                if text and len(text) > 200:
                    self._push_log("Synthesis successful via secondary protocol")
                    logger.debug(f"playwright_success | url={url} | chars={len(text)}")
                    return text
            finally:
                await page.close()
            self._push_log("Warning: Source yielded insufficient data.")
        except Exception as e:
            self._push_log(f"Simulated environment error: {str(e)[:40]}...")
            logger.debug(f"playwright_failed | url={url} | {e}")

        # ── Tier 3: DDG Snippet (fallback) ──────────────────────────────────
        self._push_log("Protocols exhausted. Using search engine metadata.")
        return snippet or "Insufficient content available at source."

    async def _fetch_page(self, url: str) -> str:
        """Single URL fetch for FETCH requests (used by _tool_browser_fetch)."""
        return await self._fetch_page_smart(url)

    # ── News sweep ────────────────────────────────────────────────────────────

    async def _sweep_news(self, silent: bool = False):
        """Reads news_topics.yaml and pulls latest headlines via RSS."""
        config_path = "charlie/config/news_topics.yaml"
        if not os.path.exists(config_path):
            return "News configuration missing."

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        summary = []
        if not silent:
            self._push_status("📰  Sweeping news sources...")
            self._push_log("NEWS: Reading news_topics.yaml...")

        async def fetch_rss(topic_name, source):
            try:
                if not silent:
                    self._push_log(f"RSS: Fetching {source[:50]}...")
                feed = await asyncio.to_thread(feedparser.parse, source)
                entries = []
                for entry in feed.entries[:5]:
                    summary_text = entry.get("summary", entry.get("description", ""))
                    # Sanitize HTML from summary
                    clean_summary = trafilatura.extract(summary_text) if summary_text else ""
                    if not clean_summary:
                        clean_summary = summary_text[:200] + "..."

                    entries.append({
                        "topic": topic_name,
                        "title": entry.title,
                        "summary": clean_summary[:400],
                        "link": entry.link
                    })
                if not silent:
                    self._push_log(f"RSS: Found {len(entries)} detailed entries")
                return entries
            except Exception as e:
                if not silent:
                    self._push_log(f"RSS ERROR: {str(e)[:40]}")
                logger.error(f"rss_fetch_failed | source={source} | {e}")
                return []

        tasks = []
        for topic in config.get("topics", []):
            topic_name = topic.get("name")
            if not silent:
                self._push_log(f"TOPIC: Sweeping '{topic_name}'...")
            logger.info(f"sweeping_topic | {topic_name}")
            for source in topic.get("sources", []):
                tasks.append(fetch_rss(topic_name, source))

        results = await asyncio.gather(*tasks)
        for res_list in results:
            for item in res_list:
                summary.append(
                    f"### {item['title']}\n"
                    f"- **Topic**: {item['topic']}\n"
                    f"- **Summary**: {item['summary']}\n"
                    f"- **Source**: {item['link']}\n"
                )

        return "\n".join(summary) if summary else "No new updates found."

    # ── CVE poller (kept for on-demand activation) ────────────────────────────

    async def _cve_poller(self):
        """Manual CVE sweep — call via NEWS request, not auto-triggered."""
        config_path = "charlie/config/news_topics.yaml"
        seen_entries: set = set()

        if not os.path.exists(config_path):
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            for topic in config.get("topics", []):
                if not topic.get("proactive", False):
                    continue
                for source in topic.get("sources", []):
                    feed = await asyncio.to_thread(feedparser.parse, source)
                    for entry in feed.entries[:2]:
                        if entry.link not in seen_entries:
                            seen_entries.add(entry.link)
                            alert_text = (
                                f"🚨 CRITICAL ALERT: {entry.title}\n"
                                f"Source: {topic.get('name')}\n"
                                f"Link: {entry.link}"
                            )
                            logger.info(f"cve_alert | {entry.title}")
                            target_q = self.status_q if self.status_q else self.res_q
                            target_q.put(
                                {"type": "RESEARCH_RESULT", "content": alert_text}
                            )
        except Exception as e:
            logger.error(f"cve_poller_err | {e}")


# Alias for convenience
HeadlessBrowser = HeadlessBrowserProcess
