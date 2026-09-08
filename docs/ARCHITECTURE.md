# Architecture

## Layers

1. **Workflow model** — typed steps, values, dependencies, branches and reusable subflows.
2. **Triggers** — event, file, schedule and external-system event adapters.
3. **Effects/actions** — filesystem, network, process/application and plugin actions behind explicit capabilities.
4. **Execution** — scheduling, concurrency, cancellation, retry, timeout and idempotency.
5. **Durability** — checkpoints, resumability, schema/version metadata and event identity.
6. **Security** — capability grants, opaque secret handles and audit boundaries.
7. **Evidence/tooling** — graph inspection, execution history, provenance, deterministic replay and diagnostics.

## First milestones

1. Typed in-process workflow graph.
2. File/time triggers and basic effects.
3. Retry/idempotency/cancellation semantics.
4. Durable checkpoint/replay model.
5. Plugin/credential boundaries and realistic automation examples.
