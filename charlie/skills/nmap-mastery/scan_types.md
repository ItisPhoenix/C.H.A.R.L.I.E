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
