import { resolve } from "node:path";
import {
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";

function required(name: string): string {
  const index = process.argv.indexOf(name);
  const value = index >= 0 ? process.argv[index + 1] : undefined;
  if (!value) throw new Error(`${name} requires a value`);
  return resolve(value);
}

function optional(name: string, fallback: string): string {
  const index = process.argv.indexOf(name);
  if (index < 0) return fallback;
  const value = process.argv[index + 1];
  return value || fallback;
}

const cwd = required("--cwd");
const agentDir = required("--agent-dir");
const provider = optional("--provider", "dashscope");
const modelId = optional("--model", "qwen-plus");

const modelRuntime = await ModelRuntime.create({
  authPath: resolve(agentDir, "auth.json"),
  modelsPath: resolve(agentDir, "models.json"),
  allowModelNetwork: false,
  refreshOnCreate: false,
});
const model = modelRuntime.getModel(provider, modelId);
if (!model) throw new Error(`model not found: ${provider}/${modelId}`);
const settingsManager = SettingsManager.create(cwd, agentDir);
const resourceLoader = new DefaultResourceLoader({
  cwd,
  agentDir,
  settingsManager,
  noExtensions: true,
  noSkills: true,
  noPromptTemplates: true,
  noThemes: true,
  noContextFiles: true,
});
await resourceLoader.reload();
const { session } = await createAgentSession({
  cwd,
  agentDir,
  modelRuntime,
  model,
  tools: ["read", "bash", "edit", "write"],
  resourceLoader,
  settingsManager,
  sessionManager: SessionManager.inMemory(cwd),
});
try {
  const tools = session.getAllTools().filter((tool) =>
    session.getActiveToolNames().includes(tool.name),
  );
  process.stdout.write(
    JSON.stringify(
      {
        system: "upstream-ts",
        cwd,
        systemPrompt: session.systemPrompt,
        systemPromptCharacters: session.systemPrompt.length,
        systemPromptLines: session.systemPrompt.split("\n").length,
        activeToolNames: session.getActiveToolNames(),
        tools: tools.map((tool) => ({
          name: tool.name,
          description: tool.description,
          parameters: tool.parameters,
          promptGuidelines: tool.promptGuidelines,
        })),
      },
      null,
      2,
    ) + "\n",
  );
} finally {
  session.dispose();
}
