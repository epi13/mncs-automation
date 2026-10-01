# RFC 0002: Projection coherence — reconciling derived representations with canonical state

Status: Implemented (reconcile core, projection records, doc apply, media manifests); adoption by Atlas/Commons/Forge/RAVEL pending.

Owner: mncs-automation (decision), mncs-store (bytes), mncs-doc (text apply), mncs-media (manifests).

Affected repositories: mncs-automation, mncs-store, mncs-doc, mncs-media, mncs-atlas, MNCS-Commons, mncs-forge, RAVEL.

## Problem

Documentation, Atlas pages, journal entries, media artifacts, and
publications are projections of project state, but nothing reconciles
them with that state. Each surface is updated by hand or by a bespoke
script an agent must remember to run, so representations drift from
verified reality and from each other. There is no shared answer to
"which derived representations are stale for canonical generation N",
no verification gate before authoritative claims, no durable record of
what was published where, and no restart-safe convergence loop.

## Principle

MNCS continuously reconciles its external representations with its
actual internal project state:

```text
REAL MNCS PROJECT STATE
        |
        | authoritative facts / identities / evidence / generations
        v
CANONICAL MACHINE-READABLE STATE
        |
        +------ dependency / impact graph
        |
        +------ reconciliation (this RFC)
        |
        +--------------------+--------------------+--------------------+
        |                    |                    |                    |
        v                    v                    v                    v
 documentation          Atlas/UI             journal              media
 projections            projections          projections          projections
        |                    |                    |                    |
        +--------------------+--------------------+--------------------+
                             |
                             v
                    publication surfaces
```

Generated outputs are never semantic authorities. Facts stay
traceable to evidence; narrative keeps provenance to its source
state. Reconciliation is desired-vs-observed comparison, not
imperative mutation scripts.

## Authorities (reused, not rebuilt)

| Concern | Owner | Mechanism |
|---|---|---|
| Repository identity | each repo | `.mncs/project.json` (`mncs-family.repository-manifest/v0alpha1`) |
| Language facts | mncs-language | capability index, inventories via `$MNCS_BIN` |
| Declaration/test identity | mncs-compiler | `mncs.compiler.declaration-inventory/1`, `test-inventory/1` |
| Family architecture/pressures | MNCS-Commons | architecture model, `family-semantic-edges/v1` impact overlay |
| Bounded selection/planning | RAVEL | `mncs.verification-plan/1`, obligation planners |
| Durable generations/bytes | mncs-store | `store.generation`, atomic publication, journal |
| Execution | Forge / Test / Doctor | typed targets; Automation never executes |
| Tick/trigger semantics | mncs-automation | `evaluation.mncs`, `trigger.mncs` (idempotent, UNKNOWN-safe) |
| Doc model/projection | mncs-doc | `mncs.documentation-{model,index,validation}/1`, `MNCS:generated` regions |
| Journal (prose + events) | mncs-atlas | `mncs.journal.event.v1`, maintainer (read-only for this RFC) |
| Media semantics | mncs-media | descriptors, budgets, backend contract |
| Provenance/rights | mncs-rights-provenance | manifests, evidence, policy |

`mncs-control` is control theory (PID/plant), not orchestration, and
owns no part of this loop. No canonical event bus exists; watches
reconcile against canonical state on cadence, and this RFC follows
that pattern: polling convergence with generations, never transient
delivery as a correctness requirement.

## New contracts (this RFC)

Codes are shared integers across the native/host boundary, following
the Automation convention that tests cross-check every value.

Verification verdicts (mirror trigger CondValue positions):
`0 fail`, `1 pass`, `2 unknown`.

Projection status: `0 current`, `1 stale`, `2 unknown`, `3 blocked`
(observed ahead of canonical), `4 failed` (verdict failed).

### `mncs.reconcile-decision/1` (mncs-automation, native)

