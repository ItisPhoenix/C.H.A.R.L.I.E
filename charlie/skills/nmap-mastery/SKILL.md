---
name: nmap-mastery
description: Nmap scan types, flags, output parsing, common port services, network scanning methodology
metadata:
  version: "1.0.0"
  author: "system"
  icon: "🌐"
  inject_mode: "on_demand"
  tags: ["nmap", "network", "scanning", "ports", "security"]
---

# Nmap Scan Types Reference

## Scan Types

### TCP SYN Scan (-sS)
- Default scan type, fast and stealthy
- Sends SYN, waits for SYN-ACK (open) or RST (closed)
- Requires root/sudo privileges

### TCP Connect Scan (-sT)
- Full TCP handshake, more reliable
- No root required, but more logged
- Use when SYN scan isn't available

### UDP Scan (-sU)
- Slower than TCP scans
- Common UDP services: DNS(53), SNMP(161), TFTP(69)
- Use with -sS for comprehensive scanning

### Service Version Detection (-sV)
- Probes open ports to identify service versions
- Helps identify specific vulnerabilities
- Adds time to scan

### OS Detection (-O)
- Guesses operating system from network behavior
- Requires root privileges
- Combine with -sV for best results

### Script Scan (--script)
- Run NSE scripts for vulnerability detection
- --script=default: safe default scripts
- --script=vuln: vulnerability detection scripts

## Scan Timing (-T0 to -T5)
- -T0 (Paranoid): IDS evasion, very slow
- -T1 (Sneaky): Slow, less likely to trigger alerts
- -T2 (Polite): Slowed down, respects bandwidth
- -T3 (Normal): Default speed
- -T4 (Aggressive): Fast, reliable network needed
- -T5 (Insane): Very fast, may miss results


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
