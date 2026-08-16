"""MessageBroker — async in-process message bus for inter-agent communication."""

import asyncio
import logging
from collections import defaultdict

from harness.models.messages import AgentMessage, MessageType

logger = logging.getLogger(__name__)


class AgentNotFoundError(Exception):
    """Raised when a target agent is not registered with the broker."""
    pass


class MessageBroker:
    """Async in-process message broker with direct and pub/sub messaging.

    Supports:
    - Direct agent-to-agent messages (point-to-point)
    - Channel-based publish/subscribe
    - RPC-style request/response with timeout
    """

    _subscriptions: dict[str, list[asyncio.Queue[AgentMessage]]]
    _agents: dict[str, asyncio.Queue[AgentMessage]]

    def __init__(self) -> None:
        self._subscriptions = defaultdict(list)
        self._agents = {}

    async def register_agent(self, agent_id: str) -> asyncio.Queue[AgentMessage]:
        """Register an agent and return its inbox queue.

        Args:
            agent_id: Unique agent identifier.

        Returns:
            An async queue for the agent to receive messages.
        """
        queue: asyncio.Queue[AgentMessage] = asyncio.Queue(maxsize=128)
        self._agents[agent_id] = queue
        return queue

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent's registration."""
        self._agents.pop(agent_id, None)

    async def send(self, message: AgentMessage) -> None:
        """Send a direct message to a specific agent.

        Args:
            message: The message to send.

        Raises:
            AgentNotFoundError: If the target agent is not registered.
        """
        if message.target_agent is None:
            raise ValueError("message.target_agent must be set for direct send")

        target_queue = self._agents.get(message.target_agent)
        if target_queue is None:
            raise AgentNotFoundError(f"Agent '{message.target_agent}' not registered")

        await target_queue.put(message)

    async def publish(self, channel: str, message: AgentMessage) -> None:
        """Publish a message to all subscribers of a channel.

        Args:
            channel: The channel name.
            message: The message to publish.
        """
        subscribers = self._subscriptions.get(channel, [])
        for queue in subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning(f"Subscriber queue full for channel '{channel}'")

    async def subscribe(self, channel: str) -> asyncio.Queue[AgentMessage]:
        """Subscribe to a channel.

        Args:
            channel: Channel name to subscribe to.

        Returns:
            An async queue receiving messages published to the channel.
        """
        queue: asyncio.Queue[AgentMessage] = asyncio.Queue(maxsize=256)
        self._subscriptions[channel].append(queue)
        return queue

    async def request(self, message: AgentMessage, timeout: float = 30.0) -> AgentMessage:
        """Send a REQUEST and wait for a RESPONSE (RPC pattern).

        Args:
            message: The request message (type must be REQUEST).
            timeout: Maximum wait time for the response.

        Returns:
            The response message.

        Raises:
            asyncio.TimeoutError: If no response received within timeout.
        """
        message.message_type = MessageType.REQUEST
        await self.send(message)

        # Wait for a RESPONSE with matching correlation_id
        target_queue = self._agents.get(message.source_agent)
        if target_queue is None:
            raise AgentNotFoundError(f"Cannot receive response: source agent '{message.source_agent}' not registered")

        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"Request to '{message.target_agent}' timed out")

            response = await asyncio.wait_for(target_queue.get(), timeout=remaining)
            if (
                response.message_type in (MessageType.RESPONSE, MessageType.ERROR)
                and response.correlation_id == message.message_id
            ):
                return response
            # Put back non-matching messages
            await target_queue.put(response)
