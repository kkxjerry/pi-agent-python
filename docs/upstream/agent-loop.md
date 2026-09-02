# Agent Loop behavior — pi v0.84.4

## Public entry points

The low-level layer has two distinct operations:

```text
agentLoop(newPromptMessages, existingContext, config, ...)
agentLoopContinue(existingContext, config, ...)
```

Prompt mode treats supplied prompts as new output from this invocation. Continue
mode adds no user message and requires an existing context whose tail is not an
assistant message.

## Inputs and ownership

The loop receives:

- model and provider stream function;
- system prompt;
- application transcript (`AgentMessage[]`);
- tool definitions and executors;
- context transform and model-message converter;
- stream options, API-key resolver, and cancellation signal.

Prompt mode builds a runtime message list without mutating the caller's durable
history. Continue mode operates on the supplied active transcript because it is
resuming that exact branch.

## Provider boundary

Before **every** provider request:

```text
copy active AgentMessage transcript
→ transform_context(messages, cancellation)
→ convert_to_llm(messages)
→ attach system prompt and tool schemas
→ resolve current API key
→ call provider stream
```

Custom application messages may remain in the transcript but must be removed or
converted at `convert_to_llm`.

## Assistant accumulation

- provider `start` inserts a pending assistant message;
- deltas replace that transcript slot with the newest partial;
- terminal `done/error` replaces it with the final message;
- Agent emits message start/update/end around this process.

A provider stream that exits without a terminal event is an explicit error, not
an implicit successful response.

## Tool handling

For every final Tool Call:

1. emit `tool_execution_start`;
2. resolve tool by name;
3. optionally prepare/normalize arguments;
4. validate arguments against the declared schema;
5. execute with cancellation and progress callback;
6. convert success or failure to a ToolResult;
7. emit ToolResult message events;
8. append ordered ToolResults to the runtime transcript.

Unknown tools, schema failures, and exceptions all become error ToolResults. They
do not crash the loop because the next provider turn can repair the call.

### Truncated calls

If the assistant stop reason is `length`, Tool Calls are never executed. The loop
creates an error ToolResult explaining that arguments may be truncated and asks
the model to re-issue a complete call.

### Termination

A batch requests termination only when it contains results and **all** results
set the termination flag. One terminating result must not suppress remaining
results or the next provider turn.

### Parallel boundary

Phase 7 intentionally executes serially. Phase 8 will preserve upstream parallel
semantics:

- start events in call order;
- completion events in actual completion order;
- transcript results in original call order;
- a sequential tool forces the batch to use the sequential path.

## Cancellation and failures

Cancellation is checked before provider calls, before/after tools, during retry
sleep, and between streaming lines. Even a pre-cancelled run produces a complete
Agent lifecycle and returns the prompt plus an aborted assistant result.

Retry belongs to the provider adapter. A request may retry only before any
assistant stream event becomes visible. Retrying after `start` could duplicate
text, tools, billing, or side effects, so it is forbidden.

## Return value

`AgentEventStream.result()` contains only messages produced by this invocation:
new prompts, assistant messages, and ToolResults. Existing history used by
continue/context transformation is not duplicated in the returned list.
