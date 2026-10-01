# Automation model reference

## Definition (`mncs.automation-definition/1`)

```json
{
  "schema_version": "mncs.automation-definition/1",
  "name": "hourly-selftest",
  "revision": 1,
  "lifecycle": "active",
  "trigger": {
    "kind": "schedule",
    "schedule": {"kind": "interval", "anchor_ms": 0, "period_ms": 3600000}
  },
  "target": {
    "kind": "mncs-test",
    "program": "/abs/path/suite.mncs",
    "libraries": ["/abs/mncs-test/native"],
    "test_identities": [],
    "timeout_s": 300
  },
  "granted_capabilities": ["test:execute"],
  "misfire": "catch-up",
  "catchup_bound": 3,
  "history_bound": 64,
  "expires_ms": null,
  "created_ms": 0,
  "updated_ms": 0,
  "target_accepted_digest": "sha256:..."
}
```

Schedules: `one-shot {at_ms}` (native Pinned, seq 1) · `interval
{anchor_ms, period_ms>0}` · `daily-utc {hour, minute}` (minute-of-day =
`(hour*60+minute)*60000`) · `daily-local {tz, hour, minute}` (IANA name
required; resolved host-side to Pinned UTC + local-day serial).

Watches: `{cadence_ms>=1000, edge: rising|while-true, condition}` with
conditions `test-verdict {program, libraries, test_identities,
timeout_s}` and `path-changed {path}` (first observation bootstraps the
baseline and does not fire).

Targets: `mncs-test`, `mncs-call {program, libraries, module, function,
args (HostExecutionValue wire), grants}`, `doctor {root, changed_paths,
extra_args (allowlisted), libraries (evaluation only)}`,
`environment-reconcile {environment_repo,
state_dir, workspace, libraries (evaluation only)}`. Default required
capabilities: `test:execute`, `call:execute` (+`grant-*` per grant),
`doctor:read`, `environment:reconcile`.

## Shared codes (native `_code()` fns ↔ `tools/automation/codes.py`)

Sched 0 Pinned · 1 Interval · 2 DailyUtc · 3 DailyLocal (native defers).
Trig 0 schedule · 1 watch. Cond 0 False · 1 True · 2 Unknown. Edge
0 Rising · 1 WhileTrue. Misfire 0 FireNow · 1 Skip · 2 CatchUp ·
3 MarkMissed. No concurrency field: overlap is structurally impossible
single-node (engine holds the state lock across evaluate+execute).
Reasons 0 due-fire · 1 waiting · 2 already-fired · 3 cond-false ·
4 cond-unknown · 5 edge-closed · 6 misfire-skipped · 7 concurrency
(host) · 8 stale-target (host) · 9 terminal (host).

## State (`mncs.automation-state/1`)

`{automation, definition_revision, prev_seq, last_at_ms, last_eval_ms,
last_condition, last_observed, pending|null, active, coalesced,
wakeup_ms, eval_errors, last_block_reason}`.

## Occurrence log (JSONL, append-only, pruned to `history_bound`)

`{occurrence_id, seq, definition_revision, trigger_kind,
scheduled_at_ms|observed, evaluated_ms, decision:
fired|missed|skipped|suppressed|blocked|terminal|unknown,
reason, reason_name, target:{kind}, invocation_id|null,
outcome: null|{status: ok|failed|unknown, ...execution identities},
condition_value}`.

## CLI

```
mncs-automation [--state-dir DIR] [--mncs BIN] [--now-ms MS]
  define --file DEF.json | validate --file DEF.json [--verbose]
  list | show NAME [--limit N] | next [NAME] | occurrences NAME [--limit N]
  why NAME
  enable|disable NAME | update NAME --file DEF.json | revalidate NAME
  delete NAME [--purge]
  tick [--dry-run] [--no-execute] | run [--max-idle-s S] [--passes N]
  recover
```

`tick --dry-run` persists and executes nothing. `why` explains the last
evaluation (lifecycle, trigger, condition, seqs, wakeup, pending,
blocks). Exit 0 normally, 1 on pass errors, 2 on invalid input.

## State directory layout

`definitions/<aid>.json · state/<aid>.json ·
occurrences/<aid>.jsonl · artifacts/<aid>/<inv>.* · policy.json · lock`.
Default: `~/.local/share/mncs-automation`.

## Reconciliation (`mncs.reconcile-decision/1`)

Native `mncs.automation.reconcile.v1::reconcile_tick` decides one
projection at one instant; `adopt_observed` advances the observed
generation after a host-performed regeneration. See
`docs/rfcs/0002-projection-coherence.md` for the architecture.

