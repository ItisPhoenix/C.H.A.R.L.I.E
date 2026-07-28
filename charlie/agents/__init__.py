"""Charlie Agent Swarm -- MARVEL-named specialized agents.

Each agent wraps a specific capability and operates on the shared Blackboard.
J.A.R.V.I.S. orchestrates; the others are workers.
"""

from charlie.agents.base import BaseAgent
from charlie.agents.edith import EDITH
from charlie.agents.friday import FRIDAY
from charlie.agents.helm import HELM
from charlie.agents.jarvis import JarvisAgent
from charlie.agents.karen import KAREN
from charlie.agents.shuri import SHURI
from charlie.agents.strange import StrangeAgent
from charlie.agents.vision import VisionAgent

AGENT_REGISTRY = {
    "J.A.R.V.I.S.": JarvisAgent,
    "Doctor Strange": StrangeAgent,
    "Shuri": SHURI,
    "F.R.I.D.A.Y.": FRIDAY,
    "E.D.I.T.H.": EDITH,
    "K.A.R.E.N.": KAREN,
    "Vision": VisionAgent,
    "H.E.L.M.": HELM,
}

__all__ = [
    "BaseAgent",
    "JarvisAgent",
    "StrangeAgent",
    "SHURI",
    "FRIDAY",
    "EDITH",
    "KAREN",
    "VisionAgent",
    "HELM",
    "AGENT_REGISTRY",
]
