# Security and execution boundaries

The default Python coding agent is **not an operating-system sandbox**. It runs
with the filesystem, process, network, and credential permissions of the user
that launched `pi-py`.

This is an explicit compatibility boundary rather than a hidden promise. A tool
approval prompt can reduce accidental operations, but it cannot prevent a
trusted in-process extension or a compromised dependency from using normal
Python APIs.

## Approval gate

`ApprovalPolicy` classifies each tool call before execution:

```text
read / grep / find / ls         low risk; allowed by default
write / edit / patch            filesystem mutation; prompt by default
bash / shell / exec             process execution; prompt by default
unknown tool                    prompt by default
destructive command pattern     denied before prompting
path outside allowed roots      denied before prompting
```

The decision options are:

```text
allow_once
allow_session
deny
```

Session approvals are keyed by a SHA-256 fingerprint of the exact tool name and
arguments. They are not broad glob rules and are discarded with the
`ApprovalGate` instance.

When a policy requires approval but no prompt implementation is available, the
gate fails closed. Interactive mode exposes a TUI approval dialog; headless
modes should either use `--approval deny`, provide an SDK callback, or disable
the gate explicitly.

Approval audit records contain the decision, risk, reasons, IDs, and a
fingerprint. Raw tool arguments are deliberately omitted so command bodies,
file contents, and secrets do not leak into the audit log.

## Extensions

Python extensions execute in process. `ExtensionPolicy` checks allowed source
roots and declared capabilities before import, and extension activation is
transactional, but those checks are not a process sandbox.

Treat a Python extension exactly like an installed Python dependency:

- review its source and package provenance;
- trust project extensions explicitly;
- grant only the capabilities the extension declares and needs;
- use a container or another OS boundary for untrusted code.

## Credentials

`AuthStorage` uses atomic replacement and restrictive POSIX permissions.
Telemetry redacts sensitive keys and registered secret values before export.
Applications should still avoid placing secrets in prompts, command lines, file
paths, or package manifests.

Environment variables are inherited by the default process. The optional
container command builder forwards only variables named in
`environment_allowlist`.

## Optional container boundary

`ContainerSandboxSpec` and `build_container_command()` create a shell-free
Docker or Podman argument vector. The helper defaults to:

```text
--read-only
--network none
workspace bind mount
/tmp tmpfs
--rm
```

Example:

```python
from pathlib import Path

from pi_agent.coding_agent import ContainerSandboxSpec, build_container_command

spec = ContainerSandboxSpec(
    image="pi-agent-python:local",
    workspace=Path.cwd(),
    network="none",
    environment_allowlist=("OPENAI_API_KEY",),
)
command = build_container_command(spec, ("--mode", "interactive"))
```

The project does not automatically execute this command, pull an image, or
claim the host container runtime is configured securely. Isolation strength
still depends on Docker/Podman, host mounts, Linux capabilities, seccomp,
user namespaces, and the selected image.

## Recommended operating modes

For trusted local development:

```bash
pi-py --approval prompt
```

For unattended automation where mutations must be blocked:

```bash
pi-py --mode json --approval deny -p "Inspect the repository"
```

For untrusted repositories or extensions, run inside a separately configured
container/VM with minimal mounts and credentials. Do not rely on the TUI prompt
as the sole security boundary.
