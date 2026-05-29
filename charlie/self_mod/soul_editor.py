import logging
import re
from pathlib import Path

logger = logging.getLogger("charlie.self_mod.soul")

class SoulEditor:
    def __init__(self, brain=None, soul_path: str = "charlie_soul.md"):
        self.brain = brain
        self.soul_path = Path(soul_path)

    def _check_auth(self) -> bool:
        if not self.brain:
            return True
        level = self.brain.relationship.trust_level
        if level in ("Cautious", "Cooperative"):
            logger.warning(f"soul_update_blocked | insufficient_trust={level}")
            return False
        return True

    def read_soul(self) -> str:
        if not self.soul_path.exists():
            return ""
        return self.soul_path.read_text(errors="replace")

    def update_preference(self, pref: str) -> bool:
        """Adds a preference to the soul file."""
        if not self._check_auth():
            return False
        content = self.read_soul()
        if not content:
            content = "# C.H.A.R.L.I.E. SOUL\n\n## Preferences\n"

        if "## Preferences" not in content:
            content += "\n## Preferences\n"

        # Avoid duplicate preferences
        if pref in content:
            return True

        new_content = content.rstrip() + f"\n- {pref}\n"

        try:
            self.soul_path.write_text(new_content)
            return True
        except Exception as e:
            logger.error(f"failed_update_soul | {e}")
            return False

    def update_section(self, section_name: str, new_content: str) -> bool:
        """Replaces a full section in the markdown soul."""
        if not self._check_auth():
            return False
        content = self.read_soul()
        pattern = rf"(## {section_name}.*?)(?=\n## |\Z)"

        if re.search(pattern, content, re.DOTALL):
            updated = re.sub(pattern, f"## {section_name}\n{new_content}", content, flags=re.DOTALL)
        else:
            updated = content.rstrip() + f"\n\n## {section_name}\n{new_content}\n"

        try:
            self.soul_path.write_text(updated)
            return True
        except Exception as e:
            logger.error(f"failed_update_section | {e}")
            return False
