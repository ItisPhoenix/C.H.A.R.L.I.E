import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("charlie.integrations.base")

class BaseIntegration(ABC):
    """
    BaseIntegration: Abstract base class for all external service integrations.
    Each integration: connect(), fetch(), execute(action), disconnect().
    """
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def connect(self) -> bool:
        """Establishes connection/authentication with the service."""
        pass

    @abstractmethod
    def fetch(self, **kwargs) -> Any:
        """Retrieves data from the service."""
        pass

    @abstractmethod
    def execute(self, action: str, **kwargs) -> bool:
        """Executes an action on the service."""
        pass

    @abstractmethod
    def disconnect(self):
        """Cleanly closes the connection."""
        pass
