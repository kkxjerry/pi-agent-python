.PHONY: sync format format-check lint type test phase-0-3 phase-8-12 phase-17-20 phase-21-24 check build

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
	python3.11 scripts/check_phase_8_12.py

phase-17-20:
	python3.11 scripts/check_phase_17_20.py

check: phase-21-24
	uv lock --check
	$(MAKE) format-check
	$(MAKE) lint
	$(MAKE) type
	$(MAKE) test
	$(MAKE) phase-0-3
	$(MAKE) phase-8-12
	$(MAKE) phase-17-20
	git diff --check

build:
	uv build

phase-21-24:
	uv run python scripts/check_phase_21_24.py
