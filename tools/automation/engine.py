"""Evaluation engine: decide when, hand off how, record what happened.

One engine pass (`tick`) evaluates every known definition at `now_ms`:

1. Load definition + state (active only; terminal lifecycles are skipped).
2. Reconcile expiry, target freshness (digest vs accepted), and
   authorization (required vs granted, plus global policy). A mismatch
   records ONE blocked marker on transition, then stays silent -- a stale
   target must not spam an occurrence per tick.
3. Concurrency: none needed -- the engine holds the state lock across
   evaluate+execute, so overlap is structurally impossible single-node.
4. Resolve the native descriptor (daily-local via the timezone bridge,
   everything else direct) and call native `evaluate_tick`.
5. On fire: deduplicate by occurrence identity (replay safety), persist
   `pending`, execute the typed target synchronously, record the outcome,
   advance `prev_seq`/`last_at_ms` from the native decision, record any
   missed range, prune history to bound.
6. On wait: persist `wakeup_ms`, condition observations, `last_eval_ms`.

Ticks are idempotent: same state + same instant = same decision, because
the decision is native and persistence only moves forward (prev_seq,
last_at_ms, occurrence log appends).

Recovery (`recover`): any `pending` invocation without an outcome becomes
an `unknown` outcome marker -- at-most-once across crashes. The
occurrence is never re-fired; the next tick proceeds from stored seqs.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from . import conditions, native, store, targets, tzbridge
from .codes import (
    COND_FALSE,
    COND_UNKNOWN,
    EDGE_RISING,
    EDGE_WHILE_TRUE,
    MISFIRE_CODES,
    REASON_NAMES,
    REASON_STALE_TARGET,
    REASON_TERMINAL,
    SCHED_DAILY_LOCAL,
    SCHED_DAILY_UTC,
    SCHED_INTERVAL,
    SCHED_PINNED,
    TRIG_SCHEDULE,
    TRIG_WATCH,
)
from .model import automation_id, fresh_state, occurrence_id


def _now_ms() -> int:
    return int(time.time() * 1000)


def _missed_empty(missed_from: int, missed_to: int) -> bool:
    return missed_from > missed_to


def _record_occurrence(paths: dict[str, Path], aid: str, record: dict[str, Any],
                       history_bound: int) -> None:
    store.append_jsonl(paths["occurrences"] / f"{aid}.jsonl", record)
    store.prune_jsonl(paths["occurrences"] / f"{aid}.jsonl", history_bound)


def _schedule_descriptor(schedule: dict[str, Any], *, last_at_ms: int,
                         now_ms: int,
                         created_ms: int = 0) -> tuple[int, int, int]:
    """Return (kind_code, p1, p2) for native evaluation."""
    kind = schedule["kind"]
    if kind == "one-shot":
        return SCHED_PINNED, int(schedule["at_ms"]), 1
    if kind == "interval":
        return SCHED_INTERVAL, int(schedule["anchor_ms"]), int(
            schedule["period_ms"])
    if kind == "daily-utc":
        tod = (int(schedule["hour"]) * 60 + int(schedule["minute"])) * 60000
        return SCHED_DAILY_UTC, tod, 0
    # Local days resolve against creation, never the epoch: history
    # before the definition existed must not produce pins. Pin seqs are
    # 1-based local-day numbers; serial 0 is reserved as "no sequence".
    floor = max(int(last_at_ms), int(created_ms))
    resolved = tzbridge.next_daily_local(
        tz_name=schedule["tz"], hour=int(schedule["hour"]),
        minute=int(schedule["minute"]), after_utc_ms=floor)
    return SCHED_PINNED, int(resolved["at_utc_ms"]), int(
        resolved["serial"]) + 1


def _target_program(target: dict[str, Any]) -> str | None:
    if target["kind"] in ("mncs-test", "mncs-call"):
        return target.get("program")
    return None


def _libraries_for(definition: dict[str, Any]) -> list[str]:
    target = definition["target"]
    return list(target.get("libraries", []))


def recover(*, state_dir: Path) -> list[dict[str, Any]]:
    """Reconcile crash-interrupted invocations. Returns recovery markers."""
    paths = store.ensure(state_dir)
    markers: list[dict[str, Any]] = []
    with store.locked(state_dir):
        markers.extend(_recover_unlocked(paths))
    return markers


def _recover_unlocked(paths: dict[str, Path]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for path in sorted(paths["definitions"].glob("*.json")):
        definition = store.read_json(path)
        if not isinstance(definition, dict):
            continue
        aid = automation_id(definition["name"])
        state_path = paths["state"] / f"{aid}.json"
        state = store.read_json(state_path)
        if not isinstance(state, dict) or not state.get("pending"):
            continue
        pending = state["pending"]
        marker = {"occurrence_id": pending["occurrence_id"],
                  "seq": pending.get("seq"),
                  "definition_revision": state.get("definition_revision"),
                  "trigger_kind": pending.get("trigger_kind", "schedule"),
                  "evaluated_ms": pending.get("started_ms"),
                  "decision": "unknown",
                  "reason": None,
                  "reason_name": "recovery-pending-without-outcome",
                  "target": {"kind": definition["target"]["kind"]},
                  "invocation_id": pending.get("invocation_id"),
                  "outcome": {"status": "unknown",
                              "reason": "interrupted-before-outcome"},
                  "condition_value": None}
        _record_occurrence(paths, aid, marker, definition["history_bound"])
        state["pending"] = None
        store.write_json(state_path, state)
        markers.append(marker)
    return markers


def _check_target_fresh(*, definition: dict[str, Any]) -> str | None:
    """Return an error string when the live target digest moved, else None."""
    target = definition["target"]
    program = _target_program(target)
    if program is None:
        return None
    try:
        live = targets.program_digest(program)
    except targets.TargetError as error:
        return str(error)
    accepted = definition.get("target_accepted_digest")
    if accepted != live:
        return (f"target program changed since definition "
                f"(accepted {accepted}, live {live}); "
                f"run revalidate")
    return None


def tick(*, state_dir: Path, mncs: str, now_ms: int | None = None,
         dry_run: bool = False, execute: bool = True,
         policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate every definition once at `now_ms`. Returns a pass report."""
    paths = store.ensure(state_dir)
    now = int(now_ms) if now_ms is not None else _now_ms()
    policy = policy or store.read_policy(state_dir)
    report: dict[str, Any] = {"now_ms": now, "dry_run": dry_run,
                              "evaluated": [], "errors": [],
                              "recovered": []}
    with store.locked(state_dir):
        if not dry_run:
            report["recovered"] = _recover_unlocked(paths)
        for path in sorted(paths["definitions"].glob("*.json")):
            definition = store.read_json(path)
            if not isinstance(definition, dict):
                continue
            try:
                outcome = _tick_one(paths=paths, definition=definition,
                                    mncs=mncs, now_ms=now, dry_run=dry_run,
                                    execute=execute, policy=policy)
                report["evaluated"].append(outcome)
            except Exception as error:  # never let one automation kill a pass
                aid = automation_id(definition.get("name", "?"))
                report["errors"].append({"automation": aid,
                                         "error": str(error)[:300]})
    return report


