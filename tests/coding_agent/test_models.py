from __future__ import annotations

from pi_agent.ai import Model, ModelRegistry


def test_model_registry_lists_and_resolves_unique_models() -> None:
    registry = ModelRegistry()
    left = Model(
        api="openai-completions",
        provider="left",
        id="shared",
        name="Left",
        context_window=32_000,
        max_tokens=4096,
    )
    right = Model(
        api="openai-completions",
        provider="right",
        id="unique",
        name="Right",
        reasoning=True,
        input=("text", "image"),
        context_window=128_000,
        max_tokens=8192,
    )
    registry.register(left)
    registry.register(right)

    assert registry.get("left", "shared").name == "Left"
    assert registry.resolve("unique").provider == "right"
    assert [(model.provider, model.id) for model in registry.list()] == [
        ("left", "shared"),
        ("right", "unique"),
    ]
