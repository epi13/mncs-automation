# RFC 0001: Automation foundation

Status: Draft

## Principles

- A workflow is an inspectable typed graph, not merely a sequence of opaque callbacks.
- External actions are explicit effects gated by capabilities.
- Credentials are opaque references; workflows do not need raw secrets.
- Retry policy is coupled to explicit idempotency/side-effect semantics.
- Trigger events have durable identity so duplicates and replay are defined behavior.
- Execution can checkpoint and resume without pretending partially completed effects never happened.
- Time/schedules and cancellation have explicit semantics.

## Pressure objectives

Effect/capability modeling, ergonomic workflow/DSL syntax, closures, async/await, structured concurrency, serialization, durable state, schema evolution, pattern matching, retry/error types, clocks/schedules, plugin interfaces, opaque secret handles and graph introspection/reflection.
