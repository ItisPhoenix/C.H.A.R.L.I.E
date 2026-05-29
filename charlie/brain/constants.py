"""Shared constants for the brain module."""

TOPIC_MAP = {
    "web_security": ["xss", "sqli", "csrf", "pentest", "vulnerability", "injection"],
    "network": ["nmap", "wireshark", "packet", "ip", "dns", "port", "socket"],
    "malware": ["reverse", "assembly", "binary", "shellcode", "obfuscation"],
    "os_hardening": ["linux", "powershell", "registry", "policy", "firewall"],
    "python": ["python", "pip", "pytest", "import", "def ", "class ", "async"],
    "ui_automation": ["click", "type", "window", "focus", "pyautogui", "desktop"],
    "ai_llm": ["llm", "model", "gemma", "ollama", "lm studio", "prompt", "token"],
    "system_admin": ["cpu", "ram", "vram", "gpu", "process", "disk", "temp", "memory"],
    "media": ["spotify", "play", "music", "volume", "pause", "youtube", "media"],
    "files": ["file", "folder", "directory", "read", "write", "path", "save"],
}

CORRECTION_KEYWORDS = ["wrong", "no that's not", "undo", "revert", "incorrect", "mistake", "fix that"]
CONFIRMATION_KEYWORDS = ["correct", "perfect", "good job", "exactly", "yes that's right", "well done", "nice"]
