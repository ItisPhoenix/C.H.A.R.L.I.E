"""Charlie Agent Swarm -- MARVEL-named specialized agents.

Each agent wraps a specific capability and operates on the shared Blackboard.
J.A.R.V.I.S. orchestrates; the others are workers.
"""

from charlie.agents.aida import AIDA
from charlie.agents.base import BaseAgent
from charlie.agents.edith import EDITH
from charlie.agents.friday import FRIDAY
from charlie.agents.herbie import HERBIE
from charlie.agents.jarvis import JarvisAgent
from charlie.agents.karen import KAREN
from charlie.agents.vision import VisionAgent

AGENT_REGISTRY = {
    "J.A.R.V.I.S.": JarvisAgent,
    "Vision": VisionAgent,
    "F.R.I.D.A.Y.": FRIDAY,
    "E.D.I.T.H.": EDITH,
    "K.A.R.E.N.": KAREN,
    "H.E.R.B.I.E.": HERBIE,
    "A.I.D.A.": AIDA,
}

__all__ = [
    "BaseAgent",
    "JarvisAgent",
    "VisionAgent",
    "FRIDAY",
    "EDITH",
    "KAREN",
    "HERBIE",
    "AIDA",
    "AGENT_REGISTRY",
]
