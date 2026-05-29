"""
charlie/brain/agent_bus.py

Agent-to-Agent message bus.
Enables inter-agent communication for multi-agent collaboration.
"""

import logging
import queue
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("charlie.brain.agent_bus")


class AgentMessage:
    """A message on the agent bus."""

    def __init__(
        self,
        sender: str,
        message_type: str,
        payload: Any,
        target: Optional[str] = None,
    ):
        self.sender = sender
        self.message_type = message_type
        self.payload = payload
        self.target = target  # None = broadcast
        self.timestamp = time.time()
        self.id = f"{sender}_{message_type}_{int(self.timestamp * 1000)}"


class AgentBus:
    """Publish/subscribe message bus for agent-to-agent communication."""

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}
        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._request_responses: dict[str, queue.Queue] = {}

    def start(self) -> None:
        """Start the message processing loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()
        logger.info("agent_bus_started")

    def stop(self) -> None:
        """Stop the message processing loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("agent_bus_stopped")

    def subscribe(self, agent_name: str, message_type: str, callback: Callable) -> None:
        """Subscribe to messages of a specific type."""
        key = f"{agent_name}:{message_type}"
        if key not in self._subscribers:
            self._subscribers[key] = []
        self._subscribers[key].append(callback)
        logger.debug("agent_bus_subscribe | agent=%s type=%s", agent_name, message_type)

    def publish(self, agent_name: str, message_type: str, payload: Any) -> None:
        """Publish a message to the bus."""
        msg = AgentMessage(sender=agent_name, message_type=message_type, payload=payload)
        self._queue.put(msg)

    def request_response(
        self,
        sender: str,
        target: str,
        message_type: str,
        payload: Any,
        timeout: float = 30,
    ) -> Optional[Any]:
        """Send a request and wait for a response.

        Args:
            sender: Agent sending the request
            target: Agent to receive the request
            message_type: Type of message
            payload: Message payload
            timeout: Seconds to wait for response

        Returns:
            Response payload, or None on timeout
        """
        # Create response queue
        request_id = f"{sender}_{target}_{int(time.time() * 1000)}"
        response_q = queue.Queue()
        self._request_responses[request_id] = response_q

        # Send request
        msg = AgentMessage(
            sender=sender,
            message_type=message_type,
            payload={"request_id": request_id, "data": payload},
            target=target,
        )
        self._queue.put(msg)

        # Wait for response
        try:
            result = response_q.get(timeout=timeout)
            return result
        except queue.Empty:
            logger.warning("agent_bus_timeout | sender=%s target=%s", sender, target)
            return None
        finally:
            self._request_responses.pop(request_id, None)

    def respond(self, request_id: str, payload: Any) -> None:
        """Send a response to a request."""
        response_q = self._request_responses.get(request_id)
        if response_q:
            response_q.put(payload)
        else:
            logger.warning("agent_bus_no_request | id=%s", request_id)

    def _process_loop(self) -> None:
        """Main message processing loop."""
        while self._running:
            try:
                msg = self._queue.get(timeout=0.5)
                self._dispatch(msg)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("agent_bus_error | %s", e)

    def _dispatch(self, msg: AgentMessage) -> None:
        """Dispatch a message to subscribers."""
        # Targeted message
        if msg.target:
            key = f"{msg.target}:{msg.message_type}"
            handlers = self._subscribers.get(key, [])
            for handler in handlers:
                try:
                    handler(msg)
                except Exception as e:
                    logger.error("agent_bus_handler_error | %s", e)
            return

        # Broadcast — deliver to all agents subscribed to this message type
        for key, handlers in self._subscribers.items():
            if key.endswith(f":{msg.message_type}"):
                for handler in handlers:
                    try:
                        handler(msg)
                    except Exception as e:
                        logger.error("agent_bus_handler_error | %s", e)
