.PHONY: sync format format-check lint type test phase-0-3 phase-8-12 phase-13-16 phase-17-20 phase-21-24 phase-25-31 phase-31-release check benchmark benchmark-agents build

sync:
	uv sync --all-groups

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

type:
	uv run mypy --strict src

test:
	uv run pytest -q

phase-0-3:
	python3.11 scripts/check_phase_0_3.py

phase-8-12:
	uv run python scripts/check_phase_8_12.py

phase-13-16:
	uv run python scripts/check_phase_13_16.py

phase-17-20:
	uv run python scripts/check_phase_17_20.py

phase-21-24:
	uv run python scripts/check_phase_21_24.py

phase-25-31:
	uv run python scripts/check_phase_25_31.py

phase-31-release:
	uv run python scripts/check_phase_31_release.py

check: format-check lint type test phase-0-3 phase-8-12 phase-13-16 phase-17-20 phase-21-24 phase-25-31
	uv lock --check
	git diff --check

benchmark:
	uv run python benchmarks/benchmark_phase_25_31.py --iterations 10000

benchmark-agents:
	uv run --python 3.11 python benchmarks/agent_compare/run.py --core-parity --repetitions 3

build:
	uv build