def _tick_one(*, paths: dict[str, Path], definition: dict[str, Any],
              mncs: str, now_ms: int, dry_run: bool, execute: bool,
              policy: dict[str, Any]) -> dict[str, Any]:
    aid = automation_id(definition["name"])
    state_path = paths["state"] / f"{aid}.json"
    state = store.read_json(state_path)
    if not isinstance(state, dict):
        state = fresh_state(aid, definition["revision"])
    summary: dict[str, Any] = {"automation": aid, "name": definition["name"],
                               "lifecycle": definition["lifecycle"]}
    lifecycle = definition["lifecycle"]
    if lifecycle != "active":
        summary["decision"] = "skipped"
        summary["reason_name"] = f"lifecycle-{lifecycle}"
        return summary
    expires = definition.get("expires_ms")
    if expires is not None and now_ms >= expires:
        summary["decision"] = "terminal"
        summary["reason_name"] = "expired"
        summary["lifecycle"] = "expired"
        if dry_run:
            return summary
        definition["lifecycle"] = "expired"
        store.write_json(paths["definitions"] / f"{aid}.json", definition)
        _record_occurrence(paths, aid, {
            "occurrence_id": f"{aid}:r{definition['revision']}:terminal",
            "seq": None, "definition_revision": definition["revision"],
            "trigger_kind": definition["trigger"]["kind"],
            "evaluated_ms": now_ms, "decision": "terminal",
            "reason": REASON_TERMINAL, "reason_name": "expired",
            "target": {"kind": definition["target"]["kind"]},
            "invocation_id": None, "outcome": None, "condition_value": None},
            definition["history_bound"])
        return summary

    target = definition["target"]
    # Freshness + authorization before any semantic work.
    block = _check_target_fresh(definition=definition)
    if block is None:
        try:
            targets.check_policy(target=target, policy=policy, mncs_bin=mncs)
            targets.check_authorization(
                target=target, granted=definition["granted_capabilities"])
        except targets.TargetError as error:
            block = str(error)
    if block is not None:
        return _blocked(paths, definition, state, state_path, now_ms,
                        block, summary, dry_run=dry_run)

    # No concurrency gate: the engine holds the state lock across
    # evaluate+execute, so two invocations of one automation cannot
    # overlap on this node by construction. Overlap policy would only
    # become meaningful with async executors (explicitly out of scope).
    trigger = definition["trigger"]
    libraries = _libraries_for(definition)
    if trigger["kind"] == "schedule":
        return _tick_schedule(paths=paths, definition=definition, state=state,
                              state_path=state_path, mncs=mncs, now_ms=now_ms,
                              dry_run=dry_run, execute=execute,
                              libraries=libraries, summary=summary)
    return _tick_watch(paths=paths, definition=definition, state=state,
                       state_path=state_path, mncs=mncs, now_ms=now_ms,
                       dry_run=dry_run, execute=execute, libraries=libraries,
                       summary=summary)


