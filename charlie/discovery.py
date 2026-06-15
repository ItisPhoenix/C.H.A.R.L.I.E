import os
import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger("charlie.discovery")

class SystemDiscovery:
    """
    Dynamically discovers Charlie's current hardware, software, and 
    recent changes to keep the personality self-aware.
    """
    def __init__(self, config):
        self.config = config
        self.changelog_path = "CHANGELOG.md"

    def get_latest_updates(self, limit: int = 1) -> str:
        """Parses CHANGELOG.md to find the N most recent update sections."""
        if not os.path.exists(self.changelog_path):
            return "No recent change records found."
        
        try:
            with open(self.changelog_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # Split by version headers (## [Date])
            sections = re.split(r'(?m)^##\s+\[.*?\]', content)
            if len(sections) < 2:
                return "Recent architectural logs are empty."
            
            # Get the first N non-empty sections (skipping the title/intro)
            recent = [s.strip() for f in sections[1:limit+1] if (s := f.strip())]
            return "\n".join(recent)
        except Exception as e:
            logger.error(f"Failed to discover latest updates: {e}")
            return "Discovery of recent changes failed."

    def discover_manifest(self, mcp_client=None) -> Dict[str, Any]:
        """Scans the system to build a comprehensive capability manifest."""
        manifest = {
            "senses": {
                "hearing": f"Whisper ASR ({self.config.whisper_model})",
                "voice": "Kokoro TTS (Local, GPU Accelerated)",
                "attention": "Wake Word Passive Listening" if self.config.enable_wake_word else "Push-to-Talk / Always-on"
            },
            "brain": {
                "architecture": "Hybrid Local/Cloud Router",
                "local_model": self.config.local_llm_model if self.config.enable_local_llm else "Disabled",
                "cloud_model": self.config.llm_model,
                "memory": "SQLite Long-term Semantic Storage"
            },
            "agency": {
                "tools_available": len(mcp_client._tools) if mcp_client else 0,
                "mcp_status": "Active" if (mcp_client and mcp_client.is_available) else "Offline",
                "web_intelligence": "SearXNG + Crawl4AI"
            },
            "recent_upgrades": self.get_latest_updates(limit=1)
        }
        return manifest

    def format_manifest_for_prompt(self, manifest: Dict[str, Any]) -> str:
        """Converts the manifest dict into a concise text block for the LLM."""
        lines = ["\nSYSTEM MANIFEST (CURRENT STATE):"]
        
        # Core Specs
        s = manifest["senses"]
        lines.append(f"- SENSES: {s['hearing']} | {s['voice']} | {s['attention']}")
        
        b = manifest["brain"]
        lines.append(f"- INTELLIGENCE: {b['architecture']} (Local: {b['local_model']} | Cloud: {b['cloud_model']})")
        
        a = manifest["agency"]
        lines.append(f"- AGENCY: {a['mcp_status']} ({a['tools_available']} tools active) | {a['web_intelligence']}")
        
        # Recent History
        if manifest["recent_upgrades"]:
            lines.append("\nRECENT ARCHITECTURAL UPGRADES:")
            lines.append(manifest["recent_upgrades"])
            
        return "\n".join(lines)
