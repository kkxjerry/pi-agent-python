from __future__ import annotations

import pytest

from pi_agent.ai import Model, ModelCost


@pytest.fixture
def model() -> Model:
    return Model(
        api="openai-completions",
        provider="fixture",
        id="fixture-model",
        name="Fixture Model",
        base_url="https://example.invalid/v1",
        reasoning=True,
        input=("text", "image"),
        cost=ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=1.5),
        context_window=8192,
        max_tokens=2048,
    )
