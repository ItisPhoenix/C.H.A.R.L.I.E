"""Recon tools — passive reconnaissance (TIER_1).

All tools are passive: no active exploitation, no confirmation required.
Python-native implementations, no external dependencies.
"""

import socket
import concurrent.futures
import re

from charlie.tools.tool_decorator import tool, RiskTier


COMMON_SUBDOMAINS = [
    "www", "mail", "ftp", "smtp", "pop", "imap", "webmail", "ns1", "ns2",
    "ns3", "dns", "dns1", "dns2", "mx", "mx1", "mx2", "relay", "vpn",
    "remote", "gateway", "proxy", "cdn", "api", "app", "apps", "beta",
    "dev", "develop", "staging", "stage", "test", "testing", "sandbox",
    "demo", "preview", "canary", "edge", "static", "assets", "media",
    "img", "images", "files", "download", "uploads", "docs", "doc",
    "wiki", "kb", "help", "support", "faq", "forum", "community",
    "blog", "news", "status", "monitor", "health", "grafana", "prometheus",
    "jenkins", "ci", "cd", "git", "gitlab", "github", "bitbucket", "svn",
    "jira", "confluence", "wiki", "admin", "panel", "dashboard", "console",
    "manage", "management", "portal", "sso", "auth", "login", "signin",
    "oauth", "ldap", "ad", "dc", "exchange", "outlook", "owa", "teams",
    "slack", "chat", "crm", "erp", "hr", "payroll", "billing", "invoice",
    "shop", "store", "ecommerce", "cart", "checkout", "payment", "pay",
    "db", "database", "sql", "mysql", "postgres", "mongo", "redis", "cache",
    "mq", "rabbitmq", "kafka", "queue", "search", "elastic", "solr",
    "backup", "bak", "old", "archive", "legacy", "internal", "intranet",
    "corp", "corporate", "office", "vpn2", "openvpn", "wireguard",
]


@tool(
    name="whois_lookup",
    description="Look up WHOIS registration info for a domain",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def whois_lookup(domain: str) -> str:
    """WHOIS domain registration lookup."""
    try:
        import whois
        w = whois.whois(domain)
        lines = []
        for attr in ["domain_name", "registrar", "creation_date", "expiration_date",
                      "name_servers", "emails", "org", "country", "state"]:
            val = getattr(w, attr, None)
            if val:
                if isinstance(val, list):
                    val = ", ".join(str(v) for v in val)
                lines.append(f"{attr}: {val}")
        return "\n".join(lines) if lines else f"No WHOIS data found for {domain}"
    except ImportError:
        return "python-whois not installed. Install with: pip install python-whois"
    except Exception as e:
        return f"WHOIS lookup failed: {e}"


@tool(
    name="dns_enum",
    description="Enumerate DNS records for a domain (A, MX, NS, TXT, CNAME)",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def dns_enum(domain: str, record_type: str = "A") -> str:
    """DNS record enumeration."""
    record_type = record_type.upper()
    results = []

    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, record_type)
        for rdata in answers:
            results.append(f"  {rdata}")
    except ImportError:
        # Fallback to socket for A records
        if record_type == "A":
            try:
                ips = socket.getaddrinfo(domain, None, socket.AF_INET)
                seen = set()
                for family, type, proto, canonname, sockaddr in ips:
                    ip = sockaddr[0]
                    if ip not in seen:
                        seen.add(ip)
                        results.append(f"  {ip}")
            except socket.gaierror as e:
                return f"DNS lookup failed: {e}"
        else:
            return "dnspython not installed. Install with: pip install dnspython"
    except Exception as e:
        return f"DNS {record_type} lookup failed for {domain}: {e}"

    if results:
        return f"DNS {record_type} records for {domain}:\n" + "\n".join(results)
    return f"No {record_type} records found for {domain}"


@tool(
    name="subdomain_scan",
    description="Discover subdomains by brute-forcing common names",
    risk_tier=RiskTier.TIER_1,
    category="security",
    timeout=60,
)
def subdomain_scan(domain: str) -> str:
    """Subdomain discovery via common name brute-force."""
    found = []

    def check_subdomain(sub: str) -> str | None:
        fqdn = f"{sub}.{domain}"
        try:
            socket.getaddrinfo(fqdn, None, socket.AF_INET)
            return fqdn
        except socket.gaierror:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_subdomain, sub): sub for sub in COMMON_SUBDOMAINS}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                found.append(result)

    if found:
        found.sort()
        return f"Found {len(found)} subdomains for {domain}:\n" + "\n".join(f"  {s}" for s in found)
    return f"No subdomains found for {domain} (checked {len(COMMON_SUBDOMAINS)} common names)"


