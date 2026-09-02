# Phase 0–3 acceptance evidence

The new local directory initially contained only `.editorconfig` and
`.python-version`; it did not contain the prior artifact archive. The baseline was
therefore rebuilt on `develop` and accepted from reproducible evidence rather than
assuming the previous chat artifact was present.

## Phase 0

- repository/tag/commit/archive SHA are pinned in `UPSTREAM.md`;
- the upstream MIT attribution is retained;
- project and fixture manifests embed the same baseline;
- exact npm dependency versions are locked for fixture execution.

## Phase 1

Eight behavior documents and a machine-readable source map cover packages,
events, Agent Loop, session tree, compaction, extensions, RPC, and TUI. Each
contract records ownership, event/state transitions, failure semantics, and the
Python target boundary.

## Phase 2

Thirty unique scenarios are registered. Ten scenarios were executed using the
exact official `0.84.4` AI and Agent Core npm packages. The TypeScript runner
passed `tsc --noEmit`. Fresh events/results exactly matched the earlier
source-derived contracts before fixture provenance was promoted to
`upstream-execution`.

## Phase 3

The repository includes Python 3.11+ packaging, typed-package marker, metadata
CLI, pytest/asyncio tests, Ruff, strict mypy, CI matrix, sdist/wheel build, and
phase gates. Final command evidence is recorded in the Phase 4–7 acceptance run.

## Release archive byte verification status

Two local download attempts for the official `pi-v0.84.4.tar.gz` asset were made:

1. Python `urllib` returned HTTP 502.
2. `curl` with HTTP/1.1 and four retries exceeded the 360-second execution gate.

No archive bytes were accepted and no checksum success is claimed. The expected
SHA-256 remains pinned and `scripts/fetch_upstream.py` / `verify_upstream.py`
provide the reproducible gate. This evidence gap does not alter the exact npm
package execution evidence, but it remains explicitly open.