`mncs.automation.reconcile.v1::reconcile_tick` decides one
projection at one instant. Actions: `0 up-to-date`, `1 regenerate`,
`2 await-verification`, `3 blocked`, `4 failed-verification`.
Reasons: `0 current`, `1 canonical-advanced`, `2 verification-unknown`,
`3 verification-failed`, `4 observed-ahead`.

Rules:

- Same inputs always produce the same decision (idempotent replay).
- `observed > canonical` blocks and never publishes: a stale
  generation can never overwrite newer output.
- `observed < canonical` regenerates only on a passing verdict
  (authoritative default). Failed verdicts withhold the claim even
  above publication thresholds. Unknown verdicts wait unless the
  caller explicitly allows drafts.
- The native side never advances `observed`. The host advances it
  only after a regeneration it performed, through `adopt_observed`,
  which refuses stale (`regenerated != canonical`) and regressing
  (`regenerated < observed`) generations. Crash between regenerate
  and adopt replays safely: the next tick says regenerate again with
  deterministic bytes, never duplicate-publication.
- Publication is orthogonal to currency: `publish` fires when
  verified-but-unpublished work reaches a count threshold or exceeds
  a maximum latency, and only on a passing verdict. Held work names
  its revisit (`wakeup_ms`).

```json
{"schema_version": "mncs.reconcile-decision/1", "action": 1,
 "action_name": "regenerate", "reason": 1,
 "reason_name": "canonical-advanced", "new_observed": 4,
 "publish": false, "publish_reason": 0, "wakeup_ms": 0}
```

### `mncs.projection-state/1` (mncs-store, native bytes)

`store.projection.v1` persists per-projection desired-vs-observed
state in a 132-byte fixed record (`PJ` magic): projection identity,
canonical generation, observed generation, verification code, status
code, evidence identity, supersedes identity. `is_current` holds only
when observed equals canonical with status current and a passing
verdict. The JSON interchange mirrors the bytes:

```json
{"schema_version": "mncs.projection-state/1",
 "projection": "sha256:...", "canonical_generation": 5,
 "observed_generation": 4, "verification": 1,
 "verification_name": "pass", "status": 1, "status_name": "stale",
 "evidence": "sha256:...", "supersedes": null}
```

### `mncs.publication-receipt/1` (mncs-store, native bytes)

`store.receipt.v1` persists one completed publication in a 140-byte
fixed record (`PR` magic): artifact identity, target identity,
external (provider-issued handle) identity, canonical generation,
evidence identity. Presence is the publication fact; absence means
unpublished. `same_publication` compares the identity triple and is
the dedup key: re-recording never duplicates. Dependent projections
reconcile cross-links from receipts instead of rediscovering URLs.

```json
{"schema_version": "mncs.publication-receipt/1",
 "artifact": "sha256:...", "target": "sha256:...",
 "external": "sha256:...", "external_uri": null,
 "canonical_generation": 7, "evidence": "sha256:..."}
```

### `mncs.projection-descriptor/1` (mncs-doc, native policy)

`mncs.doc.projection.v1::admit` gates every document-region write:
ambiguous regions are never admitted, missing regions need explicit
creation consent, and sourceless or templateless bytes are refused.
`tools/project.py project-apply` finds spans (transport), classifies
them through native `mncs.doc.region`, admits through native policy,
and only then splices bytes — failing closed without the compiler.

```json
{"schema_version": "mncs.projection-descriptor/1",
 "projection": "mncs-doc:rfc-index", "document": "docs/rfc-index.generated.md",
 "template": "project-rfc-index", "sources": ["docs/rfcs/0001-foundation.md"],
 "region": "mncs-generated", "create_allowed": true}
```

### `mncs.media-manifest/1` + `mncs.session-evidence/1` (mncs-media)

`mncs.media.manifest.v1` binds one media artifact to its source
generation, derivation chain, and provenance. Admission requires
content binding, provenance, and named parents for derived artifacts.
Executability is separate from validity: audio/video manifests admit
as descriptors while backend execution stays image-only, so nothing
silently no-ops. `derivation_current` marks derived artifacts older
than their parent stale; `publication_claim_ok` requires a linked
receipt for published claims.

```json
{"schema_version": "mncs.media-manifest/1", "artifact": "sha256:...",
 "kind": "image", "content": "sha256:...", "semantic": {"dims": [8, 6]},
 "derivation": ["sha256:parent"], "provenance": "sha256:...",
 "source_generation": 5, "descriptor_only": false,
 "targets": [], "receipt": null}
```

Session evidence (commands, tests, diffs, diagnostics, narrative)
feeds manifests; its wire shape is `mncs.session-evidence/1`
(described in `mncs-media/docs/MEDIA_MODEL.md`). Video assembly,
terminal replay, and external publishers (YouTube/Reddit/RSS) remain
greenfield: they consume manifests and emit receipts, and must not be
hard-coded as architecture.

### Dependency edges (no new code)

Impact narrowing reuses `store.relationship` v2 with registered
relation-type identities: `mncs.relation/projects-from/1`
(projection derived from canonical subject),
`mncs.relation/depends-on/1` (projection needs another projection),
`mncs.relation/supersedes/1` (generation lineage). A relation-type
identity is the first 32 bytes of the SHA-256 of its canonical
descriptor JSON, computed by the host and compared by byte equality
natively. Compiler `mncs.semantic-impact/1` stays the source-level
truth; the Commons `family-semantic-edges/v1` overlay stays the
family-level truth; these edges are the projection-level truth.

## End-to-end flow (implemented and tested)

`mncs-automation/tests/test_reconcile.py` drives the loop across
repository boundaries with virtual time:

```text
canonical generation 5 observed, projection at 4, verdict PASS
  -> reconcile_tick: regenerate / canonical-advanced
  -> project-apply: native classify + admit, splice bytes, prose preserved
  -> adopt_observed(4, 5, 5): accept, observed advances
  -> reconcile_tick: up-to-date; re-apply --check clean, bytes unchanged
```

Covered properties: idempotent ticks, staleness on canonical advance,
verification gating (fail withholds, unknown waits, drafts opt-in),
interruption recovery without duplicates, stale/regressing adoption
refused, observed-ahead blocking, threshold/latency publication with
per-receipt firing, receipt identity dedup, human prose preservation
(including non-ASCII byte offsets).

## Deliberate non-goals

- No workflow engine, planner, executor, or event bus in Automation.
- No second source of language/architecture/pressure facts anywhere.
- No LLM narrative in the authoritative path; narrative keeps
  provenance to source facts when it exists.
- No live external publishing in this slice; publishers are future
  receipt-emitting targets exercised against mock/dry-run sinks.
- No merge of responsibilities into one repository; each contract
  lives with its owning subsystem.

## Adoption pressures (recorded, not forced)

- Commons (foreign branch; untouched): promote these wire shapes to
  family contracts beside `mncs.verification-obligation-plan/1`,
  and extend `family-semantic-edges/v1` with the projection edge
  types. See AUTO-P7.
- Atlas (campaign branch; untouched): project dashboard/journal from
  projection-state records; resolve the prose-vs-event journal
  duality by deriving prose checkpoints from the canonical event
  log; RSS remains greenfield. See AUTO-P8.
- Forge (campaign branch; untouched): accept reconcile decisions as
  typed execution targets (`projection-regenerate`,
  `projection-publish`) with receipt return. See AUTO-P9.
- RAVEL (campaign branch; untouched): consume projection staleness
  in bounded selection (`mncs.verification-plan/1`). See AUTO-P10.
- Automation engine follow-up: a `projection` watch condition
  (canonical/observed/verdict observation) plus occurrence
  integration, reusing the existing at-most-once machinery. See
  AUTO-P11.
- mncs-doc follow-up: `model.mncs` native tests use the retired
  `test -> i64` style and are not executed; migrate to
  `mncs.test.suite` or retire. See DOC-07.
