import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { scenarios } from "./scenarios.js";

const BASELINE = {
  repository: "earendil-works/pi",
  tag: "v0.84.4",
  commit: "b79e4cc",
};

function parseArgs(argv: string[]): { out: string; scenario?: string } {
  let out = resolve(import.meta.dirname, "../../../fixtures/upstream");
  let scenario: string | undefined;
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--out") {
      const next = argv[index + 1];
      if (!next) throw new Error("--out requires a path");
      out = resolve(process.cwd(), next);
      index += 1;
    } else if (value === "--scenario") {
      const next = argv[index + 1];
      if (!next) throw new Error("--scenario requires a name");
      scenario = next;
      index += 1;
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  return { out, ...(scenario ? { scenario } : {}) };
}

function serialize(records: Record<string, unknown>[]): string {
  return records.map((record) => JSON.stringify(record)).join("\n") + "\n";
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  const names = options.scenario ? [options.scenario] : Object.keys(scenarios).sort();
  await mkdir(options.out, { recursive: true });
  for (const name of names) {
    const scenario = scenarios[name];
    if (!scenario) throw new Error(`Unknown scenario: ${name}`);
    const captured = await scenario();
    const records: Record<string, unknown>[] = [
      {
        record: "meta",
        schemaVersion: 1,
        scenario: name,
        upstream: BASELINE,
        provenance: "upstream-execution",
        capturedBy: "tools/upstream-fixtures@0.0.0",
      },
      ...captured.events.map((event, index) => ({ record: "event", index, event })),
      {
        record: "result",
        ...captured.result,
        ...(captured.observations ? { observations: captured.observations } : {}),
      },
    ];
    const path = resolve(options.out, `${name}.jsonl`);
    await writeFile(path, serialize(records), "utf8");
    process.stdout.write(`${name}: ${captured.events.length} events -> ${path}\n`);
  }
}

await main();
