"""
charlie/brain/_brain_init.py

Brain initialization groups extracted from core.py for maintainability.
Each _init_* method is called during Brain.__init__() to set up subsystems.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import TYPE_CHECKING


from charlie.config import settings
from charlie.utils.logger import get_logger

if TYPE_CHECKING:
    from charlie.brain.core import Brain

logger = get_logger(__name__)


def init_core_handlers(brain: "Brain") -> None:
    """Core brain handlers: memory, tools, reactor, stream, vision."""
    from charlie.brain.chain_executor import ChainExecutor
    from charlie.brain.context_builder import ContextBuilder
    from charlie.brain.reactor import Reactor
    from charlie.brain.skill_injector import SkillInjector
    from charlie.brain.skill_loader import SkillLoader
    from charlie.brain.stream_handler import StreamHandler
    from charlie.brain.task_manager import AsyncTaskManager
    from charlie.brain.tool_handler import ToolHandler
    from charlie.brain.agent_bus import AgentBus
    from charlie.brain.vision_handler import VisionHandler
    from charlie.intelligence.evolution_engine import EvolutionEngine
    from charlie.intelligence.skill_nudge import SkillNudgeEngine
    from charlie.intelligence.user_model import UserModelEngine
    from charlie.memory.memory_coordinator import MemoryCoordinator
    from charlie.memory.session_search import SessionSearchEngine
    from charlie.security.confidence_gate import ConfidenceGate
    from charlie.tools.messenger import Messenger
    from charlie.tools.tool_registry import ToolRegistry
    from charlie.utils.guardian import Guardian
    from charlie.utils.mentor import MentorSystem

    brain.memory = MemoryCoordinator()
    brain.session_search = SessionSearchEngine()
    brain.user_model = UserModelEngine()
    brain.skill_nudge = SkillNudgeEngine()
    brain.evolution_engine = EvolutionEngine()
    brain.confidence_gate = ConfidenceGate()
    brain.agent_bus = AgentBus()
    brain.skill_loader = SkillLoader(skills_dir="charlie/skills")
    brain.skill_loader.load_all()
    brain.skill_injector = SkillInjector(skill_loader=brain.skill_loader)
    brain.task_mgr = AsyncTaskManager(max_concurrent=3)
    brain.mentor = MentorSystem()
    brain.messenger = Messenger()
    brain.guardian = Guardian()
    brain.chain_mgr = ChainExecutor()

    # Modular Handlers
    brain.context_builder = ContextBuilder(brain)
    brain.tool_handler = ToolHandler(brain)
    brain.tool_registry = ToolRegistry()
    brain.stream_handler = StreamHandler(brain)
    brain.vision_handler = VisionHandler(brain)
    brain.reactor = Reactor(brain)


def init_mcp(brain: "Brain") -> None:
    """MCP infrastructure (lazy init — servers connect on first tool call)."""
    from charlie.mcp.bridge import MCPToolBridge
    from charlie.mcp.manager import MCPManager

    brain.mcp_manager = MCPManager()
    brain.mcp_bridge = MCPToolBridge(brain.mcp_manager)

    # Give the bridge access to the unified tool registry (Req 9.3)
    if hasattr(brain, "tool_registry"):
        brain.mcp_bridge.set_registry(brain.tool_registry)

    # Register MCP shutdown hook so Brain._shutdown_async tears down all
    # MCP sessions cleanly (Req 9.7).
    brain._shutdown_hooks.append(brain.mcp_manager.stop_all_sync)


def init_personality(brain: "Brain") -> None:
    """Personality and relationship management."""
    from charlie.personality.drift_engine import PersonalityDriftEngine

    brain.drift = PersonalityDriftEngine(brain)


def init_security(brain: "Brain") -> None:
    """Security snapshot and self-modification router."""
    from charlie.security.snapshot import SnapshotManager

    brain.snapshot = SnapshotManager()


def init_state(brain: "Brain") -> None:
    """Mutable brain state — history, widgets, timers, flags."""
    brain.active_window = "None"
    brain.history = []
    brain._history_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "scratch",
        "conversation_history.json",
    )
    brain._load_history()

    # Seen trackers for proactive notifications (now in ProceduralMemory.seen_ids)
    brain._last_service_poll: float = 0.0

    brain._last_frustration_alert: float = 0.0

    # Event loop — created in run(), not here
    brain.loop = None
    brain.session = None

    brain.is_busy = False
    brain.standby_mode = False
    brain.conversation_active = False
    brain.system_prompt = ""  # Reactor writes to this attr; init it.
    brain.awaiting_confirmation = None
    brain.confirmation_event = None  # Created in _async_init when event loop exists
    brain.confirmation_result = None
    brain.last_confirmation_time = 0.0
    brain.active_timers: dict = {}
    brain.active_stopwatches: dict = {}
    brain.timers_lock = threading.Lock()
    brain._stop_event = threading.Event()

    # Centralized idle detection — single source of truth shared by
    # ProactivityEngine, AutonomyLoop, and any other idle-aware subsystem.
    from charlie.perception.idle import IdleWatcher

    brain.idle_watcher = IdleWatcher()

    # Lifecycle
    brain._startup_time: float = time.time()
    brain._shutdown_hooks: list = []


def init_intelligence(brain: "Brain") -> None:
    """Ambient intelligence: world model, scheduler, suggestions, RAG."""
    from charlie.intelligence.graph_builder import GraphBuilder
    from charlie.intelligence.memory_graph import MemoryGraph
    from charlie.intelligence.scheduler import TaskScheduler
    from charlie.intelligence.suggestion_engine import SuggestionEngine
    from charlie.intelligence.task_queue import AutonomousTaskQueue
    from charlie.memory.rag_indexer import ProjectIndexer
    from charlie.perception.ambient_context import AmbientContextEngine
    from charlie.perception.world_model import WorldModel

    brain.world = WorldModel()
    brain.ace = AmbientContextEngine(brain.world)
    brain.task_queue = AutonomousTaskQueue(brain.world)
    brain.scheduler = TaskScheduler(brain.task_queue, brain=brain)

    brain.suggestion_engine = SuggestionEngine()
    if hasattr(brain.ace, "tracker"):
        brain.suggestion_engine.set_pattern_tracker(brain.ace.tracker)
    brain.suggestion_engine.set_brain(brain)
    brain.suggestion_engine.delivery_callback = brain._on_suggestion

    # Pass shared ChromaDB client to RAG indexer to avoid duplicate backend
    shared_chroma = getattr(brain.memory, "_chroma_client", None)
    brain.rag_indexer = ProjectIndexer(root_dir=".", chroma_client=shared_chroma)

    brain.graph = MemoryGraph()
    brain.graph_builder = GraphBuilder(brain.graph, brain.memory)


def init_automation(brain: "Brain") -> None:
    """Automation engine: orchestrator, rules, risk gate, autonomy loop."""
    from charlie.automation.autonomy_loop import AutonomyLoop
    from charlie.automation.risk_gate import RiskGate
    from charlie.automation.rule_engine import RuleEngine
    from charlie.automation.rules import get_default_rules
    from charlie.brain.agent import Orchestrator
    from charlie.brain.agent_creator import AgentCreator
    from charlie.brain.agent_factory import AgentFactory

    brain.orchestrator = Orchestrator(brain)
    brain.rule_engine = RuleEngine()
    brain.risk_gate = RiskGate(brain=brain)
    brain.autonomy_loop = AutonomyLoop(brain=brain)
    brain.agent_factory = AgentFactory(agents_dir="charlie/agents")
    brain.agent_creator = AgentCreator(agents_dir="charlie/agents")
    for rule in get_default_rules():
        brain.rule_engine.add_rule(rule)


def init_external_controllers(brain: "Brain") -> None:
    """External controllers: app, browser, research."""
    from charlie.tools.app_controller import UniversalAppController
    from charlie.tools.browser_controller import AdvancedBrowserController

    brain.app_controller = UniversalAppController()
    brain.browser_controller = AdvancedBrowserController()
    # analyze_dependencies is exposed as a Pattern A @tool function in
    # charlie.tools.research_tools — no controller object needed.


def init_model(brain: "Brain") -> None:
    """LLM model manager, lock, and shared LLM client."""
    from charlie.brain.llm_client import LLMClient
    from charlie.brain.model_manager import ModelManager

    brain.model_manager = ModelManager(settings)
    brain.model = settings.llm.llm_model
    brain.async_llm_lock = asyncio.Lock()
    brain.tool_execution_lock = threading.Lock()

    brain.model_router = brain.model_manager.router  # Reuse single instance
    brain.llm_client = LLMClient(brain.model_router)
