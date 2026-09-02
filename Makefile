.PHONY: check test lint format typecheck
check:
	python3.11 scripts/check_phase_0_3.py
	uv run --python 3.11 pytest -q
	uv run --python 3.11 ruff check .
	uv run --python 3.11 ruff format --check .

test:
	uv run --python 3.11 pytest -q

lint:
	uv run --python 3.11 ruff check .

format:
	uv run --python 3.11 ruff format .

typecheck:
	uv run --python 3.11 mypy src