@tool(
    name="tech_fingerprint",
    description="Identify technologies used by a web server from headers and HTML",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def tech_fingerprint(url: str) -> str:
    """Technology stack identification from HTTP headers and HTML meta tags."""
    # SSRF prevention
    from urllib.parse import urlparse
    import ipaddress
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "Error: Only HTTP/HTTPS URLs allowed."
        host = parsed.hostname or ""
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return "Error: URL targets a private/internal address."
        except ValueError:
            pass
    except Exception:
        return "Error: Invalid URL."
    try:
        import requests
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        findings = []

        # HTTP headers
        headers_to_check = {
            "Server": "Web Server",
            "X-Powered-By": "Backend",
            "X-AspNet-Version": "ASP.NET",
            "X-Generator": "Generator",
            "Content-Security-Policy": "CSP",
            "X-Frame-Options": "Clickjacking Protection",
            "Strict-Transport-Security": "HSTS",
            "X-Content-Type-Options": "MIME Sniffing Protection",
        }
        for header, label in headers_to_check.items():
            val = resp.headers.get(header)
            if val:
                findings.append(f"  {label}: {val}")

        # HTML meta tags
        body = resp.text[:10000]
        meta_gen = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', body, re.I)
        if meta_gen:
            findings.append(f"  CMS/Generator: {meta_gen.group(1)}")

        # Common technology indicators
        tech_indicators = {
            "wp-content": "WordPress",
            "drupal": "Drupal",
            "joomla": "Joomla",
            "react": "React",
            "vue": "Vue.js",
            "angular": "Angular",
            "next": "Next.js",
            "nuxt": "Nuxt.js",
            "django": "Django",
            "flask": "Flask",
            "laravel": "Laravel",
            "rails": "Ruby on Rails",
            "express": "Express.js",
            "cloudflare": "Cloudflare",
        }
        body_lower = body.lower()
        for indicator, tech in tech_indicators.items():
            if indicator in body_lower:
                findings.append(f"  Detected: {tech}")

        if findings:
            return f"Technology fingerprint for {url}:\n" + "\n".join(findings)
        return f"No technology indicators found for {url}"
    except Exception as e:
        return f"Tech fingerprint failed: {e}"


@tool(
    name="google_dork",
    description="Format a Google dork query for advanced search",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def google_dork(query: str, site: str = "") -> str:
    """Format a Google dork query and return formatted search string."""
    dork_parts = [query]
    if site:
        dork_parts.append(f"site:{site}")

    dork_query = " ".join(dork_parts)

    # Try DuckDuckGo search
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(dork_query, max_results=5):
                results.append(f"**{r['title']}**\n{r['body']}\n{r['href']}")
        if results:
            return f"Search results for: {dork_query}\n\n" + "\n\n".join(results)
        return f"No results for: {dork_query}"
    except ImportError:
        return f"Dork query formatted: {dork_query}\n(duckduckgo-search not installed for results)"
    except Exception as e:
        return f"Search for '{dork_query}' failed: {e}"


@tool(
    name="caller_lookup",
    description="Look up regional details and public profile info for a phone number",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def caller_lookup(phone_number: str) -> str:
    """Validate E.164 phone formats and search online directory details."""
    import html

    # 1. E.164 Validation
    phone_clean = phone_number.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not re.match(r"^\+?[1-9]\d{1,14}$", phone_clean):
        return f"Error: '{phone_number}' is not a valid E.164 phone number."

    # 2. Local Regional Profiling using Prefix / Area Code
    us_area_codes = {
        "201": "New Jersey (Jersey City)",
        "202": "Washington, DC",
        "203": "Connecticut (Bridgeport)",
        "206": "Washington State (Seattle)",
        "212": "New York (Manhattan)",
        "213": "California (Los Angeles)",
        "214": "Texas (Dallas)",
        "305": "Florida (Miami)",
        "312": "Illinois (Chicago)",
        "415": "California (San Francisco)",
        "512": "Texas (Austin)",
        "617": "Massachusetts (Boston)",
        "702": "Nevada (Las Vegas)",
        "818": "California (San Fernando Valley)",
        "917": "New York (NYC Mobile)",
    }

    country_prefixes = {
        "91": "India",
        "44": "United Kingdom",
        "49": "Germany",
        "33": "France",
        "81": "Japan",
        "61": "Australia",
        "1": "United States/Canada",
        "86": "China",
        "7": "Russia",
        "55": "Brazil",
    }

    country = "Unknown"
    region = "Unknown Region"

    prefix_number = phone_clean.lstrip("+")
    for pref, name in sorted(country_prefixes.items(), key=lambda x: len(x[0]), reverse=True):
        if prefix_number.startswith(pref):
            country = name
            if pref == "1" and len(prefix_number) >= 4:
                area = prefix_number[1:4]
                region = us_area_codes.get(area, "North America")
            elif pref == "91" and len(prefix_number) >= 5:
                region = "India Mobile Circle"
            else:
                region = f"{country}"
            break

    resolved_name = "Potential Business/Individual"
    resolved_age = "N/A"
    resolved_email = "N/A"
    resolved_address = f"{region}, {country}" if region != "Unknown Region" else country
    reported_date = "2026-05-24"

    # DuckDuckGo search for OSINT info
    try:
        from duckduckgo_search import DDGS
        scraped_info = []
        with DDGS() as ddgs:
            search_query = f'"{phone_clean}" OR "{phone_number}"'
            for r in ddgs.text(search_query, max_results=3):
                title = r.get("title", "")
                body = r.get("body", "")
                scraped_info.append(f"Result: {title} | Snippet: {body}")

        scraped_text = " ".join(scraped_info)
        email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', scraped_text)
        if email_match:
            resolved_email = email_match.group(0)

        for result in scraped_info:
            if any(w in result.lower() for w in ["spam", "scam", "telemarketer", "fraud"]):
                resolved_name = "Reported Telemarketer/Robocall"
                break
    except Exception:
        pass

    def clean_str(s: str) -> str:
        s_no_html = re.sub(r'<[^>]*>', '', s)
        return html.escape(s_no_html)

    resolved_name = clean_str(resolved_name)
    resolved_email = clean_str(resolved_email)
    resolved_address = clean_str(resolved_address)

    # 3. GUI event trigger
    from charlie.utils import queue_bridge
    status_q = queue_bridge.get_status_q()
    if status_q:
        widget_data = {
            "widget": "caller_profile",
            "data": {
                "name": resolved_name,
                "age": resolved_age,
                "phone": phone_number,
                "email": resolved_email,
                "address": resolved_address,
                "reported_date": reported_date
            }
        }
        status_q.put_nowait({
            "type": "WIDGET_SHOW",
            "content": widget_data
        })

    maps_query = f"https://www.google.com/maps/search/?api=1&query={resolved_address.replace(' ', '+')}"

    dossier = f"""### 👤 [CALLER DOSSIER] {phone_number}

| Field | Value |
| :--- | :--- |
| **Name** | {resolved_name} |
| **Age** | {resolved_age} |
| **Phone** | {phone_number} |
| **Email** | {resolved_email} |
| **Address** | {resolved_address} |
| **Reported Date** | {reported_date} |

📍 **Maps Link**: [Google Maps Locale]({maps_query})
"""
    return dossier
