"""Compatibility imports for AgentSession telemetry instrumentation."""

from .instrumentation import AgentSessionInstrumentation, instrument_agent_session

__all__ = ["AgentSessionInstrumentation", "instrument_agent_session"]
