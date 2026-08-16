"""EventBus — async publish/subscribe event system (Observer pattern)."""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """An event in the harness system."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""


class EventBus:
    """Async publish/subscribe event bus.

    Agents publish events (intent classified, step completed, error occurred)
    and subscribers react asynchronously. Used for logging, monitoring,
    and cross-agent coordination.
    """

    _subscriptions: dict[str, list[asyncio.Queue[Event]]]

    def __init__(self) -> None:
        self._subscriptions = defaultdict(list)

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers of its type.

        Args:
            event: The event to publish.
        """
        event_type = event.event_type
        subscribers = self._subscriptions.get(event_type, [])
        if not subscribers:
            return

        logger.debug(f"Event '{event_type}' → {len(subscribers)} subscriber(s)")
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"Subscriber queue full for event '{event_type}'")

    async def subscribe(self, event_type: str) -> asyncio.Queue[Event]:
        """Subscribe to a specific event type.

        Args:
            event_type: The event type to subscribe to.

        Returns:
            An async queue that receives matching events.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        self._subscriptions[event_type].append(queue)
        return queue

    def unsubscribe(self, event_type: str, queue: asyncio.Queue[Event]) -> None:
        """Remove a subscription.

        Args:
            event_type: The event type.
            queue: The subscriber's queue to remove.
        """
        subs = self._subscriptions.get(event_type, [])
        if queue in subs:
            subs.remove(queue)

    @property
    def subscription_count(self) -> dict[str, int]:
        """Get subscriber counts per event type."""
        return {k: len(v) for k, v in self._subscriptions.items()}
