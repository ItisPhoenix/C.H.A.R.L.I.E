# Nmap Cheat Sheet

## Quick Commands

### Basic Scans
- `nmap <target>` — Quick scan of common ports
- `nmap -sS <target>` — SYN scan (default)
- `nmap -sT <target>` — TCP connect scan
- `nmap -sU <target>` — UDP scan

### Comprehensive Scans
- `nmap -sS -sV -O <target>` — SYN + version + OS
- `nmap -A <target>` — Aggressive (OS, version, scripts, traceroute)
- `nmap -p- <target>` — All 65535 ports
- `nmap -p 80,443,8080 <target>` — Specific ports

### Network Scans
- `nmap 192.168.1.0/24` — Scan entire subnet
- `nmap -sn 192.168.1.0/24` — Ping sweep (no port scan)
- `nmap -iL targets.txt` — Scan from file

### Output
- `-oN file.txt` — Normal output to file
- `-oX file.xml` — XML output
- `-oG file.grep` — Grepable output
- `-oA basename` — All formats

### Common Ports
- 21: FTP, 22: SSH, 23: Telnet, 25: SMTP
- 53: DNS, 80: HTTP, 110: POP3, 143: IMAP
- 443: HTTPS, 993: IMAPS, 995: POP3S
- 3306: MySQL, 3389: RDP, 5432: PostgreSQL
- 8080: HTTP-Proxy, 8443: HTTPS-Alt
