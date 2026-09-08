# Agent and contributor contract

- Prefer `mncs-language` for workflow/runtime implementation.
- Workflows and effects should remain machine-inspectable rather than hidden inside arbitrary callbacks.
- Permissions/capabilities are explicit; credentials remain opaque and least-privilege.
- Retry and resume behavior must account for idempotency and partial completion.
- Do not hide side effects, background tasks or durable state transitions.
- Record language/compiler/runtime pressure in `docs/LANGUAGE_PRESSURES.md`.
- Tests should cover replay, duplicate events, failures, retries, time and schema evolution.
