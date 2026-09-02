# Official TypeScript fixture runner

This isolated runner imports exactly `0.84.4` of the official AI and Agent Core npm packages. It must be run before changing fixture provenance to `upstream-execution`.

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm run typecheck
npm run capture -- --out ../../fixtures/upstream
```

The checked-in files remain `source-contract` until that command succeeds.
