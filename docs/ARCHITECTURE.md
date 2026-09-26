# Architecture

`mncs-automation` persistently decides **when declared work becomes
eligible** and hands it to the systems that execute it. It is not a
workflow engine, not a planner, not an action registry, and not a
second Forge.

```
definition (structured JSON, validated)
  -> trigger: schedule (time rule) | watch (cadence + tri-state condition)
  -> native evaluate_tick at now (deterministic, idempotent)
  -> occurrence (stable identity) -> typed target invocation
  -> outcome recorded, state persisted, history bounded
```

## What Automation owns

- Automation definitions (typed, versioned by revision).
- Schedule semantics: one-shot, fixed interval, daily UTC, daily local
  (IANA timezone, explicit DST policy).
- Watch semantics: evaluation cadence, tri-state conditions, edge policy
  (rising / while-true), previous-observation state.
- The single-decision tick: fire one occurrence or wait, with revisit
  time, miss ranges, and reason codes — implemented natively in
  `native/mncs/automation/evaluation.mncs`.
- Occurrence identity (`auto-<8hex>:r<rev>:<seq>`), invocation identity,
  deduplication, misfire policy. No overlap policy: single-node
  evaluate+execute under one state lock cannot overlap by construction.
- Automation-owned persistent state (definitions, evaluation state,
  append-only occurrence log, artifacts) with atomic writes and crash
  recovery (pending invocations resolve to UNKNOWN, never re-fire).
- Typed target descriptors and per-invocation authorization checks.

## What Automation explicitly does not own

- Execution (Forge / `mncs test` / `mncs call` / doctor): Automation emits
  an invocation intent and records the outcome reference. No executor,
  no subprocess pool, no parallel Forge.
- Planning (RAVEL): Automation may run recurring evidence collection for
  an obligation; it never judges obligations.
- Actions/operations (Actions, Control): targets name canonical
  capabilities; Automation invents no action registry. (Note: in the
  current tree `mncs-control` is control theory and `mncs-signal` is DSP
  — there is no event bus or ops catalog yet. Automation targets what
  exists: `mncs test`, `mncs call`, read-only doctor checks.)
- Events (no canonical bus exists): watches reconcile against canonical
  state on cadence; transient delivery is not required for correctness.
- Persistence machinery (Store): Store's Python surface is a
  content-addressed artifact transport, not a small-state KV — a poor fit
  for per-tick state. Automation keeps atomic JSON state files and
  records this as pressure DOC?/AUTO-S1 rather than forking Store.
- Telemetry (System Monitor): monitor observations may feed conditions;
  Automation collects no telemetry itself.
- Test verdicts (Test), diagnoses (Debug), conformance (Doctor),
  provenance graphs (Lineage): referenced, never duplicated. Occurrence
  records carry execution identities others can follow.

## Native vs host boundary

Native (`native/mncs/automation/`): epoch-millis arithmetic, next
occurrence, sequence mapping, tri-state edges, misfire selection, the
tick decision. Pure, total, clock-free — time arrives as arguments.

Host (`tools/automation/`): wall-clock reads, timezone database,
JSON persistence with fsync, wakeup loop, condition observation
(test runner, file digests), typed target execution, CLI. The host
tells Automation that time advanced; MNCS decides what it means.

## Time

Instants are i64 epoch millis; durations are i64 millis. Date math
(day index, minute-of-day) is UTC and native. Named-timezone recurrence
is host-resolved to UTC instants + local-day serials under a documented
DST policy (gap: shift forward; overlap: first). Occurrence identity
embeds the UTC instant and serial plus the IANA name in the definition,
so machine moves cannot silently reschedule.

## Guarantees (honest)

- At-most-once invocation per occurrence identity, including across
  crashes (pending resolves to UNKNOWN; dedup by identity on replay).
- No unbounded catch-up: one firing per tick per automation, miss ranges
  recorded, catch-up bound enforced natively.
- First evaluation aligns, never backfills: a fresh definition, a fresh
  state row, or a revision change maps `now` to the current position and
  waits, so creating a definition cannot replay history. Blocked time
  accrues no position for the same reason.
- A skipped gap leaves exactly one missed-range marker, never one line
  per sequence number. Pins (one-shot, daily-local) are singleton seq
  labels, not contiguous counts: a newer pin fires as-is with no range.
- UNKNOWN conditions never fire and never count as FALSE.
- Single-node topology only; multi-node would need leader election
  (explicit future pressure, not half-implemented).
