"""
BrainBuilder — constructs a Brain instance step by step.

The Brain class is the high-level cognitive hub. Its construction involves
loading many subsystems (LLM, vision, memory, tools, agents, IPC, polling,
audio). This builder centralizes the construction order and isolates the
dependency graph from the Brain class itself.

Usage:
    builder = BrainBuilder(brain)
    builder.build()
"""

from __future__ import annotations

from charlie.utils.logger import get_logger

logger = get_logger(__name__)


class BrainBuilder:
    """Assembles a Brain's subsystems in the correct order.

    The build order is significant: state and IPC must be set up before
    the subsystems that depend on them. See Core Loop Spec §E for the
    authoritative ordering.
    """

    def __init__(self, brain):
        self.brain = brain

    def build(self) -> None:
        """Run the full construction pipeline. Idempotent on the Brain."""
        brain = self.brain
        brain._init_core_handlers()
        brain._init_state()
        brain._init_mcp()
        brain._init_personality()
        brain._init_security()
        brain._init_intelligence()
        brain._init_automation()
        brain._init_external_controllers()
        brain._init_model()
        brain._discover_tools()
        logger.info("brain_build_complete")
