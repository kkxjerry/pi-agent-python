from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pi_agent.ai import FauxProvider, Model
from pi_agent.coding_agent import CreateAgentSessionOptions, SettingsResolver, create_agent_session


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--provider", default="dashscope")
    parser.add_argument("--model", default="qwen-plus")
    args = parser.parse_args()
    cwd = args.cwd.expanduser().resolve()
    settings = SettingsResolver().resolve(
        environ={},
        runtime={
            "session.enabled": False,
            "resources.user_root": str(cwd / ".missing-user-resources"),
            "compaction.enabled": False,
        },
    )
    model = Model(
        api="openai-completions",
        provider=args.provider,
        id=args.model,
        name=args.model,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        context_window=131072,
        max_tokens=8192,
    )
    session = (
        await create_agent_session(
            CreateAgentSessionOptions(
                cwd=cwd,
                model=model,
                stream_fn=FauxProvider([]).stream,
                settings=settings,
                no_session=True,
                include_coding_tools=True,
            )
        )
    ).session
    try:
        prompt = session.agent.system_prompt
        value = {
            "system": "python",
            "cwd": str(cwd),
            "systemPrompt": prompt,
            "systemPromptCharacters": len(prompt),
            "systemPromptLines": len(prompt.splitlines()),
            "activeToolNames": [tool.name for tool in session.agent.tools],
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in session.agent.tools
            ],
        }
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        await session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
