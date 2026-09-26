# Agent and contributor contract

- Prefer `mncs-language` for workflow/runtime implementation.
- Automation semantics live in `native/mncs/automation/`; the host
  (`tools/automation/`) is transport only (clocks, tzdata, filesystem,
  subprocess). Never re-derive a schedule, edge, or firing decision in
  host code — call native `evaluate_tick` and persist what it returns.
- Time is always injectable: production reads the wall clock once per
  pass; tests pass `--now-ms`. No `now()` calls inside decision paths.
- Permissions/capabilities are explicit; credentials remain opaque and least-privilege.
- Retry and resume behavior must account for idempotency and partial completion.
- Do not hide side effects, background tasks or durable state transitions.
- Record language/compiler/runtime pressure in `docs/LANGUAGE_PRESSURES.md`;
  automation-workload gaps go in `docs/PRESSURES.md`.
- Tests should cover replay, duplicate events, failures, retries, time and schema evolution.
- `mncs-language` and `mncs-compiler` are read-only; reach them via the
  `mncs` binary only. Repositories on active campaign branches are
  read-only: inspect, never modify.
- Occurrence identities fire at most once, including across crashes.
  Never invent random IDs where deterministic identity works.
- Unknown fields in definitions are rejected; unknown conditions never
  fire and never count as false.
