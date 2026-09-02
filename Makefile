.PHONY: install format format-check lint typecheck test phase-0-3 phase-13-16 upstream-fixtures check build clean

install:
	uv sync --all-extras

format:
	uv run ruff format src tests scripts

format-check:
	uv run ruff format --check src tests scripts

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy src

test:
	uv run pytest -q

phase-0-3:
	uv run python scripts/check_phase_0_3.py

phase-13-16:
	uv run python scripts/check_phase_13_16.py

upstream-fixtures:
	npm --prefix tools/upstream-fixtures run typecheck
	rm -rf /tmp/pi-upstream-capture
	npm --prefix tools/upstream-fixtures run capture -- --out /tmp/pi-upstream-capture
	uv run python scripts/compare_upstream_capture.py /tmp/pi-upstream-capture

check: format-check lint typecheck test phase-0-3 phase-13-16

build:
	uv build

clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache
