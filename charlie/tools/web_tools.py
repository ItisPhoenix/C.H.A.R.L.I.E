"""Web tools — search, fetch URL, scrape, news."""

import ipaddress
import socket
from urllib.parse import urlparse

from charlie.tools.tool_decorator import tool


DANGEROUS_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "0.0.0.0",
    "255.255.255.255",
    "broadcasthost",
}


def _is_ipv6_with_zone_id(host: str) -> bool:
    """Check if host is an IPv6 address with zone ID (e.g. fe80::1%eth0)."""
    return "%" in host and host.count(":") >= 2


def _blocked_hostname(host: str) -> bool:
    """Return True if hostname is known-private and should be rejected."""
    lower = host.lower().strip()
    # Strip trailing dot (fully qualified)
    if lower.endswith("."):
        lower = lower[:-1]
    if lower in DANGEROUS_HOSTNAMES:
        return True
    # Catch [::1], [::], ip6-localhost, etc.
    if lower in ("[::1]", "[::]", "ip6-localhost", "ip6-loopback"):
        return True
    # Reject *.local (mDNS) and *.internal (RFC 6762)
    if lower.endswith(".local") or lower.endswith(".internal"):
        return True
    return False


def _is_safe_url(url: str, resolve_dns: bool = True) -> bool:
    """Reject URLs targeting private/link-local/loopback IPs (SSRF prevention).

    Args:
        url: The URL to validate.
        resolve_dns: If True, resolve hostnames via DNS to check resolved IPs.
                     This adds a network round-trip per unique hostname but is
                     more thorough. Default False (checks hostname/IP only).
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""

        # Block known-dangerous hostnames first
        if _blocked_hostname(host):
            return False

        # Strip IPv6 zone ID before IP check
        check_host = host
        if _is_ipv6_with_zone_id(host):
            check_host = host.split("%", 1)[0]

        try:
            ip = ipaddress.ip_address(check_host)
            # Deny private, loopback, link-local, multicast, reserved
            if any([ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved]):
                return False
            return True
        except ValueError:
            # Hostname is a domain — optionally resolve via DNS
            if resolve_dns:
                try:
                    resolved = socket.getaddrinfo(host, 80, family=socket.AF_INET, type=socket.SOCK_STREAM)
                    for family, _, _, _, sockaddr in resolved:
                        ip_str = sockaddr[0]
                        try:
                            rip = ipaddress.ip_address(ip_str)
                            if any([rip.is_private, rip.is_loopback, rip.is_link_local, rip.is_multicast, rip.is_reserved]):
                                return False
                        except ValueError:
                            continue
                except (socket.gaierror, OSError):
                    return False
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
    from charlie.security.safety_guard import check_ssrf

    allowed, msg = check_ssrf(url)
    if not allowed:
        return msg

    try:
        import requests

        resp = requests.head(url, timeout=5, allow_redirects=True)
        return f"{url} — Status: {resp.status_code} — Time: {resp.elapsed.total_seconds():.2f}s"
    except Exception as e:
        return f"{url} — Error: {e}"
