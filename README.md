# mncs-automation

Persistent, typed, explainable automation for MNCS. Automation decides
**when** declared work becomes eligible; Forge, Test, and Doctor decide
how it executes. There is one canonical implementation.

## Quick start

```text
python3 tools/mncs_automation.py define --file my-def.json
python3 tools/mncs_automation.py tick --dry-run --now-ms 1780318740000
python3 tools/mncs_automation.py tick
python3 tools/mncs_automation.py why my-automation
python3 tools/mncs_automation.py run --passes 10   # daemon (bounded here)
```

Definitions are structured JSON (`mncs.automation-definition/1`), not
cron strings. See [`docs/MODEL.md`](docs/MODEL.md). Ownership and
boundaries: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Known gaps:
[`docs/PRESSURES.md`](docs/PRESSURES.md).

## Layout

- `native/mncs/automation/` — `time`, `schedule`, `trigger`,
  `evaluation` semantics with native test blocks (33 tests: 7 time,
  8 schedule, 6 trigger, 12 evaluation).
- `tools/automation/` — thin host bridge: model/store/native-call/tz/
  conditions/targets/engine/CLI. Filesystem, clocks, tzdata, and
  subprocess transport stay here; decisions stay native.
- `tests/test_automation.py` — deterministic suite (virtual clock,
  recovery, DST, properties, native cross-checks).
- `tests/fixtures/` — tiny typed-call and condition programs.
- `docs/rfcs/0001-foundation.md` — original intent (historical; the
  architecture docs are authoritative).

## Verification

```text
export MNCS_BIN=../mncs-language/target/debug/mncs
export MNCS_LANGUAGE_ROOT=../mncs-language
export MNCS_TEST_NATIVE=../mncs-test/native
python3 -m unittest tests.test_automation
```

Native suites also run standalone, e.g.
`mncs test native/mncs/automation/evaluation.mncs --library ...`.
