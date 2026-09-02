# Changelog

## 0.2.0.dev0

### Phase 0–3 acceptance

- Rebuilt the local repository on `develop` and pinned official pi `v0.84.4` / `b79e4cc`.
- Added detailed source/behavior maps, 30 registered scenarios, and reproducible gates.
- Executed ten scenarios against exact official `0.84.4` npm packages.
- Verified every executed result/event stream exactly matched the earlier source contracts.

### Phase 4–7 implementation

- Added provider-neutral messages, content, models, cost tiers, usage, diagnostics, and options.
- Added generic and assistant async event streams with safe completion semantics.
- Added deterministic FauxProvider and OpenAI-compatible SSE provider.
- Added partial Tool Call JSON, provider/model registries, retry policy, idle timeout, callbacks,
  cache/sampling fields, header suppression, and structured errors.
- Added prompt/continue Agent Loop, context conversion, tool validation/execution/progress,
  error ToolResults, truncated-call protection, cancellation, and termination behavior.
- Added direct Python parity checks against seven executed official TypeScript fixtures.
