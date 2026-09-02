import {
  agentLoop,
  agentLoopContinue,
  type AgentContext,
  type AgentEvent,
  type AgentLoopConfig,
  type AgentTool,
  type StreamFn,
} from "@earendil-works/pi-agent-core";
import { Type } from "typebox";
import {
  assistant,
  createModel,
  identityConverter,
  projectEvent,
  projectMessages,
  scriptedStream,
  user,
} from "./helpers.js";

export interface ScenarioResult {
  events: Record<string, unknown>[];
  result: Record<string, unknown>;
  observations?: Record<string, unknown>;
}

export type Scenario = () => Promise<ScenarioResult>;

async function collect(
  stream: ReturnType<typeof agentLoop> | ReturnType<typeof agentLoopContinue>,
  observations?: Record<string, unknown>,
): Promise<ScenarioResult> {
  const events: AgentEvent[] = [];
  for await (const event of stream) events.push(event);
  const messages = await stream.result();
  return {
    events: events.map(projectEvent),
    result: projectMessages(messages),
    ...(observations ? { observations } : {}),
  };
}

function baseConfig(): AgentLoopConfig {
  return { model: createModel(), convertToLlm: identityConverter };
}

const echoSchema = Type.Object({ value: Type.String() });

function echoTool(options: { delayFirst?: boolean; onExecute?: (value: string) => void } = {}): AgentTool<typeof echoSchema, { value: string }> {
  return {
    name: "echo",
    label: "Echo",
    description: "Echo a deterministic value",
    parameters: echoSchema,
    async execute(_toolCallId, params) {
      options.onExecute?.(params.value);
      if (options.delayFirst && params.value === "first") {
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
      return {
        content: [{ type: "text", text: `echoed:${params.value}` }],
        details: { value: params.value },
      };
    },
  };
}

export const scenarios: Record<string, Scenario> = {
  async text_only() {
    const context: AgentContext = { systemPrompt: "fixture", messages: [], tools: [] };
    return collect(agentLoop([user("hello")], context, baseConfig(), undefined, scriptedStream([
      assistant([{ type: "text", text: "hello back" }]),
    ])));
  },

  async thinking_stream() {
    const context: AgentContext = { systemPrompt: "fixture", messages: [], tools: [] };
    return collect(
      agentLoop(
        [user("think")],
        context,
        baseConfig(),
        undefined,
        scriptedStream([
          assistant([
            { type: "thinking", thinking: "inspect" },
            { type: "text", text: "answer" },
          ]),
        ]),
      ),
    );
  },

  async single_tool() {
    const context: AgentContext = { systemPrompt: "fixture", messages: [], tools: [echoTool()] };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "one" } }], "toolUse"),
      assistant([{ type: "text", text: "done" }]),
    ]);
    return collect(agentLoop([user("echo one")], context, baseConfig(), undefined, stream));
  },

  async sequential_tool() {
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [echoTool({ delayFirst: true })],
    };
    const config: AgentLoopConfig = { ...baseConfig(), toolExecution: "sequential" };
    const stream = scriptedStream([
      assistant([
        { type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "first" } },
        { type: "toolCall", id: "tool-2", name: "echo", arguments: { value: "second" } },
      ], "toolUse"),
      assistant([{ type: "text", text: "done" }]),
    ]);
    return collect(agentLoop([user("echo sequentially")], context, config, undefined, stream));
  },

  async per_tool_sequential_override() {
    const sequentialTool: AgentTool<typeof echoSchema, { value: string }> = {
      ...echoTool({ delayFirst: true }),
      executionMode: "sequential",
    };
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [sequentialTool],
    };
    const stream = scriptedStream([
      assistant([
        { type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "first" } },
        { type: "toolCall", id: "tool-2", name: "echo", arguments: { value: "second" } },
      ], "toolUse"),
      assistant([{ type: "text", text: "done" }]),
    ]);
    return collect(
      agentLoop(
        [user("echo with override")],
        context,
        { ...baseConfig(), toolExecution: "parallel" },
        undefined,
        stream,
      ),
    );
  },

  async parallel_out_of_order() {
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [echoTool({ delayFirst: true })],
    };
    const config: AgentLoopConfig = { ...baseConfig(), toolExecution: "parallel" };
    const stream = scriptedStream([
      assistant([
        { type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "first" } },
        { type: "toolCall", id: "tool-2", name: "echo", arguments: { value: "second" } },
      ], "toolUse"),
      assistant([{ type: "text", text: "done" }]),
    ]);
    const captured = await collect(
      agentLoop([user("echo both")], context, config, undefined, stream),
    );
    const completionOrder = captured.events
      .filter((event) => event.type === "tool_execution_end")
      .map((event) => event.toolCallId as string);
    const transcriptOrder = captured.events
      .filter((event) => event.type === "message_end" && event.role === "toolResult")
      .map((event) => event.toolCallId as string);
    return {
      ...captured,
      observations: { completionOrder, transcriptOrder },
    };
  },

  async invalid_tool_name() {
    const context: AgentContext = { systemPrompt: "fixture", messages: [], tools: [] };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "missing", arguments: {} }], "toolUse"),
      assistant([{ type: "text", text: "recovered" }]),
    ]);
    return collect(agentLoop([user("call missing")], context, baseConfig(), undefined, stream));
  },

  async invalid_tool_arguments() {
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [echoTool()],
    };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "echo", arguments: {} }], "toolUse"),
      assistant([{ type: "text", text: "recovered" }]),
    ]);
    return collect(agentLoop([user("invalid args")], context, baseConfig(), undefined, stream));
  },

  async tool_progress_update() {
    const progressTool: AgentTool<typeof echoSchema, Record<string, unknown>> = {
      ...echoTool(),
      async execute(_toolCallId, params, _signal, onUpdate) {
        onUpdate?.({
          content: [{ type: "text", text: "half" }],
          details: { progress: 0.5 },
        });
        return {
          content: [{ type: "text", text: `echoed:${params.value}` }],
          details: { value: params.value },
        };
      },
    };
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [progressTool],
    };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "one" } }], "toolUse"),
      assistant([{ type: "text", text: "done" }]),
    ]);
    return collect(agentLoop([user("progress")], context, baseConfig(), undefined, stream));
  },

  async before_tool_call_block() {
    let executed = false;
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [echoTool({ onExecute: () => { executed = true; } })],
    };
    const config: AgentLoopConfig = {
      ...baseConfig(),
      beforeToolCall: async () => ({ block: true, reason: "policy blocked" }),
    };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "one" } }], "toolUse"),
      assistant([{ type: "text", text: "recovered" }]),
    ]);
    return collect(
      agentLoop([user("blocked")], context, config, undefined, stream),
      { executed },
    );
  },

  async after_tool_call_override() {
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [echoTool()],
    };
    const config: AgentLoopConfig = {
      ...baseConfig(),
      afterToolCall: async () => ({
        content: [{ type: "text", text: "overridden" }],
        details: { replacement: true },
        isError: true,
      }),
    };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "one" } }], "toolUse"),
      assistant([{ type: "text", text: "done" }]),
    ]);
    return collect(agentLoop([user("override")], context, config, undefined, stream));
  },

  async terminate_all_tool_results() {
    const terminating: AgentTool<typeof echoSchema, { value: string }> = {
      ...echoTool(),
      async execute(_toolCallId, params) {
        return {
          content: [{ type: "text", text: `stop:${params.value}` }],
          details: { value: params.value },
          terminate: true,
        };
      },
    };
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [terminating],
    };
    const stream = scriptedStream([
      assistant([
        { type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "one" } },
        { type: "toolCall", id: "tool-2", name: "echo", arguments: { value: "two" } },
      ], "toolUse"),
    ]);
    return collect(agentLoop([user("terminate")], context, baseConfig(), undefined, stream));
  },

  async tool_throws_exception() {
    const failing: AgentTool<typeof echoSchema, Record<string, never>> = {
      name: "echo",
      label: "Echo",
      description: "Always fails",
      parameters: echoSchema,
      async execute() {
        throw new Error("fixture failure");
      },
    };
    const context: AgentContext = { systemPrompt: "fixture", messages: [], tools: [failing] };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "x" } }], "toolUse"),
      assistant([{ type: "text", text: "recovered" }]),
    ]);
    return collect(agentLoop([user("fail")], context, baseConfig(), undefined, stream));
  },

  async truncated_tool_call() {
    const executed: string[] = [];
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [echoTool({ onExecute: (value) => executed.push(value) })],
    };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "hel" } }], "length"),
      assistant([{ type: "text", text: "reissued later" }]),
    ]);
    return collect(
      agentLoop([user("truncated")], context, baseConfig(), undefined, stream),
      { executedValues: executed },
    );
  },

  async provider_error() {
    const context: AgentContext = { systemPrompt: "fixture", messages: [], tools: [] };
    const failure = {
      ...assistant([], "error"),
      errorMessage: "provider down",
    };
    return collect(
      agentLoop([user("fail")], context, baseConfig(), undefined, scriptedStream([failure])),
    );
  },

  async provider_abort() {
    const context: AgentContext = { systemPrompt: "fixture", messages: [], tools: [] };
    const failure = {
      ...assistant([], "aborted"),
      errorMessage: "provider aborted",
    };
    return collect(
      agentLoop([user("abort")], context, baseConfig(), undefined, scriptedStream([failure])),
    );
  },

  async transform_context() {
    const observed: string[][] = [];
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [user("old-1"), assistant([{ type: "text", text: "old-2" }])],
      tools: [],
    };
    const config: AgentLoopConfig = {
      ...baseConfig(),
      transformContext: async (messages) => messages.slice(-1),
    };
    const delegate = scriptedStream([assistant([{ type: "text", text: "done" }])]);
    const streamFn: StreamFn = (model, llmContext, options) => {
      observed.push(llmContext.messages.map((message) => message.role));
      return delegate(model, llmContext, options);
    };
    return collect(agentLoop([user("new")], context, config, undefined, streamFn), {
      providerMessageRoles: observed,
    });
  },

  async steering_one() {
    let executed = false;
    let delivered = false;
    const context: AgentContext = {
      systemPrompt: "fixture",
      messages: [],
      tools: [echoTool({ onExecute: () => { executed = true; } })],
    };
    const config: AgentLoopConfig = {
      ...baseConfig(),
      getSteeringMessages: async () => {
        if (executed && !delivered) {
          delivered = true;
          return [user("steer-now")];
        }
        return [];
      },
    };
    const stream = scriptedStream([
      assistant([{ type: "toolCall", id: "tool-1", name: "echo", arguments: { value: "one" } }], "toolUse"),
      assistant([{ type: "text", text: "steered" }]),
    ]);
    return collect(agentLoop([user("start")], context, config, undefined, stream));
  },

  async follow_up_one() {
    let delivered = false;
    const context: AgentContext = { systemPrompt: "fixture", messages: [], tools: [] };
    const config: AgentLoopConfig = {
      ...baseConfig(),
      getFollowUpMessages: async () => {
        if (delivered) return [];
        delivered = true;
        return [user("follow-up")];
      },
    };
    const stream = scriptedStream([
      assistant([{ type: "text", text: "initial" }]),
      assistant([{ type: "text", text: "followed" }]),
    ]);
    return collect(agentLoop([user("start")], context, config, undefined, stream));
  },

  async continue_existing_context() {
    const context: AgentContext = { systemPrompt: "fixture", messages: [user("existing")], tools: [] };
    const stream = scriptedStream([assistant([{ type: "text", text: "continued" }])]);
    return collect(agentLoopContinue(context, baseConfig(), undefined, stream));
  },
};
