# Pressure ledger (automation workload)

Genuine gaps found while building the first canonical implementation.
`mncs-language` and `mncs-compiler` are read-only; active-campaign
repositories were inspected, never modified.

## AUTO-P1: no generic MNCS time/calendar subsystem (non-blocking)

Automation needed epoch arithmetic (built natively in
`mncs.automation.time`), recurrence mapping (`schedule`), and IANA
timezones with DST policy (host `tzbridge`, glibc-adjacent data via
Python `zoneinfo`). Nothing in the tree owns reusable time semantics:
`mncs.std.clock` offers only relational u64 comparisons plus a
`clock_read()` intrinsic. If scheduling, calendars, or deadlines spread
to other subsystems, a shared time owner should absorb the native time
math rather than leaving Automation its permanent home. Today the
automation-local modules are the de-facto reference; they are total,
tested, and importable by path.

## AUTO-P2: no canonical event bus (non-blocking)

`mncs-signal` is digital signal processing, not event delivery. Watches
therefore poll on cadence and reconcile against canonical state — the
correct fallback, and the documented pattern (state first, notification
as optimization). If a family event bus ever appears, watch triggers
gain an event-driven fast path with the same persisted edge state.

## AUTO-P3: no Action/Control operation catalog (non-blocking)

`mncs-control` is control theory (PID/plant models), and `mncs-actions`
is on an active campaign branch (read-only). Automation therefore
targets what exists with exact identity: `mncs test` programs,
`mncs call` module/function/typed-args, read-only `doctor` checks.
There is deliberately no `action_name: string` + `json_args` fallback.
When a typed operation catalog lands, the `mncs-call` target shape
already matches its calling convention (module, function, typed wire
values, capability grants).

## AUTO-P4: Store has no small-mutable-state surface (non-blocking)

`mncs-store`'s Python API is a content-addressed artifact transport
(hash/compare/recovery over compiled MNCS artifacts) — the wrong shape
for per-tick evaluation state. Automation persists atomic JSON
(definitions, state, occurrence log) with fsync + rename and records
this mismatch here instead of forking persistence. If Store gains a
small-state or KV application contract, the `tools/automation/store.py`
boundary is the migration point; schemas are already versioned.

## AUTO-P5: per-tick compile cost; cache adopted, sessions future (non-blocking)

Every native evaluation spawns `mncs call`. Measured: ~30s cold compile
of the evaluation graph, ~4s warm with `--cache-dir` (now wired through
every call via a shared content-keyed cache), ~50ms for trivial cached
programs. Ticks are infrequent by construction (schedule wakeups, watch
cadences), so ~4s per evaluation tick is acceptable — but a fleet of
short-cadence watches would pay it every minute. `mncs-test` already
uses a retained embed session; exposing the same for `call` (or a
batch-evaluate entry) would cut the warm cost further. Language-tooling
optimization, not a semantic gap.

## AUTO-P6: distributed scheduling needs leader election (future)

Single-node file locking is implemented and documented. Multi-node
operation would require election/leases; deliberately not
half-implemented.

## AUTO-P7: projection wire shapes await Commons promotion (non-blocking)

`mncs.reconcile-decision/1`, `mncs.projection-state/1`,
`mncs.publication-receipt/1`, and the projection relation types are
defined by their owning repositories (Automation, Store) and indexed
in `docs/rfcs/0002-projection-coherence.md`. MNCS-Commons owns the
family wire-contract plane (`mncs.verification-obligation-plan/1`
precedent) and should promote these shapes to `schemas/` with
`compat/` goldens, plus extend `family-semantic-edges/v1` with the
projection edge types. Commons sat on a foreign branch during this
campaign, so the shapes were defined at the producer side instead of
moved. No duplication: producers keep semantic ownership; Commons
would own only the promoted wire copies.

## AUTO-P8: Atlas projections do not consume projection state (non-blocking)

Atlas owns the dashboard projector, registry, and journal, but reads
family state through bespoke discovery rather than projection-state
records, and its prose journal is not derived from its canonical
journal-event log. Atlas sat on a campaign branch during this
campaign and was treated read-only. Desired: dashboard/journal
project from `mncs.projection-state/1` + receipts; prose checkpoints
derive from `mncs.journal.event.v1`; RSS stays greenfield.

## AUTO-P9: Forge has no reconcile execution targets (non-blocking)

Automation decides; Forge executes. No typed target yet accepts a
reconcile decision (`projection-regenerate`, `projection-publish`)
and returns a receipt. Forge sat on a campaign branch during this
campaign and was treated read-only. The e2e test
(`tests/test_reconcile.py`) drives regeneration directly; production
wiring should go through Forge targets with receipt return.

## AUTO-P10: RAVEL does not select on projection staleness (non-blocking)

RAVEL owns bounded selection (`mncs.verification-plan/1`). It does
not yet consume projection staleness when deciding what to verify
next. RAVEL sat on a campaign branch during this campaign and was
treated read-only. Desired: stale projections join the evidence the
obligation planners select over.

## AUTO-P11: engine has no projection watch condition (future)

The native reconcile decision (`native/mncs/automation/reconcile.mncs`)
is callable and tested, and the occurrence machinery already gives
at-most-once + crash recovery, but no `projection` watch condition
observes canonical/observed/verdict triples into the engine yet.
Explicit follow-up, not half-implemented: conditions stay
`test-verdict` and `path-changed` until a projection observer with
documented canonical-state sourcing lands.

## Closed during this campaign

- Typed `mncs call` wire format (HostExecutionValue tags) and finite
  returns: probed, documented in code, covered by tests.
- `MNCS_LIBRARY_PATH` unlocks inventory + test runs for `use`-bearing
  modules: used by native suites and target validation.
- `mncs-doctor doctor --json` is side-effect-free: used as the doctor
  target with a flag allowlist.
