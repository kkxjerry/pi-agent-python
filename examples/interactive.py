from __future__ import annotations

import asyncio
import os

from pi_agent.ai import Model, OpenAICompatibleProvider
from pi_agent.coding_agent import CreateAgentSessionOptions, create_agent_session
from pi_agent.tui import run_interactive_mode


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    model = Model(
        api="openai-completions",
        provider="openai",
        id="gpt-4.1-mini",
        name="GPT-4.1 mini",
        base_url="https://api.openai.com/v1",
        context_window=128_000,
        max_tokens=16_384,
    )
    provider = OpenAICompatibleProvider(api_key=api_key)
    created = await create_agent_session(
        CreateAgentSessionOptions(
            cwd=".",
            model=model,
            stream_fn=provider.stream,
            api_key=api_key,
        )
    )
    session = created.session
    try:
        await run_interactive_mode(session)
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
