# Phase 21–24 implementation evidence

Baseline: `earendil-works/pi@v0.84.4` (`b79e4cc`).

## Canonical checkpoint

`phase-21-24` points to commit `67ff31a`. During Phase 25–31 development, a second uncommitted product API was introduced alongside that checkpoint. Final cleanup retained the published Phase 21–24 architecture and removed the competing `AuthStore`/`ExtensionLoader`/`PackageSource`/multi-session-service draft rather than pretending both were supported.

## Phase 21 — auth and model access

Evidence:

- `src/pi_agent/coding_agent/auth.py`
- `src/pi_agent/coding_agent/model_access.py`
- `tests/coding_agent/test_auth.py`
- `tests/coding_agent/test_model_access.py`

Covered invariants:

- `AuthStorage` uses atomic replacement and private POSIX permissions;
- secret values are redacted from credential representations;
- `CredentialResolver` applies explicit, stored, and environment precedence;
- expired credentials can invoke an injected refresh callback;
- concurrent refresh is serialized;
- `ModelAccess` resolves provider-scoped models, rejects ambiguous IDs, and injects credentials into request options;
- dynamic provider model refresh uses the existing `ProviderRegistry`.

## Phase 22 — Python extensions

Evidence:

- `src/pi_agent/coding_agent/extensions/types.py`
- `src/pi_agent/coding_agent/extensions/host.py`
- `tests/coding_agent/test_extensions.py`
- `tests/coding_agent/test_runtime_extensions.py`

Covered invariants:

- descriptor roots and requested capabilities are checked before activation;
- activation writes Tools, commands, services, prompt fragments, event handlers, and disposers into an isolated staged contribution;
- duplicate Tool/command/service names fail before publication;
- failed activation disposes staged resources;
- reload constructs a candidate before replacing the previous live handle;
- event-handler failures are recorded with extension identity;
- disposal runs in reverse registration order.

## Phase 23 — local packages

Evidence:

- `src/pi_agent/coding_agent/packages/types.py`
- `src/pi_agent/coding_agent/packages/manager.py`
- `src/pi_agent/coding_agent/package_cli.py`
- `tests/coding_agent/test_packages.py`
- `tests/coding_agent/test_package_cli.py`
- `tests/coding_agent/test_runtime_extensions.py`

Covered invariants:

- package manifests and resource paths are validated below the package root;
- symlinks and traversal are rejected;
- local-directory install/update/remove use staging, backups, and atomic lock replacement;
- SHA-256 integrity covers relative paths and file bytes;
- dependency checks prevent invalid removal;
- staged receipts support finalize or rollback;
- `pi-pkg` exposes local install/update/remove/list/verify and JSON output.

Hosted package registries, remote source resolution, and automatic dependency installation are not part of this phase.

## Phase 24 — AgentSession runtime attachment

Evidence:

- `src/pi_agent/coding_agent/runtime.py`
- `tests/coding_agent/test_runtime.py`
- `tests/coding_agent/test_runtime_extensions.py`

Covered invariants:

- `CodingAgentRuntime` attaches credentials, local packages, and one `ExtensionHost` above an existing `AgentSession`;
- startup validates a complete candidate before changing live Tools or the system prompt;
- Tool name collisions do not mutate the session;
- package mutation finalizes only after extension and resource reconciliation succeeds;
- install/update/remove rollback restores package files, lock state, resource roots, and previous extension contributions;
- closing the runtime waits for idle, disposes extensions, and restores the original Tool list and system prompt.

This is intentionally a per-session runtime attachment. A separate shared-services/multi-session product API is not claimed.

## Gate

```bash
uv run --python 3.11 python scripts/check_phase_21_24.py
```

The phase remains `SOURCE` in `PARITY.md`: no Phase 21–24 product record is labelled `upstream-execution` because the pinned TypeScript package was not executed to generate exact product-level golden streams for these scenarios.