def _blocked(paths: dict[str, Path], definition: dict[str, Any],
             state: dict[str, Any], state_path: Path, now_ms: int,
             reason_text: str, summary: dict[str, Any],
             dry_run: bool = False) -> dict[str, Any]:
    aid = automation_id(definition["name"])
    summary["decision"] = "blocked"
    summary["reason_name"] = "stale-target"
    summary["reason"] = REASON_STALE_TARGET
    summary["detail"] = reason_text
    if dry_run:
        return summary
    if state.get("last_block_reason") != reason_text:
        # Blocked means refused-to-evaluate: last_eval_ms is untouched so
        # first-evaluation status (and post-recovery catch-up) survives.
        state["last_block_reason"] = reason_text
        store.write_json(state_path, state)
        _record_occurrence(paths, aid, {
            "occurrence_id": f"{aid}:r{definition['revision']}:blocked",
            "seq": None, "definition_revision": definition["revision"],
            "trigger_kind": definition["trigger"]["kind"],
            "evaluated_ms": now_ms, "decision": "blocked",
            "reason": REASON_STALE_TARGET, "reason_name": "stale-target",
            "target": {"kind": definition["target"]["kind"]},
            "invocation_id": None,
            "outcome": {"status": "unknown", "reason": "blocked",
                        "note": reason_text[:300]},
            "condition_value": None}, definition["history_bound"])
    return summary


def _native_args(*, kind: int, p1: int, p2: int, trig: int, edge: int,
                 prev_cond: int, cond: int, prev_seq: int, last_at: int,
                 now: int, misfire: int, bound: int,
                 first: int = 0) -> dict[str, int]:
    return {"sched_kind": kind, "p1": p1, "p2": p2, "trig_kind": trig,
            "edge": edge, "prev_cond": prev_cond, "cond": cond,
            "prev_seq": prev_seq, "last_at_ms": last_at, "now_ms": now,
            "misfire": misfire, "bound": bound, "first": first}


def _is_first(state: dict[str, Any]) -> int:
    """1 on a schedule's first evaluation ever (no history to catch up)."""
    if int(state.get("prev_seq", 0)) == 0 and not state.get("last_eval_ms"):
        return 1
    return 0


