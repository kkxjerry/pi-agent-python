# RPC protocol — pi v0.84.4

RPC mode exposes the same AgentSession used by interactive, print, JSON, and SDK
modes. It is not a second execution engine.

## Framing

Communication uses stdin/stdout JSONL:

- exactly one JSON object per LF-delimited line;
- stdout is protocol-only;
- logs and diagnostics go to stderr;
- malformed input yields an error response without corrupting later frames;
- EOF performs an orderly shutdown.

Do not use pretty-printed multi-line JSON or terminal rendering on stdout.

## Message classes

```text
command request      client → process, includes request id
command response     process → client, same request id
event notification   process → client, streamed Agent/Session event
protocol error       process → client, structured failure
```

A prompt command can return acceptance before the Agent finishes. Subsequent
events describe model and tool progress. Abort must be accepted while a prompt is
running.

## Command families

The future Python mode will cover:

```text
prompt / continue / steer / follow_up / abort
new_session / resume_session / get_state
get_models / set_model / set_thinking
compact
get_tree / navigate_tree
reload_resources
shutdown
```

## Ordering

- responses are correlated by request ID;
- Agent events preserve session event order;
- command responses and events are distinct envelopes;
- no event is silently converted to human prose;
- a terminal Agent event is emitted before the run is considered finished.

## Errors

Unknown commands, invalid parameters, unavailable models, and illegal state
transitions return structured errors. One bad command does not terminate the
process. Fatal protocol-output failures must stop rather than emit partial JSON.

## Security boundary

RPC inherits the process user's filesystem and subprocess permissions unless a
separate sandbox/environment is configured. The protocol does not imply remote
authentication or isolation by itself.

Implementation is deferred to Phase 20 after AgentSession is stable.
