# RFC 0001: Automation foundation

Status: Implemented in substance; historical context retained.

> This RFC predates the canonical implementation. It correctly
> identified the problem (inspectable triggers, explicit effects,
> durable identity, idempotent retry) but assumed Automation would own
> workflow execution itself. The implemented architecture assigns
> execution to Forge/Test/Doctor and keeps Automation to deciding
> **when** work becomes eligible. `docs/ARCHITECTURE.md` is
> authoritative; this document is design evidence, not a build order.
>
> Retained: typed inspectable triggers, capability-gated effects,
> durable trigger identity, idempotent resume, explicit time semantics.
> Redesigned: execution/checkpointing (Forge owns it), plugin actions
> (typed `mncs-call` targets instead), credential handles (out of scope
> for v1 — no secret-bearing targets exist). Discarded: an
> Automation-private workflow engine and scheduler database.
>
> Implemented refinements (2026-09-26): first evaluation aligns instead
> of backfilling (creating a definition never replays history); blocked
> and paused time accrues no position; a skipped gap leaves one
> missed-range marker; pins are singleton seq labels; no overlap policy
> (structurally impossible single-node).

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
