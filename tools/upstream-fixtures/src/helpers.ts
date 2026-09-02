import {
  EventStream,
  type AssistantMessage,
  type AssistantMessageEvent,
  type Message,
  type Model,
  type TextContent,
  type ThinkingContent,
  type ToolCall,
  type UserMessage,
} from "@earendil-works/pi-ai";
import type { AgentEvent, AgentMessage, StreamFn } from "@earendil-works/pi-agent-core";

export class MockAssistantStream extends EventStream<AssistantMessageEvent, AssistantMessage> {
  constructor() {
    super(
      (event) => event.type === "done" || event.type === "error",
      (event) => {
        if (event.type === "done") return event.message;
        if (event.type === "error") return event.error;
        throw new Error("unexpected non-terminal event");
      },
    );
  }
}

export function createUsage() {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

export function createModel(): Model<"anthropic-messages"> {
  return {
    id: "fixture-model",
    name: "Fixture Model",
    api: "anthropic-messages",
    provider: "fixture",
    baseUrl: "http://localhost:0",
    reasoning: false,
    input: ["text", "image"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 8192,
    maxTokens: 2048,
  };
}

export function user(text: string): UserMessage {
  return { role: "user", content: text, timestamp: 0 };
}

export function assistant(
  content: AssistantMessage["content"],
  stopReason: AssistantMessage["stopReason"] = "stop",
): AssistantMessage {
  return {
    role: "assistant",
    content,
    api: "anthropic-messages",
    provider: "fixture",
    model: "fixture-model",
    usage: createUsage(),
    stopReason,
    timestamp: 0,
  };
}

export function identityConverter(messages: AgentMessage[]): Message[] {
  return messages.filter(
    (message) => message.role === "user" || message.role === "assistant" || message.role === "toolResult",
  ) as Message[];
}

export function scriptedStream(messages: AssistantMessage[], delaysMs: number[] = []): StreamFn {
  let callIndex = 0;
  return () => {
    const stream = new MockAssistantStream();
    const message = messages[callIndex];
    const delay = delaysMs[callIndex] ?? 0;
    callIndex += 1;
    if (!message) throw new Error(`No scripted assistant message for call ${callIndex}`);
    const emit = () => emitMessage(stream, message);
    if (delay > 0) setTimeout(emit, delay);
    else queueMicrotask(emit);
    return stream;
  };
}

function makeDeltaEvent(
  type: "text_delta" | "thinking_delta" | "toolcall_delta",
  contentIndex: number,
  delta: string,
  partial: AssistantMessage,
): AssistantMessageEvent {
  return { type, contentIndex, delta, partial: { ...partial } };
}

export function emitMessage(stream: MockAssistantStream, message: AssistantMessage): void {
  const partial: AssistantMessage = { ...message, content: [], stopReason: "pending" };
  stream.push({ type: "start", partial: { ...partial } });

  message.content.forEach((block, contentIndex) => {
    if (block.type === "text") {
      partial.content = [...partial.content, { type: "text", text: "" }];
      stream.push({ type: "text_start", contentIndex, partial: { ...partial } });
      (partial.content[contentIndex] as TextContent).text = block.text;
      stream.push(makeDeltaEvent("text_delta", contentIndex, block.text, partial));
      stream.push({
        type: "text_end",
        contentIndex,
        content: block.text,
        partial: { ...partial },
      });
    } else if (block.type === "thinking") {
      partial.content = [...partial.content, { type: "thinking", thinking: "" }];
      stream.push({ type: "thinking_start", contentIndex, partial: { ...partial } });
      (partial.content[contentIndex] as ThinkingContent).thinking = block.thinking;
      stream.push(makeDeltaEvent("thinking_delta", contentIndex, block.thinking, partial));
      stream.push({
        type: "thinking_end",
        contentIndex,
        content: block.thinking,
        partial: { ...partial },
      });
    } else if (block.type === "toolCall") {
      partial.content = [
        ...partial.content,
        { type: "toolCall", id: block.id, name: block.name, arguments: {} },
      ];
      stream.push({ type: "toolcall_start", contentIndex, partial: { ...partial } });
      stream.push(
        makeDeltaEvent("toolcall_delta", contentIndex, JSON.stringify(block.arguments), partial),
      );
      (partial.content[contentIndex] as ToolCall).arguments = block.arguments;
      stream.push({
        type: "toolcall_end",
        contentIndex,
        toolCall: block,
        partial: { ...partial },
      });
    }
  });

  if (message.stopReason === "pending") {
    const error: AssistantMessage = {
      ...message,
      stopReason: "error",
      errorMessage: "Fixture response ended without a stop reason",
    };
    stream.push({ type: "error", reason: "error", error });
    return;
  }
  if (message.stopReason === "error" || message.stopReason === "aborted") {
    stream.push({ type: "error", reason: message.stopReason, error: message });
    return;
  }
  stream.push({ type: "done", reason: message.stopReason, message });
}

function messageText(message: AgentMessage): string | undefined {
  if (message.role === "user") {
    if (typeof message.content === "string") return message.content;
    return message.content
      .filter((item) => item.type === "text")
      .map((item) => item.text)
      .join("");
  }
  if (message.role === "assistant") {
    return message.content
      .filter((item) => item.type === "text")
      .map((item) => item.text)
      .join("");
  }
  if (message.role === "toolResult") {
    return message.content
      .filter((item) => item.type === "text")
      .map((item) => item.text)
      .join("");
  }
  return undefined;
}

export function projectEvent(event: AgentEvent): Record<string, unknown> {
  switch (event.type) {
    case "message_start":
    case "message_end": {
      const projected: Record<string, unknown> = { type: event.type, role: event.message.role };
      const text = messageText(event.message);
      if (
        text !== undefined &&
        (text.length > 0 || event.message.role !== "assistant" || event.message.stopReason !== "pending")
      ) {
        projected.text = text;
      }
      if (event.message.role === "assistant") {
        projected.stopReason = event.message.stopReason;
        projected.toolCallIds = event.message.content
          .filter((item) => item.type === "toolCall")
          .map((item) => item.id);
      }
      if (event.message.role === "toolResult") {
        projected.toolCallId = event.message.toolCallId;
        projected.toolName = event.message.toolName;
        projected.isError = event.message.isError;
      }
      return projected;
    }
    case "message_update":
      return {
        type: event.type,
        assistantEventType: event.assistantMessageEvent.type,
      };
    case "tool_execution_start":
      return {
        type: event.type,
        toolCallId: event.toolCallId,
        toolName: event.toolName,
      };
    case "tool_execution_update":
      return {
        type: event.type,
        toolCallId: event.toolCallId,
        toolName: event.toolName,
      };
    case "tool_execution_end":
      return {
        type: event.type,
        toolCallId: event.toolCallId,
        toolName: event.toolName,
        isError: event.isError,
      };
    case "turn_end":
      return {
        type: event.type,
        assistantStopReason:
          event.message.role === "assistant" ? event.message.stopReason : null,
        toolResultIds: event.toolResults.map((result) => result.toolCallId),
      };
    case "agent_end":
      return {
        type: event.type,
        messageRoles: event.messages.map((message) => message.role),
        toolResultIds: event.messages
          .filter((message) => message.role === "toolResult")
          .map((message) => message.toolCallId),
      };
    default:
      return { type: event.type };
  }
}

export function projectMessages(messages: AgentMessage[]): Record<string, unknown> {
  return {
    messageRoles: messages.map((message) => message.role),
    toolResultIds: messages
      .filter((message) => message.role === "toolResult")
      .map((message) => message.toolCallId),
  };
}
