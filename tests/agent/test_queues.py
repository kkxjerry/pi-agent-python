from __future__ import annotations

from pi_agent.agent import MessageQueue
from pi_agent.ai import UserMessage


def test_message_queue_one_at_a_time_and_all_modes() -> None:
    queue = MessageQueue("one-at-a-time")
    queue.push(UserMessage("one"))
    queue.push(UserMessage("two"))

    assert [message.content for message in queue.drain()] == ["one"]
    assert len(queue) == 1

    queue.mode = "all"
    queue.push(UserMessage("three"))
    assert [message.content for message in queue.drain()] == ["two", "three"]
    assert len(queue) == 0

    queue.clear()
    assert queue.drain() == []
