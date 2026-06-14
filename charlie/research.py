import logging
import httpx
import asyncio
import re
import os
import datetime
from .research_memory import memory as research_memory
from .config import config
from ddgs import DDGS

logger = logging.getLogger("charlie.research")
try:
    from crawl4ai import AsyncWebCrawler
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False

try:
    from markdownify import markdownify
    MARKDOWNIFY_AVAILABLE = True
except ImportError:
    MARKDOWNIFY_AVAILABLE = False


async def web_search(query: str, max_results: int = 5) -> str:
    """Truly free web search using SearXNG (if configured) or DuckDuckGo."""
    if config.searxng_url:
        try:
            return await searx_search(query, max_results)
        except Exception as e:
            logger.warning(f"SearXNG failed, falling back to DDG: {e}")

    results_list = []
    try:
        with DDGS() as ddgs:
            logger.info(f"Searching DDG Web for: {query}")
            text_results = ddgs.text(query, max_results=max_results)
            if text_results:
                for r in text_results:
                    desc = r.get('body', r.get('snippet', ''))
                    title = r.get('title', 'No Title')
                    url = r.get('href', r.get('url', ''))
                    if desc and url:
                        results_list.append(f"[WEB] {title}: {desc} ({url})")
            if not results_list:
                return "Search returned no results."
            return "\n".join(results_list[:max_results])
    except Exception as e:
        logger.error(f"search_error | {type(e).__name__}: {e}")
        return "Search error occurred. Let's try another topic."

async def get_search_urls(query: str, max_results: int = 3) -> list:
    """Helper to get just URLs from a search."""
    urls = []
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results)
            if results:
                for r in results:
                    url = r.get('href', r.get('url', ''))
                    if url:
                        urls.append(url)
    except Exception as e:
        logger.error(f"get_search_urls_error: {e}")
    return urls[:max_results]

async def searx_search(query: str, max_results: int = 5) -> str:
    """Search using a SearXNG instance."""
    url = f"{config.searxng_url.rstrip('/')}/search"
    params = {
        "q": query,
        "format": "json",
        "engines": "google,bing,duckduckgo",
        "pageno": 1
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        results_list = []
        for r in results[:max_results]:
            title = r.get("title", "No Title")
            snippet = r.get("content", r.get("snippet", ""))
            link = r.get("url", "")
            if snippet and link:
                results_list.append(f"[SEARX] {title}: {snippet} ({link})")
        return "\n".join(results_list) if results_list else "No SearXNG results."


async def read_url(url: str) -> str:
    """Read URL content with crawl4ai primary and httpx+markdownify fallback."""
    # Validate URL
    if not url.startswith(("http://", "https://")):
        return f"Invalid URL: {url}"

    # Strategy 1: crawl4ai (Headless Browser / JS Support)
    if CRAWL4AI_AVAILABLE:
        try:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url)
                if result and result.markdown:
                    logger.info(f"crawl4ai success for {url}")
                    return result.markdown[:6000]
        except Exception as e:
            logger.warning(f"crawl4ai failed for {url}, falling back: {e}")

    # Strategy 2: httpx + markdownify/BeautifulSoup fallback
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            headers = {"User-Agent": "Mozilla/5.0 Charlie/1.0"}
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            
            if MARKDOWNIFY_AVAILABLE:
                logger.info(f"markdownify fallback for {url}")
                md = markdownify(r.text)
                # Basic cleanup of extra whitespace
                clean_md = "\n".join([l.strip() for l in md.splitlines() if l.strip()])
                return clean_md[:4000]
            
            # BS4 Fallback (Original logic)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, 'html.parser')
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                tag.decompose()
            text = soup.get_text()
            clean_text = "\n".join([l.strip() for l in text.splitlines() if l.strip()])
            return clean_text[:4000]
            
    except Exception as e:
        logger.error(f"read_url_error | {url} | {e}")
        return f"Error reading site: {str(e)}"

async def deep_research(topic: str, brain) -> str:
    """Comprehensive multi-step research with synthesis and persistence."""
    logger.info(f"Starting Deep Research: {topic}")
    
    # 1. Decomposition
    sub_questions = [topic] # Default
    try:
        prompt = f"Decompose the research topic '{topic}' into 3 specific, search-friendly sub-questions. Output ONLY the questions, one per line."
        resp = await brain.fast_client.post(
            "chat/completions",
            json={"model": brain.config.fast_llm_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0}
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        sub_questions = [q.strip() for q in text.splitlines() if q.strip()][:3]
        if not sub_questions:
            sub_questions = [topic]
    except Exception as e:
        logger.warning(f"Decomposition failed, using topic only: {e}")

    # 2. Parallel Fetch
    semaphore = asyncio.Semaphore(3)
    
    async def fetch_and_extract(url):
        async with semaphore:
            content = await read_url(url)
            return {"url": url, "content": content}

    all_urls = []
    for q in sub_questions:
        urls = await get_search_urls(q, max_results=2)
        all_urls.extend(urls)
    
    all_urls = list(set(all_urls))[:6] # Unique URLs, max 6
    tasks = [fetch_and_extract(u) for u in all_urls]
    scraped_data = await asyncio.gather(*tasks)
    
    # 3. Synthesis
    context_blocks = []
    for item in scraped_data:
        context_blocks.append(f"SOURCE: {item['url']}\nCONTENT: {item['content'][:2000]}")
    
    context_str = "\n\n---\n\n".join(context_blocks)
    synthesis_prompt = (
        f"Topic: {topic}\n\n"
        "Based on the following research data, write a CONCISE, well-structured research report in Markdown. "
        "Focus on technical accuracy and speed. Include an executive summary, key findings, and a conclusion. Cite the source URLs provided.\n\n"
        f"{context_str}"
    )
    
    report = "Synthesis failed."
    try:
        resp = await brain.fast_client.post(
            "chat/completions",
            json={"model": brain.config.fast_llm_model, "messages": [{"role": "user", "content": synthesis_prompt}], "temperature": 0.3}
        )
        resp.raise_for_status()
        report = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Synthesis failed: {e}")
        report = f"Research complete for {topic}, but synthesis failed. Found {len(all_urls)} sources."

    # 4. Persistence
    try:
        session_id = research_memory.create_session(topic)
        for item in scraped_data:
            # Try to get a title from the content if possible (first line or first 50 chars)
            title = item['content'].splitlines()[0][:100] if item['content'] else "No Title"
            research_memory.add_snippet(session_id, item['url'], title, item['content'])
    except Exception as e:
        logger.error(f"Memory persistence failed: {e}")

    # 5. Disk Output
    try:
        os.makedirs("reports", exist_ok=True)
        filename = f"reports/{datetime.date.today()}-{re.sub(r'[^a-z0-9]', '-', topic.lower())}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"Report saved to {filename}")
    except Exception as e:
        logger.error(f"File output failed: {e}")

    return report

