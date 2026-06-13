import logging
import httpx
from ddgs import DDGS

logger = logging.getLogger("charlie.research")

async def web_search(query: str, max_results: int = 5) -> str:
    """Truly free web search using DuckDuckGo (local/unauthenticated)."""
    results_list = []
    
    try:
        with DDGS() as ddgs:
            logger.info(f"Searching DDG Web for: {query}")
            # .text() is free, unauthenticated, and open-source driven
            text_results = ddgs.text(query, max_results=max_results)
            
            if text_results:
                for r in text_results:
                    desc = r.get('body', r.get('snippet', ''))
                    title = r.get('title', 'No Title')
                    url = r.get('href', r.get('url', ''))
                    results_list.append(f"[WEB] {title}: {desc} ({url})")

            if not results_list:
                return "Search returned no results. Try broadening your query."

            return "\n".join(results_list[:max_results])

    except Exception as e:
        logger.error(f"search_error | {type(e).__name__}: {e}")
        if "403" in str(e) or "Ratelimit" in str(e):
            return "My search access is currently throttled. I'll try to find another way next time."
        return f"Search error: {str(e)}"

async def read_url(url: str) -> str:
    """Local scraper using httpx and BeautifulSoup (no API)."""
    logger.info(f"Scraping URL locally: {url}")
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            r = await client.get(url, headers=headers, timeout=15.0)
            r.raise_for_status()
            
            # Simple text extraction (placeholder for more robust local parsing if needed)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            text = soup.get_text()
            # break into lines and remove leading and trailing whitespace
            lines = (line.strip() for line in text.splitlines())
            # break multi-headlines into a line each
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            # drop blank lines
            text = '\n'.join(chunk for chunk in chunks if chunk)
            
            return text[:5000] # Limit to avoid context overflow
    except Exception as e:
        logger.error(f"local_scrape_error | {url} | {e}")
        return f"Error reading content from {url}: {str(e)}"
