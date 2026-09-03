# Phase 21–24 acceptance evidence

Baseline remains the pinned official TypeScript repository recorded in `UPSTREAM.md`. This milestone is a Python-native behavioral implementation. It is **not** marked `UPSTREAM` unless a dedicated fixture executes the pinned TypeScript package and compares the same contract.

## Phase 21 — credentials and model access boundary

Implemented in `src/pi_agent/coding_agent/auth.py` and `model_access.py`.

Accepted behavior:

- API-key and OAuth credential records with secret-free public metadata;
- atomic JSON replacement and a separate cross-process lock;
- POSIX `0600` credential-file mode;
- explicit → environment → local-store resolution precedence;
- expiry skew and single-flight OAuth refresh, including provider identity validation;
- corrupt or unsupported stores fail closed;
- provider registry model selection and credentialized `StreamOptions` are composed by `ModelAccess`.

The default store is plaintext and is not described as an OS keychain. A keychain-backed resolver can replace it at the product boundary.

## Phase 22 — Python extension API and activation lifecycle

Implemented in `src/pi_agent/coding_agent/extensions/`.

Accepted behavior:

- deterministic descriptor ordering and stable contribution registries;
- staged tools, commands, services, system-prompt fragments, event handlers, and disposers;
- collision checks before contributions become visible;
- sync or async activation, commands, events, and disposal;
- reverse-order disposal;
- strict batch rollback when a later extension fails;
- transactional reload: a failed single-extension or whole-host candidate does not replace the active extension set;
- path and declared-capability policy checks before import.

Extensions execute in the Python process. Capability checks are activation gates, not a sandbox.

## Phase 23 — local package manager

Implemented in `src/pi_agent/coding_agent/packages/` and exposed through `pi-pkg`.

Accepted behavior:

- `pi-package.json` parsing and path-traversal rejection;
- declared local extension/resource paths must exist;
- symbolic links are rejected during package copy;
- dependency presence checks and dependent-aware removal;
- staging inside the package root, atomic directory replacement, and atomic lock writes;
- deterministic SHA-256 integrity over relative paths and bytes;
- install/update/remove receipts that can finalize or restore the previous directory and lock entry; stale receipts fail rather than overwriting a newer cross-process update;
- list, verify, update, and remove operations;
- file and directory-style Python extension entry points receive deterministic package-qualified names;
- local directories and `file://` sources only.

Network registries, package install scripts, and dependency downloading are deliberately outside Phase 23.

## Phase 24 — AgentSession product integration

Implemented in `src/pi_agent/coding_agent/runtime.py`.

Accepted behavior:

- one runtime composes an existing AgentSession with credentials, packages, and extensions;
- extension tools and prompt fragments are applied only above Agent Core;
- base AgentSession tools and system prompt are restored on close or failed startup;
- session events are forwarded to extension handlers;
- extension commands receive the current session and shared services;
- package install/update validates extension activation before finalizing the package backup;
- failed package install, update, or removal restores the previous package files and lock, then reloads or preserves the prior extension set;
- package and extension mutation is rejected while the agent is running;
- installed package roots are synchronized into the Session resource loader before reload; the original roots and prompt are restored when the runtime closes;
- resource reload is invoked after package-set changes.

## Deterministic evidence

Focused tests:

```bash
uv run --python 3.11 pytest -q \
  tests/coding_agent/test_auth.py \
  tests/coding_agent/test_model_access.py \
  tests/coding_agent/test_extensions.py \
  tests/coding_agent/test_packages.py \
  tests/coding_agent/test_runtime_extensions.py \
  tests/coding_agent/test_package_cli.py
```

Milestone gate:

```bash
python3.11 scripts/check_phase_21_24.py
```

Full acceptance additionally requires formatting, lint, strict typing, the complete test suite, all earlier milestone gates, a wheel/sdist build, and clean-install command smoke tests.
