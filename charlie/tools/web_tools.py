"""Web tools — search, fetch URL, scrape, news."""

import ipaddress
from urllib.parse import urlparse

from charlie.tools.tool_decorator import tool


def _is_safe_url(url: str) -> bool:
    """Reject URLs targeting private/link-local/loopback IPs (SSRF prevention)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        try:
            ip = ipaddress.ip_address(host)
            return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
        except ValueError:
            # Hostname is a domain, not an IP — allow
            return True
    except Exception:
        return False


@tool(
    name="search",
    description="Search the web using DuckDuckGo",
    category="web",
)
def search_web(query: str, num_results: int = 5) -> str:
    """Search the web and return results."""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=num_results):
                results.append(f"**{r['title']}**\n{r['body']}\n{r['href']}")
        return "\n\n".join(results) if results else "No results found"
    except ImportError:
        return "duckduckgo-search not installed"
    except Exception as e:
        return f"Search failed: {e}"


@tool(
    name="browser_fetch",
    description="Fetch and extract text content from a URL",
    category="web",
)
def browser_fetch(url: str, max_length: int = 5000) -> str:
    """Fetch a URL and return text content."""
    if not _is_safe_url(url):
        return "Error: URL targets a private/internal address (SSRF blocked)."
    try:
        import requests
        from bs4 import BeautifulSoup

        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts and styles
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        return text[:max_length]
    except ImportError:
        return "requests/beautifulsoup4 not installed"
    except Exception as e:
        return f"Fetch failed: {e}"


@tool(
    name="get_news",
    description="Get latest news on a topic",
    category="web",
)
def get_news(topic: str, num_results: int = 5) -> str:
    """Get latest news articles on a topic."""
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.news(topic, max_results=num_results):
                results.append(f"**{r['title']}**\n{r['body']}\n{r['url']}")
        return "\n\n".join(results) if results else "No news found"
    except ImportError:
        return "duckduckgo-search not installed"
    except Exception as e:
        return f"News search failed: {e}"


@tool(
    name="scrape_page",
    description="Scrape a webpage and extract structured data",
    category="web",
)
def scrape_page(url: str, selector: str = "body") -> str:
    """Scrape a webpage using CSS selector."""
    if not _is_safe_url(url):
        return "Error: URL targets a private/internal address (SSRF blocked)."
    try:
        import requests
        from bs4 import BeautifulSoup

        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        elements = soup.select(selector)

        results = []
        for el in elements[:20]:
            results.append(el.get_text(strip=True))
        return "\n".join(results) if results else "No elements found"
    except ImportError:
        return "requests/beautifulsoup4 not installed"
    except Exception as e:
        return f"Scrape failed: {e}"


@tool(
    name="check_website",
    description="Check if a website is online and get HTTP status",
    category="web",
)
def check_website(url: str) -> str:
    """Check website availability."""
    try:
        import requests
        resp = requests.head(url, timeout=5, allow_redirects=True)
        return f"{url} — Status: {resp.status_code} — Time: {resp.elapsed.total_seconds():.2f}s"
    except Exception as e:
        return f"{url} — Error: {e}"
