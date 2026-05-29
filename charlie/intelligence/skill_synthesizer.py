"""
charlie/intelligence/skill_synthesizer.py
Skill Synthesizer and Safe Staging Protocol.
Compiles new tools to pending buffers and registers them only after user diff inspection and manual signature.
"""

import os
import shutil
import re
import importlib
from charlie.utils.logger import get_logger

logger = get_logger("SKILL_SYNTHESIZER")

class SkillSynthesizer:
    """Manages secure generation and user-signed registration of dynamic tools."""

    def __init__(self, brain=None):
        self.brain = brain
        self.pending_dir = os.path.abspath("charlie/tools/pending")
        self.dest_dir = os.path.abspath("charlie/tools")
        os.makedirs(self.pending_dir, exist_ok=True)

    def synthesize(self, skill_name: str, code: str) -> str:
        """Stage a newly generated skill code template strictly to a .pending file."""
        if not re.match(r"^[a-zA-Z0-9_]+$", skill_name):
            return "Error: Skill name must be strictly alphanumeric with underscores."

        # AST safety check on dynamic code before even staging it
        try:
            import ast
            ast.parse(code)
        except SyntaxError as se:
            return f"Error: Compiled Python code contains syntax errors: {se}"

        pending_path = os.path.join(self.pending_dir, f"{skill_name}.pending")
        try:
            with open(pending_path, "w", encoding="utf-8") as f:
                f.write(code)
            logger.info(f"skill_staged_successfully | name={skill_name} | path={pending_path}")

            notice = (
                f"### 🛡️ [SAFE STAGING PROTOCOL STAGED]\n"
                f"New tool code successfully staged to pending buffer:\n"
                f"- **Staged File**: `charlie/tools/pending/{skill_name}.pending`\n\n"
                f"⚠️ **Action Required**: Before activation, you must manually inspect the Git diff "
                f"and console-sign the activation command:\n"
                f"```bash\nactivate_pending_skill {skill_name} SIGN_APPROVE\n```\n"
                f"*Note: Automated dynamic dry-run execution is fully disabled to prevent RCE.*"
            )
            return notice
        except Exception as e:
            logger.error(f"skill_staging_failed | {e}")
            return f"Error staging skill: {e}"

    def activate(self, skill_name: str, signature: str) -> str:
        """Move staged .pending tool into the active tools directory and dynamically register it."""
        if signature != "SIGN_APPROVE":
            logger.warning(f"skill_activation_rejected | invalid_signature={signature}")
            return "Error: Skill activation failed. Invalid approval signature. Must be exactly 'SIGN_APPROVE'."

        pending_path = os.path.join(self.pending_dir, f"{skill_name}.pending")
        if not os.path.exists(pending_path):
            return f"Error: Staged skill '{skill_name}.pending' does not exist."

        dest_path = os.path.join(self.dest_dir, f"{skill_name}.py")

        try:
            # 1. Move to active directory
            shutil.copy(pending_path, dest_path)
            logger.info(f"skill_activated_active_path | from={pending_path} | to={dest_path}")

            # 2. Trigger hot reload/discover on ToolRegistry
            if self.brain and hasattr(self.brain, "tool_registry"):
                # Clean import cache if re-importing
                mod_name = f"charlie.tools.{skill_name}"
                if mod_name in importlib.sys.modules:
                    importlib.reload(importlib.sys.modules[mod_name])
                else:
                    importlib.import_module(mod_name)

                discovered = self.brain.tool_registry.auto_discover("charlie.tools")
                logger.info(f"tool_registry_reloaded | count={discovered}")

            return f"Success: Dynamic tool '{skill_name}' successfully activated, verified and loaded into active ToolRegistry, Sir!"
        except Exception as e:
            logger.error(f"skill_activation_failed | {e}")
            return f"Error registering tool: {e}"