```json
{
  "schema_version": "mncs.reconcile-decision/1",
  "action": 1,
  "action_name": "regenerate",
  "reason": 1,
  "reason_name": "canonical-advanced",
  "new_observed": 4,
  "publish": false,
  "publish_reason": 0,
  "wakeup_ms": 0
}
```

Actions 0 up-to-date · 1 regenerate · 2 await-verification ·
3 blocked · 4 failed-verification. Reasons 0 current ·
1 canonical-advanced · 2 verification-unknown ·
3 verification-failed · 4 observed-ahead. Publish reasons 0 none ·
1 threshold-reached · 2 latency-exceeded. Adopt reasons 0 adopted ·
1 stale-regeneration · 2 regression-refused. Verdicts 0 fail ·
1 pass · 2 unknown (mirror CondValue positions). Host codes live in
`tools/automation/codes.py` and are cross-checked against the native
`_code()` functions by `tests/test_reconcile.py`.

## Projection plan (`mncs.reconcile-plan/1`)

Native `mncs.automation.projection.v1::plan_tick` answers "may this
projection regenerate now, and how?" It always computes the reconcile
decision (so evidence shows staleness even while deferred) and
reports `execute` only when the safety gate proceeds and the
decision is regenerate. Canonical advances exactly once per observed
input change; the caller persists advanced canonical, observed, and
input digest atomically on adopt, so replay before adopt is
identical. `tools/reconcile.py plan --request REQ.json` is the
provider entrypoint (transport only); it prints this envelope:

```json
{
  "schema_version": "mncs.reconcile-plan/1",
  "projection": "mncs-doc:rfc-index",
  "gate": 0, "gate_name": "proceed",
  "gate_reason": 0, "gate_reason_name": "ok",
  "action": 1, "action_name": "regenerate",
  "reason": 1, "reason_name": "canonical-advanced",
  "new_canonical": 5, "new_observed": 4,
  "publish": false, "publish_reason": 0, "wakeup_ms": 0,
  "execute": true
}
```

Gates 0 proceed · 1 defer · 2 escalate. Gate reasons 0 ok ·
1 deferred-foreign-claim · 2 deferred-foreign-mutation ·
3 deferred-foreign-branch · 4 deferred-ambiguous ·
5 deferred-region-explicit-only · 6 escalate-ambiguous-markers ·
7 escalate-unknown-target · 8 escalate-human-only ·
9 escalate-unknown-repo · 10 deferred-output-diverged ·
11 escalate-deferral-exhausted.

Fact codes (host-observed, native-interpreted): repo 0 clean ·
1 dirty-generated-only · 2 dirty-other · 3 unknown; branch
0 mainline · 1 foreign · 2 unknown; claim 0 none · 1 self ·
2 foreign · 3 adopted; target 0 whole-file · 1 region-in-file ·
2 human-only · 3 unknown; region 0 missing · 1 invalid · 2 valid ·
3 not-applicable (shared with `mncs.doc.region`); output 0 missing ·
1 matches-fresh · 2 matches-last-render · 3 diverged · 4 unknown.
`splice_ok` authorizes region splicing: derived from declaration
policy on the ambient path, granted with a live claim on the
explicit apply path; `defer_bound` of 0 means unbounded deferral.

## Adoption (`mncs.adopt-decision/1`)

`tools/reconcile.py adopt --request REQ.json` calls native
`mncs.automation.reconcile.v1::adopt_observed` and prints:

```json
{
  "schema_version": "mncs.adopt-decision/1",
  "projection": "mncs-doc:rfc-index",
  "accept": true, "new_observed": 5,
  "reason": 0, "reason_name": "adopted"
}
```

Request: `observed_gen`, `regenerated_gen`, `canonical_gen` (integers).
Accepts only when the regeneration was built from exactly the current
canonical generation without moving observed backwards. Reasons 0
adopted · 1 stale-regeneration · 2 regression-refused. Hosts must
adopt through this decision; writing observed generations directly
bypasses the stale/regression guard.

## Revisit (`mncs.revisit-decision/1`)

`tools/reconcile.py revisit --request REQ.json` calls native
`mncs.automation.projection.v1::revisit_tick` and prints:

```json
{
  "schema_version": "mncs.revisit-decision/1",
  "projection": "mncs-doc:rfc-index",
  "wait": 1, "wait_name": "claim",
  "event": 1, "event_name": "claim-changed",
  "revisit": true
}
```

Request: `wait`, `event` (integers). Waits 0 none · 1 claim ·
2 verification · 3 provider · 4 repo-state. Events 0 tick ·
1 claim-changed · 2 verdict-resolved · 3 provider-changed ·
4 repo-changed · 5 declaration-changed. Each wait wakes only on the
events that can resolve it; a bare tick never revisits and unknown
codes fail closed into quiet, so continuous operation never polls.
