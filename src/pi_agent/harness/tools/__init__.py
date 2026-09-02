"""Coding tools backed by an injectable execution environment."""

from pi_agent.agent import AgentTool

from ..types import ExecutionEnv
from .bash import create_bash_tool
from .edit import create_edit_tool
from .read import create_read_tool
from .write import create_write_tool


def create_coding_tools(env: ExecutionEnv) -> list[AgentTool]:
    return [
        create_read_tool(env),
        create_write_tool(env),
        create_edit_tool(env),
        create_bash_tool(env),
    ]


__all__ = [
    "create_bash_tool",
    "create_coding_tools",
    "create_edit_tool",
    "create_read_tool",
    "create_write_tool",
]
