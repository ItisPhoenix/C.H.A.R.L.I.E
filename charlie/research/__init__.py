"""Charlie web research subsystem.

Research acquires and verifies public-web evidence. Interactive website work
remains owned by ``charlie.browser``.
"""

from charlie.research.engine import ResearchEngine
from charlie.research.models import ResearchMode, ResearchReport
from charlie.research.router import ResearchDecision, route

__all__ = ["ResearchDecision", "ResearchEngine", "ResearchMode", "ResearchReport", "route"]
