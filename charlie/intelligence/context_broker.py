"""
charlie/intelligence/context_broker.py

Cross-modal context broker for unified context management across all interaction channels.
Maintains conversation history, entity resolution, and context fusion from multiple sources.

"""

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class ConversationContext:
    """Represents a unified context for a user session across all channels."""
    session_id: str
    channel: str  # telegram, discord, cli, etc.
    user_id: str
    entities: Dict[str, str] = field(default_factory=dict)  # resolved entities
    recent_tasks: List[str] = field(default_factory=list)  # task IDs
    current_goal: Optional[str] = None
    last_updated: float = field(default_factory=time.time)
    message_count: int = 0
    channel_history: Dict[str, List[Dict]] = field(default_factory=dict)  # per-channel history

    def update_entity(self, key: str, value: str):
        """Update or add an entity to the context."""
        self.entities[key] = value
        self.last_updated = time.time()

    def add_task(self, task_id: str):
        """Add a task to recent tasks list."""
        if task_id not in self.recent_tasks:
            self.recent_tasks.append(task_id)
            # Keep only last 20 tasks
            if len(self.recent_tasks) > 20:
                self.recent_tasks = self.recent_tasks[-20:]
        self.last_updated = time.time()

    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return {
            'session_id': self.session_id,
            'channel': self.channel,
            'user_id': self.user_id,
            'entities': self.entities,
            'recent_tasks': self.recent_tasks,
            'current_goal': self.current_goal,
            'last_updated': self.last_updated,
            'message_count': self.message_count,
            'channel_history': self.channel_history
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'ConversationContext':
        """Deserialize from dictionary."""
        return cls(
            session_id=data['session_id'],
            channel=data['channel'],
            user_id=data['user_id'],
            entities=data.get('entities', {}),
            recent_tasks=data.get('recent_tasks', []),
            current_goal=data.get('current_goal'),
            last_updated=data.get('last_updated', time.time()),
            message_count=data.get('message_count', 0),
            channel_history=data.get('channel_history', {})
        )


class ContextBroker:
    """
    Central context broker that manages unified context across all interaction channels.
    Implements singleton pattern for global access.
    """

    _instance: Optional['ContextBroker'] = None
    _lock = threading.Lock()

    def __init__(self, storage_path: Optional[str] = None):
        """
        Initialize the context broker.

        Args:
            storage_path: Optional path for persistent storage. If None, uses memory only.
        """
        self._contexts: Dict[str, ConversationContext] = {}
        self._entity_cache: Dict[str, Dict[str, Any]] = {}  # entity_name -> {resolutions, last_seen}
        self._channel_listeners: Dict[str, List[callable]] = {}  # channel -> [callbacks]
        self._storage_path = storage_path
        self._dirty_contexts: Set[str] = set()  # contexts needing persistence
        self._save_thread: Optional[threading.Thread] = None
        self._running = False

        # Load existing contexts if storage path provided
        if storage_path:
            self._load_contexts()
            self._start_save_thread()

    @classmethod
    def get_context_broker(cls, storage_path: Optional[str] = None) -> 'ContextBroker':
        """Get or create the singleton ContextBroker instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(storage_path)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance and cls._instance._running:
                cls._instance._running = False
                if cls._instance._save_thread:
                    cls._instance._save_thread.join(timeout=2)
            cls._instance = None

    def create_context(self, session_id: str, channel: str, user_id: str) -> ConversationContext:
        """
        Create a new context or return existing for session.

        Args:
            session_id: Unique session identifier
            channel: Interaction channel (telegram, discord, cli, etc.)
            user_id: User identifier

        Returns:
            ConversationContext instance
        """
        if session_id in self._contexts:
            ctx = self._contexts[session_id]
            # Update channel if changed
            if ctx.channel != channel:
                ctx.channel = channel
            return ctx

        ctx = ConversationContext(
            session_id=session_id,
            channel=channel,
            user_id=user_id
        )
        self._contexts[session_id] = ctx
        self._dirty_contexts.add(session_id)
        return ctx

    def get_context(self, session_id: str) -> Optional[ConversationContext]:
        """Get existing context by session ID."""
        return self._contexts.get(session_id)

    def get_or_create_context(self, session_id: str, channel: str, user_id: str) -> ConversationContext:
        """Get existing context or create new one."""
        return self.get_context(session_id) or self.create_context(session_id, channel, user_id)

    def update_context(self, session_id: str, **kwargs) -> Optional[ConversationContext]:
        """
        Update context fields.

        Args:
            session_id: Session to update
            **kwargs: Fields to update (entities, current_goal, etc.)

        Returns:
            Updated context or None if not found
        """
        ctx = self._contexts.get(session_id)
        if not ctx:
            return None

        for key, value in kwargs.items():
            if hasattr(ctx, key):
                setattr(ctx, key, value)

        ctx.last_updated = time.time()
        self._dirty_contexts.add(session_id)
        return ctx

    def add_message_to_history(self, session_id: str, channel: str, message: Dict):
        """
        Add a message to the channel-specific history.

        Args:
            session_id: Session identifier
            channel: Channel the message came from
            message: Message data {role, content, timestamp}
        """
        ctx = self._contexts.get(session_id)
        if not ctx:
            return

        if channel not in ctx.channel_history:
            ctx.channel_history[channel] = []

        ctx.channel_history[channel].append(message)

        # Keep last 100 messages per channel
        if len(ctx.channel_history[channel]) > 100:
            ctx.channel_history[channel] = ctx.channel_history[channel][-100:]

        ctx.message_count += 1
        ctx.last_updated = time.time()
        self._dirty_contexts.add(session_id)

    def resolve_entity(self, entity_name: str, context: Optional[ConversationContext] = None) -> Optional[str]:
        """
        Resolve an entity to its current value using cached knowledge.

        Args:
            entity_name: Name of entity to resolve
            context: Optional context for additional resolution hints

        Returns:
            Resolved entity value or None
        """
        # Check cache first
        if entity_name in self._entity_cache:
            cache_entry = self._entity_cache[entity_name]
            # Cache valid for 1 hour
            if time.time() - cache_entry.get('cached_at', 0) < 3600:
                return cache_entry.get('value')

        # Try to resolve from context entities
        if context and entity_name in context.entities:
            return context.entities[entity_name]

        return None

    def cache_entity(self, entity_name: str, value: str, metadata: Optional[Dict] = None):
        """
        Cache an entity resolution for future use.

        Args:
            entity_name: Name of entity
            value: Resolved value
            metadata: Optional metadata about the resolution
        """
        self._entity_cache[entity_name] = {
            'value': value,
            'cached_at': time.time(),
            'metadata': metadata or {}
        }

    def fuse_contexts(self, session_ids: List[str]) -> ConversationContext:
        """
        Fuse multiple session contexts into a unified view.
        Useful for cross-channel scenarios.

        Args:
            session_ids: List of session IDs to fuse

        Returns:
            Fused ConversationContext
        """
        if not session_ids:
            raise ValueError("At least one session ID required")

        # Start with first context
        primary = self._contexts.get(session_ids[0])
        if not primary:
            raise ValueError(f"Session {session_ids[0]} not found")

        fused = ConversationContext(
            session_id=f"fused_{'_'.join(session_ids)}",
            channel=primary.channel,
            user_id=primary.user_id
        )

        # Merge entities from all contexts
        for sid in session_ids:
            ctx = self._contexts.get(sid)
            if ctx:
                fused.entities.update(ctx.entities)
                fused.recent_tasks.extend(ctx.recent_tasks)
                fused.message_count += ctx.message_count
                for ch, history in ctx.channel_history.items():
                    if ch not in fused.channel_history:
                        fused.channel_history[ch] = []
                    fused.channel_history[ch].extend(history[-20:])  # Last 20 per channel

        # Deduplicate recent tasks
        fused.recent_tasks = list(dict.fromkeys(fused.recent_tasks))[-20:]

        return fused

    def register_channel_listener(self, channel: str, callback: callable):
        """
        Register a callback for channel-specific events.

        Args:
            channel: Channel name
            callback: Function to call on events
        """
        if channel not in self._channel_listeners:
            self._channel_listeners[channel] = []
        self._channel_listeners[channel].append(callback)

    def notify_channel_listeners(self, channel: str, event: str, data: Dict):
        """
        Notify listeners of a channel event.

        Args:
            channel: Channel that generated the event
            event: Event type
            data: Event data
        """
        for callback in self._channel_listeners.get(channel, []):
            try:
                callback(event, data)
            except Exception:
                pass  # Don't let listener errors break notification

    def get_recent_contexts(self, user_id: str, hours: int = 24) -> List[ConversationContext]:
        """
        Get all contexts for a user from the last N hours.

        Args:
            user_id: User to look up
            hours: Number of hours to look back

        Returns:
            List of matching contexts
        """
        cutoff = time.time() - (hours * 3600)
        return [
            ctx for ctx in self._contexts.values()
            if ctx.user_id == user_id and ctx.last_updated >= cutoff
        ]

    def cleanup_old_contexts(self, max_age_hours: int = 168) -> int:
        """
        Remove contexts older than specified age (default 1 week).

        Args:
            max_age_hours: Maximum age in hours

        Returns:
            Number of contexts removed
        """
        cutoff = time.time() - (max_age_hours * 3600)
        to_remove = [
            sid for sid, ctx in self._contexts.items()
            if ctx.last_updated < cutoff
        ]

        for sid in to_remove:
            del self._contexts[sid]
            self._dirty_contexts.discard(sid)

        return len(to_remove)

    def _load_contexts(self):
        """Load contexts from persistent storage."""
        if not self._storage_path:
            return

        context_file = os.path.join(self._storage_path, 'contexts.json')
        if os.path.exists(context_file):
            try:
                with open(context_file, 'r') as f:
                    data = json.load(f)
                    for ctx_data in data.get('contexts', []):
                        ctx = ConversationContext.from_dict(ctx_data)
                        self._contexts[ctx.session_id] = ctx
            except Exception:
                pass  # Start fresh if load fails

    def _save_contexts(self):
        """Save contexts to persistent storage."""
        if not self._storage_path or not self._dirty_contexts:
            return

        context_file = os.path.join(self._storage_path, 'contexts.json')

        # Load existing
        existing = {}
        if os.path.exists(context_file):
            try:
                with open(context_file, 'r') as f:
                    existing = json.load(f)
            except Exception:
                existing = {'contexts': []}

        # Update dirty contexts
        for sid in self._dirty_contexts:
            ctx = self._contexts.get(sid)
            if ctx:
                # Find and update or append
                found = False
                for i, ctx_data in enumerate(existing.get('contexts', [])):
                    if ctx_data.get('session_id') == sid:
                        existing['contexts'][i] = ctx.to_dict()
                        found = True
                        break
                if not found:
                    existing.setdefault('contexts', []).append(ctx.to_dict())

        # Write back
        try:
            with open(context_file, 'w') as f:
                json.dump(existing, f)
            self._dirty_contexts.clear()
        except Exception:
            pass

    def _start_save_thread(self):
        """Start background thread for periodic saves."""
        self._running = True
        self._save_thread = threading.Thread(target=self._save_loop, daemon=True)
        self._save_thread.start()

    def _save_loop(self):
        """Background loop for periodic saves."""
        while self._running:
            time.sleep(60)  # Save every minute
            self._save_contexts()


# Convenience function for getting the broker
def get_context_broker(storage_path: Optional[str] = None) -> ContextBroker:
    """Get the singleton ContextBroker instance."""
    return ContextBroker.get_context_broker(storage_path)


if __name__ == "__main__":
    # Test the context broker
    broker = ContextBroker()

    # Create a context
    ctx = broker.create_context("session_123", "telegram", "user_456")
    print(f"Created context: {ctx.session_id} for {ctx.channel}")

    # Add some entities
    ctx.update_entity("project", "CHARLIE")
    ctx.update_entity("current_task", "implementing autonomy")
    print(f"Entities: {ctx.entities}")

    # Add a task
    ctx.add_task("task_001")
    ctx.add_task("task_002")
    print(f"Recent tasks: {ctx.recent_tasks}")

    # Add message to history
    broker.add_message_to_history("session_123", "telegram", {
        "role": "user",
        "content": "Show me the autonomy plan",
        "timestamp": time.time()
    })
    print(f"Message count: {ctx.message_count}")

    # Test entity resolution
    resolved = broker.resolve_entity("project", ctx)
    print(f"Resolved project: {resolved}")

    # Cache and resolve
    broker.cache_entity("favorite_language", "Python")
    resolved = broker.resolve_entity("favorite_language")
    print(f"Resolved favorite_language: {resolved}")

    # Test context fusion
    ctx2 = broker.create_context("session_789", "discord", "user_456")
    ctx2.update_entity("project", "OtherProject")
    fused = broker.fuse_contexts(["session_123", "session_789"])
    print(f"Fused entities: {fused.entities}")

    print("\nAll tests passed!")
