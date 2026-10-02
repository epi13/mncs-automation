"""Host test suite for the canonical Automation system.

Time is injected everywhere (`tick(..., now_ms=...)`); no test sleeps
for real time. Native semantics execute for real through `mncs call`
and `mncs test` (skipped cleanly when the compiler binary is absent);
orchestration, persistence, and policy are asserted against the state
directory, never against wall-clock behavior.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(REPO / "tools"))

from automation import codes, engine, model, native, store, targets  # noqa: E402
from automation.model import DefinitionError  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures"
WORKSPACE = Path(os.environ.get("MNCS_WORKSPACE_ROOT", str(REPO.parent)))


def find_mncs() -> str | None:
    for candidate in (
        os.environ.get("MNCS_BIN"),
        os.environ.get("MNCS_BINARY"),
        str(WORKSPACE / "mncs-language" / "target" / "debug" / "mncs"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


MNCS = find_mncs()
LIBRARIES = [str(WORKSPACE / "mncs-test" / "native"),
             str(REPO / "native")]


def need_mncs(test):
    return unittest.skipIf(MNCS is None, "mncs binary unavailable")(test)


def open_policy() -> dict[str, Any]:
    return {"schema_version": "mncs.automation-policy/1",
            "allowed_target_kinds": ["mncs-test", "mncs-call", "doctor"],
            "program_roots": [], "doctor_binary": "", "mncs_binary": "",
            "max_automations": 256}


from typing import Any  # noqa: E402


class EngineCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="automation-test-"))
        self.state_dir = self.tmp / "state"
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def define(self, document: dict, now_ms: int = 1_000) -> str:
        clean = model.validate_definition(document, now_ms=now_ms)
        target = clean["target"]
        if target.get("program"):
            clean["target_accepted_digest"] = targets.program_digest(
                target["program"])
        else:
            clean["target_accepted_digest"] = None
        aid = model.automation_id(clean["name"])
        paths = store.ensure(self.state_dir)
        store.write_json(paths["definitions"] / f"{aid}.json", clean)
        store.write_json(paths["state"] / f"{aid}.json",
                         model.fresh_state(aid, clean["revision"]))
        return aid

    def tick(self, now_ms: int, **kwargs) -> dict:
        return engine.tick(state_dir=self.state_dir, mncs=MNCS,
                           now_ms=now_ms, policy=open_policy(), **kwargs)

    def occurrences(self, aid: str) -> list:
        return store.read_jsonl(
            self.state_dir / "occurrences" / f"{aid}.jsonl")

    def state(self, aid: str) -> dict:
        return store.read_json(self.state_dir / "state" / f"{aid}.json")

    def ping_target(self, **extra) -> dict:
        base: dict[str, Any] = {
            "kind": "mncs-call",
            "program": str(FIXTURES / "tiny.mncs"),
            "libraries": LIBRARIES,
            "module": "mncs.automation.fixture.tiny.v1",
            "function": "ping",
            "args": [{"integer": {"value": 41}}],
        }
        base.update(extra)
        return base

    def schedule_def(self, name: str, schedule: dict, **extra) -> dict:
        base: dict[str, Any] = {
            "schema_version": codes.DEFINITION_SCHEMA,
            "name": name,
            "trigger": {"kind": "schedule", "schedule": schedule},
            "target": self.ping_target(),
            "granted_capabilities": ["call:execute"],
        }
        base.update(extra)
        return base


class DefinitionTests(unittest.TestCase):
    def test_minimal_valid_definition(self) -> None:
        clean = model.validate_definition({
            "schema_version": codes.DEFINITION_SCHEMA,
            "name": "hourly-thing",
            "trigger": {"kind": "schedule",
                        "schedule": {"kind": "interval", "anchor_ms": 0,
                                     "period_ms": 3_600_000}},
            "target": {"kind": "mncs-test", "program": "/x/y.mncs"},
            "granted_capabilities": ["test:execute"]})
        self.assertEqual(clean["misfire"], "catch-up")
        self.assertNotIn("concurrency", clean)
        self.assertEqual(model.automation_id("hourly-thing")[:5], "auto-")
        self.assertEqual(
            model.occurrence_id("auto-abcdef12", 3, 42), "auto-abcdef12:r3:42")

    def test_rejects_bad_names_kinds_fields(self) -> None:
        base = {"schema_version": codes.DEFINITION_SCHEMA, "name": "ok-name",
                "trigger": {"kind": "schedule",
                            "schedule": {"kind": "interval", "anchor_ms": 0,
                                         "period_ms": 1000}},
                "target": {"kind": "mncs-test", "program": "/x"},
                "granted_capabilities": []}
        for mutate in (
            lambda d: d.update(name="Bad Name"),
            lambda d: d.update(name=""),
            lambda d: d["trigger"].update(kind="cron"),
            lambda d: d["trigger"]["schedule"].update(
                kind="interval", period_ms=0),
            lambda d: d["trigger"]["schedule"].update(
                kind="daily-local", tz="Mars/Olympus", hour=5, minute=0),
            lambda d: d["target"].update(kind="shell"),
            lambda d: d.update(granted_capabilities="all"),
            lambda d: d.update(surprise_field=1),
            lambda d: d["target"].update(
                args=[{"run": "rm -rf /"}]),
        ):
            document = json.loads(json.dumps(base))
            mutate(document)
            with self.assertRaises(DefinitionError, msg=str(mutate)):
                model.validate_definition(document)

    def test_daily_local_needs_real_timezone(self) -> None:
        with self.assertRaises(DefinitionError):
            model.validate_definition({
                "schema_version": codes.DEFINITION_SCHEMA, "name": "tzt",
                "trigger": {"kind": "schedule",
                            "schedule": {"kind": "daily-local",
                                         "tz": "Not/AZone",
                                         "hour": 5, "minute": 0}},
                "target": {"kind": "mncs-test", "program": "/x"},
                "granted_capabilities": []})


class NativeCodeTests(unittest.TestCase):
    @need_mncs
    def test_codes_match_native(self) -> None:
        # sched_kind_code returns a bare u64, not a record.
        document = native.call_function(
            mncs=MNCS,
            program=str(REPO / "native" / "mncs" / "automation" / "schedule.mncs"),
            module="mncs.automation.schedule.v1",
            function="sched_kind_code",
            args=[{"finite": {"type": "SchedKind", "variant": "Interval"}}],
            libraries=LIBRARIES)
        returned = document["call"]["returned"][0]["integer"]["value"]
        self.assertEqual(returned, codes.SCHED_INTERVAL)
        for variant, expected in (("Pinned", codes.SCHED_PINNED),
                                  ("DailyUtc", codes.SCHED_DAILY_UTC),
                                  ("DailyLocal", codes.SCHED_DAILY_LOCAL)):
            document = native.call_function(
                mncs=MNCS,
                program=str(REPO / "native" / "mncs" / "automation" / "schedule.mncs"),
                module="mncs.automation.schedule.v1",
                function="sched_kind_code",
                args=[{"finite": {"type": "SchedKind", "variant": variant}}],
                libraries=LIBRARIES)
            self.assertEqual(document["call"]["returned"][0]["integer"]["value"],
                             expected)
        for variant, expected in (("False", codes.COND_FALSE),
                                  ("True", codes.COND_TRUE),
                                  ("Unknown", codes.COND_UNKNOWN)):
            document = native.call_function(
                mncs=MNCS,
                program=str(REPO / "native" / "mncs" / "automation" / "trigger.mncs"),
                module="mncs.automation.trigger.v1",
                function="cond_code",
                args=[{"finite": {"type": "CondValue", "variant": variant}}],
                libraries=LIBRARIES)
            self.assertEqual(document["call"]["returned"][0]["integer"]["value"],
                             expected)
        for function, expected in (("misfire_fire_now", codes.MISFIRE_FIRE_NOW),
                                   ("misfire_skip", codes.MISFIRE_SKIP),
                                   ("misfire_catch_up", codes.MISFIRE_CATCH_UP),
                                   ("misfire_mark_missed",
                                    codes.MISFIRE_MARK_MISSED)):
            document = native.call_function(
                mncs=MNCS,
                program=str(REPO / "native" / "mncs" / "automation" / "evaluation.mncs"),
                module="mncs.automation.evaluation.v1",
                function=function, args=[], libraries=LIBRARIES)
            self.assertEqual(document["call"]["returned"][0]["integer"]["value"],
                             expected)


class OneShotTests(EngineCase):
    @need_mncs
    def test_fires_once_then_completes_without_duplicates(self) -> None:
        aid = self.define(self.schedule_def(
            "smoke-oneshot", {"kind": "one-shot", "at_ms": 5_000}))
        early = self.tick(4_000, dry_run=True)
        self.assertEqual(early["evaluated"][0]["decision"], "wait")
        self.assertEqual(early["evaluated"][0]["wakeup_ms"], 5_000)
        self.assertEqual(self.occurrences(aid), [])
        fired = self.tick(5_001)
        first = fired["evaluated"][0]
        self.assertEqual(first["decision"], "fired")
        self.assertEqual(first["occurrence_id"], f"{aid}:r1:1")
        self.assertEqual(first["outcome"]["status"], "ok")
        again = self.tick(9_999)
        self.assertEqual(again["evaluated"][0]["decision"], "skipped")
        self.assertEqual(len(self.occurrences(aid)), 1)


class IntervalTests(EngineCase):
    @need_mncs
    def test_fire_now_records_missed_and_dedups(self) -> None:
        aid = self.define(self.schedule_def(
            "every-second", {"kind": "interval", "anchor_ms": 0,
                             "period_ms": 1_000}, misfire="fire-now"))
        first = self.tick(2_500)
        self.assertEqual(first["evaluated"][0]["decision"], "wait")
        self.assertEqual(self.state(aid)["prev_seq"], 3)
        report = self.tick(3_500)
        self.assertEqual(report["evaluated"][0]["decision"], "fired")
        self.assertEqual(report["evaluated"][0]["occurrence_id"],
                         f"{aid}:r1:4")
        # Downtime accrues exactly one missed-range marker, not one line
        # per sequence.
        report = self.tick(6_500)
        self.assertEqual(report["evaluated"][0]["occurrence_id"],
                         f"{aid}:r1:7")
        markers = [record for record in self.occurrences(aid)
                   if record["decision"] == "missed"]
        self.assertEqual(len(markers), 1)
        self.assertEqual((markers[0]["missed_from"],
                          markers[0]["missed_to"],
                          markers[0]["missed_count"]), (5, 6, 2))
        repeat = self.tick(6_500)
        self.assertEqual(repeat["evaluated"][0]["decision"], "wait")
        self.assertEqual(len([record for record in self.occurrences(aid)
                              if record["decision"] == "fired"]), 2)

    @need_mncs
    def test_catch_up_drains_one_per_tick(self) -> None:
        aid = self.define(self.schedule_def(
            "catcher", {"kind": "interval", "anchor_ms": 0,
                        "period_ms": 1_000},
            misfire="catch-up", catchup_bound=3))
        self.assertEqual(self.tick(8_500)["evaluated"][0]["decision"],
                         "wait")
        self.assertEqual(self.state(aid)["prev_seq"], 9)
        first = self.tick(12_500)
        self.assertEqual(first["evaluated"][0]["occurrence_id"],
                         f"{aid}:r1:11")
        second = self.tick(12_500)
        self.assertEqual(second["evaluated"][0]["occurrence_id"],
                         f"{aid}:r1:12")
        seqs = sorted(record["seq"] for record in self.occurrences(aid)
                      if record["decision"] == "fired")
        self.assertEqual(seqs, [11, 12])
        markers = [record for record in self.occurrences(aid)
                   if record["decision"] == "missed"]
        self.assertEqual((markers[0]["missed_from"],
                          markers[0]["missed_to"]), (10, 10))

    @need_mncs
    def test_skip_policy_advances_without_firing(self) -> None:
        aid = self.define(self.schedule_def(
            "skipper", {"kind": "interval", "anchor_ms": 0,
                        "period_ms": 1_000}, misfire="skip"))
        self.assertEqual(self.tick(5_500)["evaluated"][0]["decision"],
                         "wait")
        report = self.tick(6_500)
        self.assertEqual(report["evaluated"][0]["decision"], "wait")
        self.assertEqual(report["evaluated"][0]["reason_name"],
                         "misfire-skipped")
        markers = [record for record in self.occurrences(aid)
                   if record["decision"] == "missed"]
        self.assertEqual(len(markers), 1)
        self.assertEqual((markers[0]["missed_from"],
                          markers[0]["missed_to"]), (7, 7))
        self.assertEqual(self.state(aid)["prev_seq"], 7)


class LifecycleTests(EngineCase):
    @need_mncs
    def test_disable_never_fires_then_resumes(self) -> None:
        aid = self.define(self.schedule_def(
            "pausable", {"kind": "interval", "anchor_ms": 0,
                         "period_ms": 1_000}, lifecycle="paused"))
        report = self.tick(5_000)
        self.assertEqual(report["evaluated"][0]["decision"], "skipped")
        self.assertEqual(self.occurrences(aid), [])
        paths = store.ensure(self.state_dir)
        definition = store.read_json(
            paths["definitions"] / f"{aid}.json")
        definition["lifecycle"] = "active"
        store.write_json(paths["definitions"] / f"{aid}.json", definition)
        # Paused time accrues no history, like blocked time: the resume
        # tick aligns instead of backfilling, then firing resumes.
        report = self.tick(5_000)
        self.assertEqual(report["evaluated"][0]["decision"], "wait")
        report = self.tick(6_000)
        self.assertEqual(report["evaluated"][0]["decision"], "fired")
        self.assertEqual(report["evaluated"][0]["occurrence_id"],
                         f"{aid}:r1:7")

    @need_mncs
    def test_expiry_is_terminal(self) -> None:
        aid = self.define(self.schedule_def(
            "short-lived", {"kind": "interval", "anchor_ms": 0,
                            "period_ms": 1_000}, expires_ms=2_000))
        report = self.tick(3_000)
        self.assertEqual(report["evaluated"][0]["decision"], "terminal")
        records = self.occurrences(aid)
        self.assertEqual(records[-1]["decision"], "terminal")


class WatchTests(EngineCase):
    def _watch_def(self, name: str, path: str, edge: str = "rising",
                   **extra) -> dict:
        base: dict[str, Any] = {
            "schema_version": codes.DEFINITION_SCHEMA,
            "name": name,
            "trigger": {"kind": "watch",
                        "watch": {"cadence_ms": 60_000, "edge": edge,
                                  "condition": {"kind": "path-changed",
                                                "path": path}}},
            "target": self.ping_target(),
            "granted_capabilities": ["call:execute"],
        }
        base.update(extra)
        return base

    @need_mncs
    def test_rising_edge_fires_once_per_change(self) -> None:
        watched = self.tmp / "watched.txt"
        watched.write_text("v1", encoding="utf-8")
        aid = self.define(self._watch_def("watcher", str(watched)))
        self.assertEqual(self.tick(1_000)["evaluated"][0]["decision"], "wait")
        watched.write_text("v2", encoding="utf-8")
        fired = self.tick(2_000)
        self.assertEqual(fired["evaluated"][0]["decision"], "fired")
        self.assertEqual(fired["evaluated"][0]["occurrence_id"],
                         f"{aid}:r1:1")
        self.assertEqual(self.tick(3_000)["evaluated"][0]["decision"], "wait")
        self.assertEqual(len(self.occurrences(aid)), 1)

    @need_mncs
    def test_while_true_fires_every_tick(self) -> None:
        # Level-triggered firing needs a condition that stays true;
        # path-changed is pulse-like by design, so use test-verdict.
        aid = self.define({
            "schema_version": codes.DEFINITION_SCHEMA, "name": "level",
            "trigger": {"kind": "watch",
                        "watch": {"cadence_ms": 60_000, "edge": "while-true",
                                  "condition": {
                                      "kind": "test-verdict",
                                      "program": str(
                                          FIXTURES / "cond_true.mncs"),
                                      "libraries": LIBRARIES}}},
            "target": self.ping_target(),
            "granted_capabilities": ["call:execute"]})
        self.tick(1_000)
        self.tick(2_000)
        fired = [record for record in self.occurrences(aid)
                 if record["decision"] == "fired"]
        self.assertEqual([record["seq"] for record in fired], [1, 2])

    @need_mncs
    def test_unknown_condition_never_fires(self) -> None:
        aid = self.define(self._watch_def(
            "ghost", str(self.tmp / "missing.txt")))
        report = self.tick(1_000)
        self.assertEqual(report["evaluated"][0]["decision"], "wait")
        self.assertEqual(report["evaluated"][0]["reason_name"],
                         "cond-unknown")
        self.assertEqual(self.occurrences(aid), [])

    @need_mncs
    def test_test_verdict_true_and_false(self) -> None:
        aid = self.define({
            "schema_version": codes.DEFINITION_SCHEMA, "name": "verdict",
            "trigger": {"kind": "watch",
                        "watch": {"cadence_ms": 60_000, "edge": "rising",
                                  "condition": {
                                      "kind": "test-verdict",
                                      "program": str(
                                          FIXTURES / "cond_true.mncs"),
                                      "libraries": LIBRARIES}}},
            "target": self.ping_target(),
            "granted_capabilities": ["call:execute"]})
        report = self.tick(1_000)
        self.assertEqual(report["evaluated"][0]["decision"], "fired")
        aid2 = self.define({
            "schema_version": codes.DEFINITION_SCHEMA, "name": "verdict-f",
            "trigger": {"kind": "watch",
                        "watch": {"cadence_ms": 60_000, "edge": "rising",
                                  "condition": {
                                      "kind": "test-verdict",
                                      "program": str(
                                          FIXTURES / "cond_false.mncs"),
                                      "libraries": LIBRARIES}}},
            "target": self.ping_target(),
            "granted_capabilities": ["call:execute"]})
        report = self.tick(2_000)
        second = [item for item in report["evaluated"]
                  if item["name"] == "verdict-f"][0]
        self.assertEqual(second["decision"], "wait")
        self.assertEqual(second["reason_name"], "cond-false")


class DryRunPurityTests(EngineCase):
    @need_mncs
    def test_dry_run_persists_nothing(self) -> None:
        aid = self.define(self.schedule_def(
            "pure", {"kind": "interval", "anchor_ms": 0,
                     "period_ms": 1_000}))
        before = self.state(aid)
        self.tick(5_500, dry_run=True)
        self.tick(5_500, dry_run=True)
        self.assertEqual(self.state(aid), before)
        self.assertEqual(self.occurrences(aid), [])


class NoExecuteTests(EngineCase):
    @need_mncs
    def test_observe_without_acting_loses_nothing(self) -> None:
        aid = self.define(self.schedule_def(
            "observer", {"kind": "one-shot", "at_ms": 5_000}))
        report = self.tick(5_500, execute=False)
        first = report["evaluated"][0]
        self.assertEqual(first["decision"], "would-fire")
        self.assertEqual(first["occurrence_id"], f"{aid}:r1:1")
        self.assertEqual(self.occurrences(aid), [])
        self.assertEqual(self.state(aid)["prev_seq"], 0)
        report = self.tick(5_500)
        self.assertEqual(report["evaluated"][0]["occurrence_id"],
                         f"{aid}:r1:1")
        self.assertEqual(report["evaluated"][0]["decision"], "fired")


class RecoveryTests(EngineCase):
    @need_mncs
    def test_crash_pending_becomes_unknown_without_refire(self) -> None:
        aid = self.define(self.schedule_def(
            "fragile", {"kind": "interval", "anchor_ms": 0,
                        "period_ms": 1_000}))
        self.tick(500)
        paths = store.ensure(self.state_dir)
        state = self.state(aid)
        state["pending"] = {"occurrence_id": f"{aid}:r1:99", "seq": 99,
                            "trigger_kind": "schedule",
                            "invocation_id": f"{aid}:r1:99:i0",
                            "started_ms": 600}
        store.write_json(paths["state"] / f"{aid}.json", state)
        markers = engine.recover(state_dir=self.state_dir)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["outcome"]["status"], "unknown")
        self.assertEqual(markers[0]["occurrence_id"], f"{aid}:r1:99")
        report = self.tick(1_500)
        records = self.occurrences(aid)
        crashed = [record for record in records
                   if record["occurrence_id"] == f"{aid}:r1:99"]
        self.assertEqual(len(crashed), 1)
        self.assertEqual(crashed[0]["decision"], "unknown")
        self.assertTrue(all(record["decision"] != "fired" for record in crashed))
        fired = [record for record in records
                 if record["decision"] == "fired"]
        self.assertEqual([record["occurrence_id"] for record in fired],
                         [f"{aid}:r1:2"])
        self.assertIsNone(self.state(aid)["pending"])

    @need_mncs
    def test_restart_keeps_future_semantics(self) -> None:
        aid = self.define(self.schedule_def(
            "steady", {"kind": "interval", "anchor_ms": 0,
                       "period_ms": 1_000}))
        self.tick(1_500)
        before = self.state(aid)
        # "Restart": fresh engine view over the same directory.
        report = self.tick(2_500)
        self.assertEqual(report["evaluated"][0]["decision"], "fired")
        self.assertEqual(report["evaluated"][0]["occurrence_id"],
                         f"{aid}:r1:3")
        self.assertEqual(self.state(aid)["prev_seq"], 3)
        self.assertGreaterEqual(self.state(aid)["prev_seq"],
                                before["prev_seq"])


class StaleAndAuthTests(EngineCase):
    @need_mncs
    def test_changed_program_blocks_once_then_revalidates(self) -> None:
        program = self.tmp / "mutable.mncs"
        shutil.copy(FIXTURES / "tiny.mncs", program)
        target = self.ping_target(program=str(program), libraries=LIBRARIES,
                                  module="mncs.automation.fixture.tiny.v1")
        aid = self.define(self.schedule_def("mutable", {
            "kind": "interval", "anchor_ms": 0, "period_ms": 1_000},
            target=target,
            granted_capabilities=["call:execute"]))
        program.write_text(program.read_text(encoding="utf-8") + "\n",
                           encoding="utf-8")
        first = self.tick(1_500)
        self.assertEqual(first["evaluated"][0]["decision"], "blocked")
        second = self.tick(1_600)
        blocked = [record for record in self.occurrences(aid)
                   if record["decision"] == "blocked"]
        self.assertEqual(len(blocked), 1)
        paths = store.ensure(self.state_dir)
        definition = store.read_json(
            paths["definitions"] / f"{aid}.json")
        definition["target_accepted_digest"] = targets.program_digest(
            str(program))
        definition["revision"] += 1
        store.write_json(paths["definitions"] / f"{aid}.json", definition)
        # Blocked time accrues no history: the first tick after
        # revalidation aligns instead of backfilling.
        self.assertEqual(self.tick(1_700)["evaluated"][0]["decision"],
                         "wait")
        third = self.tick(2_700)
        self.assertEqual(third["evaluated"][0]["decision"], "fired")
        self.assertEqual(third["evaluated"][0]["occurrence_id"],
                         f"{aid}:r2:3")

    @need_mncs
    def test_missing_capability_blocks(self) -> None:
        aid = self.define(self.schedule_def(
            "unauthorized", {"kind": "interval", "anchor_ms": 0,
                             "period_ms": 1_000},
            granted_capabilities=[]))
        report = self.tick(1_500)
        self.assertEqual(report["evaluated"][0]["decision"], "blocked")
        self.assertEqual(self.occurrences(aid)[0]["decision"], "blocked")

    @need_mncs
    def test_policy_can_deny_target_kind(self) -> None:
        aid = self.define(self.schedule_def(
            "denied", {"kind": "interval", "anchor_ms": 0,
                       "period_ms": 1_000}))
        policy = open_policy()
        policy["allowed_target_kinds"] = ["doctor"]
        report = engine.tick(state_dir=self.state_dir, mncs=MNCS,
                             now_ms=1_500, policy=policy)
        self.assertEqual(report["evaluated"][0]["decision"], "blocked")


class SerializationTests(EngineCase):
    @need_mncs
    def test_overlap_is_impossible_by_construction(self) -> None:
        # The engine holds the state lock across evaluate+execute, so a
        # second tick at the same instant is a dedup no-op, never a
        # second invocation. No overlap policy is needed single-node.
        aid = self.define(self.schedule_def(
            "serial", {"kind": "interval", "anchor_ms": 0,
                       "period_ms": 1_000}, misfire="fire-now"))
        self.tick(1_500)
        self.tick(2_500)
        self.tick(2_500)
        fired = [record for record in self.occurrences(aid)
                 if record["decision"] == "fired"]
        self.assertEqual(len(fired), 1)
        state = self.state(aid)
        self.assertNotIn("active", state)
        self.assertNotIn("coalesced", state)


class UpdateDeleteTests(EngineCase):
    @need_mncs
    def test_update_bumps_revision_and_restarts_history(self) -> None:
        import argparse
        from automation import cli
        aid = self.define(self.schedule_def(
            "evolving", {"kind": "interval", "anchor_ms": 0,
                         "period_ms": 1_000}))
        self.tick(1_500)
        self.tick(2_500)
        updated = self.schedule_def(
            "evolving", {"kind": "interval", "anchor_ms": 0,
                         "period_ms": 5_000})
        update_file = self.tmp / "update.json"
        update_file.write_text(json.dumps(updated), encoding="utf-8")
        args = argparse.Namespace(state_dir=str(self.state_dir),
                                  mncs=None, now_ms=3_000, name="evolving",
                                  file=str(update_file))
        self.assertEqual(cli.cmd_update(args), 0)
        report = self.tick(6_000)
        self.assertEqual(report["evaluated"][0]["decision"], "wait")
        report = self.tick(11_000)
        fired = [record for record in self.occurrences(aid)
                 if record["decision"] == "fired"]
        revisions = {record["occurrence_id"].split(":")[1] for record in fired}
        self.assertEqual(revisions, {"r1", "r2"})
        self.assertEqual(report["evaluated"][0]["occurrence_id"],
                         f"{aid}:r2:3")

    @need_mncs
    def test_delete_preserves_history(self) -> None:
        aid = self.define(self.schedule_def(
            "doomed", {"kind": "interval", "anchor_ms": 0,
                       "period_ms": 1_000}))
        self.tick(1_500)
        self.tick(2_500)
        paths = store.ensure(self.state_dir)
        (paths["definitions"] / f"{aid}.json").unlink()
        self.assertTrue(
            (paths["occurrences"] / f"{aid}.jsonl").exists())


class TimezoneTests(EngineCase):
    @need_mncs
    def test_daily_local_fires_on_local_days(self) -> None:
        # 2026-06-01 04:59 Alaska time (AKDT, UTC-8).
        before = 1_780_318_740_000
        aid = self.define(self.schedule_def(
            "anchorage-five", {"kind": "daily-local",
                               "tz": "America/Anchorage",
                               "hour": 5, "minute": 0}), now_ms=before)
        report = self.tick(before, dry_run=True)
        self.assertEqual(report["evaluated"][0]["decision"], "wait")
        # 2026-06-01 05:01 Alaska time.
        report = self.tick(before + 120_000)
        self.assertEqual(report["evaluated"][0]["decision"], "fired")
        occurrence = self.occurrences(aid)[-1]
        self.assertEqual(occurrence["scheduled_at_ms"], 1_780_318_800_000)
        serials = [record["seq"] for record in self.occurrences(aid)
                   if record["decision"] == "fired"]
        self.assertEqual(serials, sorted(serials))

    @need_mncs
    def test_daily_utc_uses_minute_of_day(self) -> None:
        aid = self.define(self.schedule_def(
            "utc-five", {"kind": "daily-utc", "hour": 5, "minute": 0}))
        report = self.tick(4_999 * 3_600_000, dry_run=True)
        self.assertEqual(report["evaluated"][0]["wakeup_ms"],
                         209 * 86_400_000 + 18_000_000)


class DstPolicyTests(unittest.TestCase):
    def test_spring_forward_gap_shifts_to_first_valid(self) -> None:
        import datetime
        from automation import tzbridge
        # 2026-03-08 02:30 does not exist in America/Anchorage.
        utc_ms, note = tzbridge.resolve_wall_time(
            "America/Anchorage", datetime.date(2026, 3, 8), 2, 30)
        self.assertEqual(note, "gap-shifted-forward")
        back = datetime.datetime.fromtimestamp(
            utc_ms / 1000, tz=datetime.timezone.utc).astimezone(
            __import__("zoneinfo").ZoneInfo("America/Anchorage"))
        self.assertEqual((back.hour, back.minute), (3, 0))

    def test_fall_back_overlap_takes_first(self) -> None:
        import datetime
        from zoneinfo import ZoneInfo
        from automation import tzbridge
        # 2026-11-01 01:30 happens twice; take the first (AKDT side).
        utc_ms, note = tzbridge.resolve_wall_time(
            "America/Anchorage", datetime.date(2026, 11, 1), 1, 30)
        self.assertEqual(note, "overlap-first")
        first = datetime.datetime(2026, 11, 1, 1, 30, fold=0,
                                  tzinfo=ZoneInfo("America/Anchorage"))
        self.assertEqual(utc_ms, int(first.timestamp() * 1000))

    def test_daily_recurrence_crosses_spring_forward(self) -> None:
        import datetime
        from automation import tzbridge
        # Day before the gap, 02:30 local: next firing shifts to 03:00.
        before = int(datetime.datetime(
            2026, 3, 7, 3, 0,
            tzinfo=__import__("zoneinfo").ZoneInfo(
                "America/Anchorage")).timestamp() * 1000)
        resolved = tzbridge.next_daily_local(
            tz_name="America/Anchorage", hour=2, minute=30,
            after_utc_ms=before)
        self.assertEqual(resolved["note"], "gap-shifted-forward")
        self.assertEqual(resolved["local_date"], "2026-03-08")


class PropertyTests(EngineCase):
    @need_mncs
    def test_generated_schedules_stay_monotonic_and_bounded(self) -> None:
        random.seed(20260926)
        for trial in range(5):
            period = random.choice([1_000, 60_000, 3_600_000])
            bound = random.choice([0, 1, 3])
            aid = self.define(self.schedule_def(
                f"prop-{trial}", {"kind": "interval", "anchor_ms": 0,
                                  "period_ms": period},
                misfire="catch-up", catchup_bound=bound))
            now = 0
            for _ in range(3):
                now += random.choice([0, period // 2, period * 2,
                                      period * 5])
                self.tick(now)
            fired = [record["seq"] for record in self.occurrences(aid)
                     if record["decision"] == "fired"]
            self.assertEqual(fired, sorted(fired))
            self.assertEqual(len(set(
                record["occurrence_id"] for record in self.occurrences(aid)
                if record["decision"] == "fired"), ), len(fired))
            state = self.state(aid)
            self.assertGreaterEqual(state["prev_seq"],
                                    max(fired) if fired else 0)


def find_doctor() -> str | None:
    for candidate in (
        os.environ.get("MNCS_DOCTOR_BIN"),
        str(WORKSPACE / "mncs-doctor" / "target" / "debug" / "mncs-doctor"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


DOCTOR = find_doctor()


def need_doctor(test):
    return unittest.skipIf(DOCTOR is None, "mncs-doctor binary unavailable")(test)


class DoctorTargetTests(EngineCase):
    @need_doctor
    @need_mncs
    def test_readonly_doctor_check_handoff(self) -> None:
        os.environ["MNCS_DOCTOR_BIN"] = DOCTOR
        try:
            aid = self.define({
                "schema_version": codes.DEFINITION_SCHEMA,
                "name": "doctor-proof",
                "trigger": {"kind": "schedule",
                            "schedule": {"kind": "one-shot", "at_ms": 5_000}},
                "target": {"kind": "doctor", "root": str(REPO),
                           "libraries": LIBRARIES,
                           "timeout_s": 300},
                "granted_capabilities": ["doctor:read"]})
            report = self.tick(5_001)
            first = report["evaluated"][0]
            self.assertEqual(first["decision"], "fired")
            outcome = first["outcome"]
            self.assertEqual(outcome["status"], "ok")
            self.assertIn("finding_count", outcome)
            self.assertTrue(Path(outcome["result_artifact"]).is_file())
        finally:
            os.environ.pop("MNCS_DOCTOR_BIN", None)


def find_environment_repo() -> str | None:
    for candidate in (
        os.environ.get("MNCS_ENVIRONMENT_REPO"),
        str(WORKSPACE / "mncs-environment"),
    ):
        if candidate and Path(candidate, "scripts",
                              "mncs-env").is_file():
            return candidate
    return None


ENVIRONMENT_REPO = find_environment_repo()


def need_environment(test):
    return unittest.skipIf(ENVIRONMENT_REPO is None,
                           "mncs-environment checkout unavailable")(test)


class EnvironmentReconcileTargetTests(EngineCase):
    def test_target_defaults_to_reconcile_capability(self) -> None:
        clean = model.validate_target({
            "kind": "environment-reconcile",
            "environment_repo": "/x/mncs-environment",
            "state_dir": "/x/state"})
        self.assertEqual(clean["required_capabilities"],
                         ["environment:reconcile"])

    def test_policy_admits_and_denies_reconcile_kind(self) -> None:
        from automation import targets as targets_mod
        target = {"kind": "environment-reconcile",
                  "environment_repo": "/x/mncs-environment",
                  "state_dir": "/x/state", "workspace": "",
                  "timeout_s": 120,
                  "required_capabilities": ["environment:reconcile"]}
        policy = open_policy()
        policy["allowed_target_kinds"] = ["environment-reconcile"]
        targets_mod.check_policy(target=target, policy=policy,
                                 mncs_bin="mncs")
        policy["allowed_target_kinds"] = ["doctor"]
        with self.assertRaises(targets_mod.TargetError):
            targets_mod.check_policy(target=target, policy=policy,
                                     mncs_bin="mncs")

    @need_environment
    @need_mncs
    def test_scheduled_reconcile_fires_and_reports(self) -> None:
        assert ENVIRONMENT_REPO is not None
        policy = open_policy()
        policy["allowed_target_kinds"] = list(policy["allowed_target_kinds"])
        policy["allowed_target_kinds"].append("environment-reconcile")
        aid = self.define({
            "schema_version": codes.DEFINITION_SCHEMA,
            "name": "env-reconcile-proof",
            "trigger": {"kind": "schedule",
                        "schedule": {"kind": "one-shot", "at_ms": 5_000}},
            "target": {"kind": "environment-reconcile",
                       "environment_repo": ENVIRONMENT_REPO,
                       "state_dir": str(self.tmp / "env-state"),
                       "workspace": str(self.tmp),
                       "libraries": LIBRARIES,
                       "timeout_s": 120},
            "granted_capabilities": ["environment:reconcile"]})
        from automation import engine as engine_mod
        report = engine_mod.tick(state_dir=self.state_dir, mncs=MNCS,
                                 now_ms=5_001, policy=policy)
        first = report["evaluated"][0]
        self.assertEqual(first["decision"], "fired")
        outcome = first["outcome"]
        self.assertEqual(outcome["status"], "ok")
        self.assertIn("observations", outcome)
        self.assertTrue(Path(outcome["result_artifact"]).is_file())
        self.assertEqual(self.occurrences(aid)[0]["decision"], "fired")


class NativeSuiteTests(unittest.TestCase):
    @need_mncs
    def test_native_suites_pass(self) -> None:
        for module in ("time", "schedule", "trigger", "evaluation",
                       "reconcile", "projection"):
            with self.subTest(module=module):
                result = native.run_test_suite(
                    mncs=MNCS,
                    program=str(REPO / "native" / "mncs" / "automation" /
                                f"{module}.mncs"),
                    libraries=LIBRARIES, timeout_s=300)
                self.assertEqual(result.get("classification"), "passed",
                                 result)


if __name__ == "__main__":
    unittest.main()