def _tick_schedule(*, paths: dict[str, Path], definition: dict[str, Any],
                   state: dict[str, Any], state_path: Path, mncs: str,
                   now_ms: int, dry_run: bool, execute: bool,
                   libraries: list[str],
                   summary: dict[str, Any]) -> dict[str, Any]:
    aid = automation_id(definition["name"])
    schedule = definition["trigger"]["schedule"]
    kind, p1, p2 = _schedule_descriptor(
        schedule, last_at_ms=int(state.get("last_at_ms", 0)), now_ms=now_ms,
        created_ms=int(definition.get("created_ms", 0)))
    misfire = MISFIRE_CODES[definition["misfire"]]
    try:
        decision = native.evaluate_tick(
            mncs=mncs, libraries=libraries,
            **_native_args(kind=kind, p1=p1, p2=p2, trig=TRIG_SCHEDULE,
                           edge=EDGE_RISING,
                           prev_cond=int(state.get("last_condition", 0)),
                           cond=COND_FALSE, prev_seq=int(
                               state.get("prev_seq", 0)),
                           last_at=int(state.get("last_at_ms", 0)),
                           now=now_ms, misfire=misfire,
                           bound=int(definition["catchup_bound"]),
                           first=_is_first(state)))
    except native.NativeError as error:
        state["eval_errors"] = int(state.get("eval_errors", 0)) + 1
        if not dry_run:
            store.write_json(state_path, state)
        summary["decision"] = "error"
        summary["reason_name"] = "evaluation-unreachable"
        summary["detail"] = str(error)[:200]
        return summary
    reason_name = REASON_NAMES.get(int(decision["reason"]), "unknown")
    summary["reason"] = int(decision["reason"])
    summary["reason_name"] = reason_name
    summary["wakeup_ms"] = int(decision["wakeup_ms"])
    if dry_run:
        summary["decision"] = "would-fire" if decision["fire"] else "wait"
        summary["seq"] = int(decision["seq"])
        return summary
    persist = execute
    state["last_eval_ms"] = now_ms
    state["wakeup_ms"] = int(decision["wakeup_ms"])
    state["eval_errors"] = 0
    # Record missed ranges first (history, never executed). Observing
    # without executing must not advance firing state, or occurrences
    # would be lost before any real invocation.
    if persist and not _missed_empty(int(decision["missed_from"]),
                                     int(decision["missed_to"])):
        missed_from = int(decision["missed_from"])
        missed_to = int(decision["missed_to"])
        _record_occurrence(paths, aid, {
            "occurrence_id": (f"{aid}:r{definition['revision']}:"
                              f"missed-{missed_from}-{missed_to}"),
            "seq": None,
            "definition_revision": definition["revision"],
            "trigger_kind": "schedule",
            "evaluated_ms": now_ms, "decision": "missed",
            "reason": int(decision["reason"]),
            "reason_name": reason_name,
            "missed_from": missed_from, "missed_to": missed_to,
            "missed_count": missed_to - missed_from + 1,
            "target": {"kind": definition["target"]["kind"]},
            "invocation_id": None, "outcome": None,
            "condition_value": None}, definition["history_bound"])
    if not decision["fire"]:
        if not dry_run:
            # Wait-path advancement (including first-tick alignment)
            # is tracking, not acting: it persists in observe mode too.
            state["prev_seq"] = int(decision["new_prev"])
            store.write_json(state_path, state)
        summary["decision"] = "wait"
        return summary
    if not execute:
        store.write_json(state_path, state)
        summary["decision"] = "would-fire"
        summary["seq"] = int(decision["seq"])
        summary["occurrence_id"] = occurrence_id(
            aid, definition["revision"], int(decision["seq"]))
        return summary
    return _fire(paths=paths, definition=definition, state=state,
                 state_path=state_path, mncs=mncs, now_ms=now_ms,
                 execute=True, libraries=libraries, summary=summary,
                 seq=int(decision["seq"]), at_ms=int(decision["at_ms"]),
                 new_prev=int(decision["new_prev"]),
                 trigger_kind="schedule", condition_value=None,
                 scheduled_at_ms=int(decision["at_ms"]))


