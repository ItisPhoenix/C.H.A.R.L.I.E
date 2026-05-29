"""
C.H.A.R.L.I.E. — State Reflector
Dynamically analyzes the codebase and configuration to update Charlie's self-awareness.
Ensures Charlie always 'knows' what modules are active without manual JSON updates.
"""

from pathlib import Path


class StateReflector:
    def __init__(self):
        self.root = Path(__file__).parent.parent.parent.resolve()

    def get_current_capabilities(self) -> str:
        """Analyzes the current environment and returns a technical capabilities block."""
        from charlie.config import settings

        caps = []

        # 1. Core Architecture
        caps.append(
            "ARCHITECTURE: 5-Process Isolated Engine (Brain, Audio, Browser, Vision, Phoenix)."
        )

        # 2. Browser Verification
        has_cloak = self._check_import("cloakbrowser")
        has_browser = (
            self.root / "charlie" / "browser" / "headless_browser.py"
        ).exists()
        if has_cloak and has_browser:
            caps.append(
                "BROWSER: Stealth Navigator active via CloakBrowser. Persistent context and startup news pre-fetching enabled."
            )
            caps.append(
                "YOUTUBE: 'Smart Play' active. You can find and play first-video results automatically."
            )
            caps.append(
                "CONTROL: OS-level browser mastery via pyautogui (play/search/fullscreen/tabs)."
            )

        # 3. Telegram Check
        if (self.root / "charlie" / "telegram" / "bridge.py").exists():
            caps.append(
                "REMOTE: Telegram Command Center v1.0 active. Multi-device encrypted link established."
            )
            caps.append(
                "PRIVACY: OCR-based Redactor active. Sensitive data blurred in screenshots."
            )
        else:
            caps.append(
                "REMOTE: Local-only. Telegram initialization pending."
            )

        # 4. RAG Check
        if (self.root / "charlie" / "memory" / "rag_indexer.py").exists():
            caps.append(
                "MEMORY: RAG / Cognitive Indexing active. Semantic project recall enabled."
            )
        else:
            caps.append(
                "MEMORY: Short-term and Episodic (ChromaDB) active. RAG pending."
            )

        # 5. Multimedia Status
        if settings.startup.play_music:
            caps.append("MEDIA: Spotify + WinRT Universal Media Control integrated.")

        # 6. Safety Level
        caps.append(
            f"SAFETY: Multi-tier Risk Verification active. Current max self-mod tier: {settings.security.self_mod_max_tier if hasattr(settings.security, 'self_mod_max_tier') else 2}."
        )

        return "\n".join(caps)

    def _check_import(self, name: str) -> bool:
        try:
            __import__(name)
            return True
        except ImportError:
            return False


state_reflector = StateReflector()
