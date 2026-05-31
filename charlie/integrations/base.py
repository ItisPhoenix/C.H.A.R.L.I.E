import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("charlie.integrations.base")


async def call_integration(method, *args, **kwargs):
    """Sync/async call adapter for integration methods.

    If *method* is a coroutine function it is awaited directly.
    Otherwise it is run in a thread via asyncio.to_thread so the event loop
    is never blocked by a synchronous integration call.

    Requirements: 11.4
    """
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    return await asyncio.to_thread(method, *args, **kwargs)

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