def _tick_watch(*, paths: dict[str, Path], definition: dict[str, Any],
                state: dict[str, Any], state_path: Path, mncs: str,
                now_ms: int, dry_run: bool, execute: bool,
                libraries: list[str],
                summary: dict[str, Any]) -> dict[str, Any]:
    aid = automation_id(definition["name"])
    watch = definition["trigger"]["watch"]
    edge = EDGE_RISING if watch["edge"] == "rising" else EDGE_WHILE_TRUE
    cond, observation = _evaluate_condition(
        definition, state, mncs, libraries)
    summary["condition"] = cond
    try:
        decision = native.evaluate_tick(
            mncs=mncs, libraries=libraries,
            **_native_args(kind=SCHED_PINNED, p1=0, p2=0, trig=TRIG_WATCH,
                           edge=edge,
                           prev_cond=int(state.get("last_condition", 0)),
                           cond=cond, prev_seq=int(
                               state.get("prev_seq", 0)),
                           last_at=int(state.get("last_at_ms", 0)),
                           now=now_ms, misfire=MISFIRE_CODES["fire-now"],
                           bound=0))
    except native.NativeError as error:
        state["eval_errors"] = int(state.get("eval_errors", 0)) + 1
        if not dry_run:
            store.write_json(state_path, state)
        summary["decision"] = "error"
        summary["reason_name"] = "evaluation-unreachable"
        summary["detail"] = str(error)[:200]
        return summary
    reason_name = REASON_NAMES.get(int(decision["reason"]), "unknown")
    summary["reason"] = int(decision["reason"])
    summary["reason_name"] = reason_name
    summary["wakeup_ms"] = now_ms + int(watch["cadence_ms"])
    if dry_run:
        summary["decision"] = "would-fire" if decision["fire"] else "wait"
        return summary
    state["last_eval_ms"] = now_ms
    state["last_condition"] = cond
    if observation is not None:
        state["last_observed"] = observation
    state["wakeup_ms"] = now_ms + int(watch["cadence_ms"])
    state["eval_errors"] = 0
    if not decision["fire"]:
        store.write_json(state_path, state)
        summary["decision"] = "wait"
        return summary
    if not execute:
        store.write_json(state_path, state)
        summary["decision"] = "would-fire"
        summary["seq"] = int(decision["seq"])
        return summary
    return _fire(paths=paths, definition=definition, state=state,
                 state_path=state_path, mncs=mncs, now_ms=now_ms,
                 execute=True, libraries=libraries, summary=summary,
                 seq=int(decision["seq"]), at_ms=now_ms,
                 new_prev=int(decision["new_prev"]),
                 trigger_kind="watch", condition_value=cond,
                 scheduled_at_ms=now_ms)


def _evaluate_condition(definition: dict[str, Any], state: dict[str, Any],
                        mncs: str, libraries: list[str]
                        ) -> tuple[int, dict[str, Any] | None]:
    condition = definition["trigger"]["watch"]["condition"]
    if condition["kind"] == "test-verdict":
        value, evidence = conditions.evaluate_test_verdict(
            mncs=mncs, program=condition["program"],
            libraries=libraries + condition.get("libraries", []),
            test_identities=condition.get("test_identities") or None,
            timeout_s=condition.get("timeout_s", 300))
        return value, {"kind": "test-verdict", **evidence}
    value, observation = conditions.evaluate_path_changed(
        path=condition["path"],
        last_observed=state.get("last_observed"),
        allowed_roots=[])
    if observation is None:
        return value, {"kind": "path-changed", "unreadable": True}
    return value, {"kind": "path-changed", **observation}


