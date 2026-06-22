import os
import re
import logging

logger = logging.getLogger("charlie.profile_manager")

SECTION_PATTERN = re.compile(r"^## (.+)$", re.MULTILINE)

USER_CATEGORY_MAP = {
    "preferences": "## Preferences",
    "work": "## Work",
    "health": "## Health",
    "family": "## Family",
    "location": "## Location",
    "general": "## Facts",
}


class ProfileManager:
    """Reads and writes SOUL.md (Charlie's identity) and USER.md (user profile).

    SOUL.md is Charlie's persistent identity — loaded into the system prompt
    every session.  Charlie can update it via TOOL: update_soul().

    USER.md is the user's persistent profile — the authoritative source of
    facts about the user.  Updated by code-level handlers ("remember that",
    auto-extraction).  SQLite memory is a supplementary search index.
    """

    def __init__(self, soul_path: str = "SOUL.md", user_path: str = "USER.md"):
        self.soul_path = soul_path
        self.user_path = user_path

    # ── readers ─────────────────────────────────────────────────────────

    def load_soul(self) -> str:
        """Return full SOUL.md content, or empty string if missing."""
        if not os.path.exists(self.soul_path):
            logger.warning(f"SOUL.md not found at {self.soul_path}")
            return ""
        try:
            with open(self.soul_path, encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"soul_load_error | {e}")
            return ""

    def load_user_profile(self) -> str:
        """Return full USER.md content, or empty string if missing."""
        if not os.path.exists(self.user_path):
            logger.warning(f"USER.md not found at {self.user_path}")
            return ""
        try:
            with open(self.user_path, encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"user_profile_load_error | {e}")
            return ""

    # ── SOUL.md writer ─────────────────────────────────────────────────

    def update_soul_section(self, section_name: str, new_content: str) -> bool:
        """Replace the content of a ## Section in SOUL.md.

        *section_name* is the text after '## ' (e.g. 'Core Values').
        *new_content* is the new body (without the heading).

        If the section does not exist it is appended at the end.
        Returns True on success.
        """
        if not os.path.exists(self.soul_path):
            logger.error("SOUL.md missing — cannot update")
            return False
        try:
            with open(self.soul_path, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.error(f"soul_read_error | {e}")
            return False

        heading = f"## {section_name}"
        # Find the section bounds
        start = None
        end = None
        for i, line in enumerate(lines):
            stripped = line.rstrip()
            if stripped == heading:
                start = i
            elif start is not None and SECTION_PATTERN.match(stripped):
                end = i
                break
        if end is None:
            end = len(lines)

        new_lines = new_content.splitlines(keepends=True)
        if start is None:
            # Append at end
            lines.append(f"\n{heading}\n")
            lines.extend(new_lines)
        else:
            lines[start + 1:end] = new_lines

        try:
            with open(self.soul_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            logger.info(f"soul_section_updated | {section_name}")
            return True
        except Exception as e:
            logger.error(f"soul_write_error | {e}")
            return False

    # ── USER.md writer ─────────────────────────────────────────────────

    def add_user_fact(self, fact: str, category: str = "general") -> bool:
        """Append a bullet under the appropriate ## section in USER.md.

        Skips duplicates (exact content match on the bullet line).
        Category is looked up in USER_CATEGORY_MAP; unknown categories
        fall through to ## Facts.
        Returns True if the fact was added.
        """
        heading = USER_CATEGORY_MAP.get(category, "## Facts")
        bullet = f"- {fact}"

        content = ""
        if os.path.exists(self.user_path):
            try:
                with open(self.user_path, encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                logger.error(f"user_read_error | {e}")
                return False
        else:
            content = "# USER.md — User Profile\n\n"

        # Dedup: skip if identical bullet exists under any heading
        if bullet in content:
            logger.debug(f"user_fact_duplicate_skipped | {fact}")
            return False

        lines = content.splitlines(keepends=True)

        # Find the target section and append bullet after its last line
        found_idx = None
        section_end = None
        for i, line in enumerate(lines):
            stripped = line.rstrip()
            if stripped == heading:
                found_idx = i
            elif found_idx is not None and SECTION_PATTERN.match(stripped):
                section_end = i
                break
        if section_end is None:
            section_end = len(lines)

        if found_idx is not None:
            insert_at = section_end
            # Insert a blank line before the bullet if the section is empty (only has heading)
            has_content_after = False
            for j in range(found_idx + 1, section_end):
                if lines[j].strip() and not lines[j].strip().startswith("<!--"):
                    has_content_after = True
                    break
            if not has_content_after:
                lines.insert(insert_at, "\n")
                insert_at += 1
            lines.insert(insert_at, bullet + "\n")
        else:
            # Section doesn't exist — append at end
            if not content.endswith("\n"):
                lines.append("\n")
            lines.append(f"\n{heading}\n")
            lines.append(f"{bullet}\n")

        try:
            with open(self.user_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            logger.info(f"user_fact_added | {category} | {fact}")
            return True
        except Exception as e:
            logger.error(f"user_write_error | {e}")
            return False

    def remove_user_fact(self, fact: str) -> bool:
        """Remove a bullet line from USER.md by exact content match.

        Returns True if a line was removed.
        """
        if not os.path.exists(self.user_path):
            return False
        try:
            with open(self.user_path, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.error(f"user_read_error | {e}")
            return False

        target = f"- {fact}\n"
        new_lines = [l for l in lines if l != target]
        if len(new_lines) == len(lines):
            return False  # no match
        try:
            with open(self.user_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            logger.info(f"user_fact_removed | {fact}")
            return True
        except Exception as e:
            logger.error(f"user_write_error | {e}")
            return False

    def get_user_facts(self) -> list[str]:
        """Return all bullet lines from USER.md."""
        content = self.load_user_profile()
        if not content:
            return []
        facts = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") and not stripped.startswith("- <!--"):
                facts.append(stripped[2:])
        return facts
    def get_user_name(self) -> str | None:
        """Extract the user's name from USER.md or SOUL.md.

        Looks for '## Name' or 'Name:' patterns in USER.md first,
        then falls back to 'your name is <X>' in SOUL.md.
        """
        import re as _re
        content = self.load_user_profile()
        if content:
            # Look for a Name section
            name_match = _re.search(r'^##\s+Name\s*\n+(.+)', content, _re.MULTILINE)
            if name_match:
                return name_match.group(1).strip().strip('*_')
            # Look for "Name: value" pattern
            name_match = _re.search(r'(?:Name|name)\s*[:=]\s*(.+)', content)
            if name_match:
                return name_match.group(1).strip().strip('*_"\'')
        soul = self.load_soul()
        if soul:
            name_match = _re.search(r'your name is (\w+)', soul, _re.IGNORECASE)
            if name_match:
                return name_match.group(1).strip()
        return None
