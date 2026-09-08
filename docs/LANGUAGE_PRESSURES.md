# MNCS language pressure ledger

Record workload, observed behavior, required semantic, reproducer, owner, workaround and closure verification.

## Initial pressure targets

- typed effect/capability declarations
- concise workflow/graph syntax
- closures and values crossing durable step boundaries
- serializable workflow state and type/schema evolution
- async structured concurrency and cancellation
- typed retries/timeouts/idempotency metadata
- schedule/time primitives
- event identity, deduplication and replay
- plugin/adapter interfaces
- opaque credential/secret handle types
- reflection/introspection for graph visualization and evidence
- diagnostics that identify the exact workflow step and effect boundary

A workaround that makes an automation run is not enough to close a pressure item if its effects cannot be inspected or safely replayed.
