import logging
import httpx
from ddgs import DDGS

logger = logging.getLogger("charlie.research")

async def web_search(query: str, max_results: int = 5) -> str:
    """Truly free web search using DuckDuckGo (local/unauthenticated)."""
    results_list = []
    
    try:
        # DDGS context manager is robust for clean session teardown
        with DDGS() as ddgs:
            logger.info(f"Searching DDG Web for: {query}")
            # .text() is unauthenticated and open-source driven
            text_results = ddgs.text(query, max_results=max_results)
            
            if text_results:
                for r in text_results:
                    desc = r.get('body', r.get('snippet', ''))
                    title = r.get('title', 'No Title')
                    url = r.get('href', r.get('url', ''))
                    
                    # Basic data integrity check
                    if desc and url:
                        results_list.append(f"[WEB] {title}: {desc} ({url})")

            if not results_list:
                logger.warning(f"No search results found for query: {query}")
                return "Search returned no results. Try broadening your query."

            return "\n".join(results_list[:max_results])

    except Exception as e:
        logger.error(f"search_error | {type(e).__name__}: {e}")
        # Identify specific anti-bot patterns
        err_str = str(e).lower()
        if "403" in err_str or "ratelimit" in err_str or "forbidden" in err_str:
            return "My search access is currently throttled by the search engine. I'll try to find another way next time."
        return "Search error occurred while looking that up. Let's try another topic."

async def read_url(url: str) -> str:
    """Local scraper using httpx and BeautifulSoup (no API)."""
    logger.info(f"Scraping URL locally: {url}")
    
    # Validate URL basic structure
    if not url.startswith(("http://", "https://")):
        return f"Invalid URL: {url}. Please provide a full link starting with http or https."

    try:
        # Use a single client with reasonable timeouts to prevent hanging
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9"
            }
            
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            
            # Content-type check to avoid trying to parse PDFs/images as HTML
            content_type = r.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type:
                return f"Skipping URL {url}: Content type '{content_type}' is not supported for text extraction."

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # 1. REMOVE NOISE
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                tag.decompose()
            
            # 2. EXTRACT MAIN CONTENT (Try common containers)
            main_content = soup.find('main') or soup.find('article') or soup.find('div', class_='content')
            target = main_content if main_content else soup
            
            text = target.get_text()
            
            # 3. CLEAN TEXT
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            clean_text = '\n'.join(chunk for chunk in chunks if chunk)
            
            if not clean_text:
                return f"I read the page at {url}, but couldn't find any readable text content."
                
            return clean_text[:4000] # Slightly tighter limit to ensure context fits
            
    except httpx.HTTPStatusError as e:
        logger.warning(f"URL access error {e.response.status_code} for {url}")
        return f"I couldn't reach that website. It returned a {e.response.status_code} error."
    except Exception as e:
        logger.error(f"local_scrape_error | {url} | {type(e).__name__}: {e}")
        return f"Error reading content from that site: {str(e)}"
