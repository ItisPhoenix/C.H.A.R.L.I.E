"""
charlie/tools/skill_synthesizer.py
Dynamic tool wrappers for skill staging and activation tools.
"""

from charlie.tools.tool_decorator import tool, RiskTier
from charlie.intelligence.skill_synthesizer import SkillSynthesizer
from charlie.utils import queue_bridge


@tool(
    name="synthesize_new_skill",
    description="Compile and stage a new custom Python skill/tool strictly to a .pending file for user inspection",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def synthesize_new_skill(skill_name: str, code: str) -> str:
    """Stage a newly generated skill code strictly to a pending buffer."""
    brain = queue_bridge.get_brain()
    synthesizer = SkillSynthesizer(brain=brain)
    return synthesizer.synthesize(skill_name, code)


@tool(
    name="activate_pending_skill",
    description="Activate and dynamically register a staged .pending skill into active ToolRegistry",
    risk_tier=RiskTier.TIER_2,
    category="security",
)
def activate_pending_skill(skill_name: str, approval_signature: str) -> str:
    """Deploy and hot-reload a staged skill after user signing."""
    brain = queue_bridge.get_brain()
    synthesizer = SkillSynthesizer(brain=brain)
    return synthesizer.activate(skill_name, approval_signature)


@tool(
    name="approve_file_exfiltration",
    description="Approve and deliver a staged workspace file to your mobile device via Telegram",
    risk_tier=RiskTier.TIER_1,
    category="security",
)
def approve_file_exfiltration(file_path: str) -> str:
    """Approve and dispatch a staged document to mobile Telegram bridge."""
    import logging

    logger = logging.getLogger("charlie.tools.exfiltration")

    brain = queue_bridge.get_brain()
    if not brain or not hasattr(brain, "autonomy_loop"):
        return "Error: AutonomyLoop not initialized."

    loop = brain.autonomy_loop
    if file_path not in loop.pending_exfiltrations:
        return f"Error: File '{file_path}' is not staged for exfiltration review."

    file_info = loop.pending_exfiltrations.pop(file_path)

    # Exfiltrate securely to Telegram
    if brain.telegram_q:
        brain._safe_put(brain.telegram_q, {"type": "FILE", "content": file_path})
        logger.warning(f"file_exfiltrated_successfully | path={file_path}")
        return f"Success: Staged file '{file_info['name']}' has been approved and dispatched to mobile, Sir!"
    return "Error: Telegram queue bridge is offline."
