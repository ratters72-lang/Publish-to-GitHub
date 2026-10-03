"""Base event system for decoupled communication between systems."""

from typing import Callable, Any


class Event:
    """Base event class. Subclass to define specific event types."""

    def __init__(self, name: str = "", **data):
        self.name = name
        self.data = data


EventHandler = Callable[[Event], None]


class EventEmitter:
    """Simple pub/sub event emitter."""

    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = {}

    def on(self, event_name: str, handler: EventHandler) -> None:
        """Register a handler for an event."""
        self._handlers.setdefault(event_name, []).append(handler)

    def off(self, event_name: str, handler: EventHandler) -> None:
        """Remove a handler for an event."""
        handlers = self._handlers.get(event_name, [])
        if handler in handlers:
            handlers.remove(handler)

    def emit(self, event: Event) -> None:
        """Emit an event to all registered handlers."""
        for handler in self._handlers.get(event.name, []):
            handler(event)

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()
