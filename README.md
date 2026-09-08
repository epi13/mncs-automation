# mncs-automation

Machine-native automation and workflow infrastructure for MNCS.

`mncs-automation` is a human-facing pressure project for typed workflows, triggers, schedules, files, APIs, application actions, durable execution, credentials, permissions, retries and audit evidence. It should feel closer to a concise scripting/workflow language while preserving machine-visible semantics.

## Initial scope

- typed workflow graphs and steps
- event, file, API and schedule triggers
- actions and data transformations
- explicit effects, capabilities and permissions
- retry, timeout and idempotency semantics
- durable workflow state and resumability
- opaque credential/secret handles
- execution history, provenance and audit evidence
- plugin/adapter boundaries for external systems

## Repository layout

- `docs/ARCHITECTURE.md`
- `docs/rfcs/0001-foundation.md`
- `docs/LANGUAGE_PRESSURES.md`
- `AGENTS.md`