def _fire(*, paths: dict[str, Path], definition: dict[str, Any],
          state: dict[str, Any], state_path: Path, mncs: str, now_ms: int,
          execute: bool, libraries: list[str], summary: dict[str, Any],
          seq: int, at_ms: int, new_prev: int, trigger_kind: str,
          condition_value: int | None,
          scheduled_at_ms: int) -> dict[str, Any]:
    aid = automation_id(definition["name"])
    occ = occurrence_id(aid, definition["revision"], seq)
    # Replay safety: an occurrence identity fires at most once ever.
    existing = [record for record in store.read_jsonl(
        paths["occurrences"] / f"{aid}.jsonl")
        if record.get("occurrence_id") == occ
        and record.get("decision") == "fired"]
    if existing:
        state["prev_seq"] = new_prev
        state["last_at_ms"] = at_ms
        store.write_json(state_path, state)
        summary["decision"] = "duplicate-suppressed"
        summary["reason_name"] = "already-fired"
        summary["reason"] = 2
        summary["occurrence_id"] = occ
        return summary
    invocation = f"{occ}:i0"
    outcome: dict[str, Any] | None = None
    if execute:
        state["pending"] = {"occurrence_id": occ, "seq": seq,
                            "trigger_kind": trigger_kind,
                            "invocation_id": invocation,
                            "started_ms": now_ms}
        store.write_json(state_path, state)
        try:
            outcome = targets.execute(
                target=definition["target"], mncs=mncs, libraries=libraries,
                artifacts_dir=paths["artifacts"] / aid,
                invocation_id=invocation.replace(":", "_"),
                timeout_s=None)
        except Exception as error:  # executor bug or timeout wrapper failure
            outcome = {"status": "unknown", "reason": "executor-error",
                       "note": str(error)[:300]}
        state["pending"] = None
    _record_occurrence(paths, aid, {
        "occurrence_id": occ, "seq": seq,
        "definition_revision": definition["revision"],
        "trigger_kind": trigger_kind,
        "scheduled_at_ms": scheduled_at_ms,
        "evaluated_ms": now_ms, "decision": "fired",
        "reason": 0, "reason_name": "due-fire",
        "target": {"kind": definition["target"]["kind"]},
        "invocation_id": invocation if execute else None,
        "outcome": outcome, "condition_value": condition_value},
        definition["history_bound"])
    state["prev_seq"] = new_prev
    state["last_at_ms"] = at_ms
    state["last_block_reason"] = None
    if (definition["trigger"]["kind"] == "schedule"
            and definition["trigger"]["schedule"]["kind"] == "one-shot"):
        definition["lifecycle"] = "completed"
        store.write_json(paths["definitions"] / f"{aid}.json", definition)
        summary["lifecycle"] = "completed"
    if trigger_kind == "watch":
        state["last_condition"] = condition_value
    store.write_json(state_path, state)
    summary["decision"] = "fired" if execute else "would-fire"
    summary["reason_name"] = "due-fire"
    summary["reason"] = 0
    summary["occurrence_id"] = occ
    summary["invocation_id"] = invocation if execute else None
    summary["outcome"] = outcome
    return summary


def next_wakeup(*, state_dir: Path, now_ms: int | None = None) -> int | None:
    """Earliest stored wakeup across definitions (None when idle)."""
    paths = store.ensure(state_dir)
    now = int(now_ms) if now_ms is not None else _now_ms()
    best: int | None = None
    for path in sorted(paths["definitions"].glob("*.json")):
        definition = store.read_json(path)
        if not isinstance(definition, dict):
            continue
        if definition.get("lifecycle") != "active":
            continue
        aid = automation_id(definition["name"])
        state = store.read_json(paths["state"] / f"{aid}.json") or {}
        wakeup = int(state.get("wakeup_ms", 0) or 0)
        if wakeup <= now:
            return now
        best = wakeup if best is None else min(best, wakeup)
    return best


def run_loop(*, state_dir: Path, mncs: str,
             policy: dict[str, Any] | None = None,
             max_idle_s: int = 60, max_passes: int | None = None) -> None:
    """Daemon: tick, sleep until the next wakeup, repeat.

    Sleeps are capped at `max_idle_s` so definition changes on disk are
    picked up promptly; there is no tight polling and no store scan per
    tick beyond the definition directory listing. Recovery runs inside
    every non-dry tick, so this preamble only covers the first pass.
    """
    passes = 0
    while True:
        report = tick(state_dir=state_dir, mncs=mncs, policy=policy)
        for error in report.get("errors", []):
            print(f"automation pass error: {error}", file=sys.stderr)
        passes += 1
        if max_passes is not None and passes >= max_passes:
            return
        wakeup = next_wakeup(state_dir=state_dir)
        now = _now_ms()
        delay = max_idle_s if wakeup is None else max(
            0, min(wakeup - now, max_idle_s * 1000)) / 1000
        time.sleep(delay)
