"""Automation definitions: schema, validation, identities, typed arguments.

A definition is a JSON document (``mncs.automation-definition/1``) created
by agents or operators through structured fields -- never cron strings or
shell glue. Validation is structural and total: unknown fields are
rejected so definitions cannot silently carry dead policy.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .codes import (
    CONDITION_KINDS,
    DEFINITION_SCHEMA,
    EDGES,
    LIFECYCLES,
    MISFIRE_POLICIES,
    SCHEDULE_KINDS,
    TARGET_KINDS,
)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_I64 = 2**63 - 1


class DefinitionError(ValueError):
    pass


def automation_id(name: str) -> str:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"auto-{digest}"


def occurrence_id(aid: str, revision: int, seq: int) -> str:
    return f"{aid}:r{revision}:{seq}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DefinitionError(message)


def _ms(value: Any, field: str, *, minimum: int = 0) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool),
             f"{field} must be an integer")
    _require(minimum <= value <= MAX_I64, f"{field} out of range")
    return value


def validate_schedule(schedule: Any) -> dict[str, Any]:
    _require(isinstance(schedule, dict), "schedule must be an object")
    kind = schedule.get("kind")
    _require(kind in SCHEDULE_KINDS, f"unknown schedule kind {kind!r}")
    _require(set(schedule) <= {"kind", "at_ms", "anchor_ms", "period_ms",
                               "hour", "minute", "tz"},
             "unknown schedule field")
    if kind == "one-shot":
        return {"kind": kind, "at_ms": _ms(schedule.get("at_ms"), "at_ms")}
    if kind == "interval":
        period = _ms(schedule.get("period_ms"), "period_ms", minimum=1)
        return {"kind": kind,
                "anchor_ms": _ms(schedule.get("anchor_ms"), "anchor_ms"),
                "period_ms": period}
    if kind == "daily-utc":
        hour = schedule.get("hour")
        minute = schedule.get("minute")
        _require(isinstance(hour, int) and 0 <= hour <= 23, "hour 0..23")
        _require(isinstance(minute, int) and 0 <= minute <= 59, "minute 0..59")
        return {"kind": kind, "hour": hour, "minute": minute}
    tz = schedule.get("tz")
    _require(isinstance(tz, str) and tz and len(tz) <= 64,
             "tz must be an IANA timezone name")
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(tz)
    except Exception as error:
        raise DefinitionError(f"unknown timezone {tz!r}: {error}")
    hour = schedule.get("hour")
    minute = schedule.get("minute")
    _require(isinstance(hour, int) and 0 <= hour <= 23, "hour 0..23")
    _require(isinstance(minute, int) and 0 <= minute <= 59, "minute 0..59")
    return {"kind": kind, "tz": tz, "hour": hour, "minute": minute}


def validate_condition(condition: Any) -> dict[str, Any]:
    _require(isinstance(condition, dict), "condition must be an object")
    kind = condition.get("kind")
    _require(kind in CONDITION_KINDS, f"unknown condition kind {kind!r}")
    if kind == "test-verdict":
        program = condition.get("program")
        _require(isinstance(program, str) and program, "program path required")
        libraries = condition.get("libraries", [])
        _require(isinstance(libraries, list) and
                 all(isinstance(item, str) for item in libraries),
                 "libraries must be a string list")
        identities = condition.get("test_identities", [])
        _require(isinstance(identities, list) and
                 all(isinstance(item, str) for item in identities),
                 "test_identities must be a string list")
        timeout = condition.get("timeout_s", 300)
        _require(isinstance(timeout, int) and 1 <= timeout <= 3600,
                 "timeout_s 1..3600")
        _require(set(condition) <= {"kind", "program", "libraries",
                                    "test_identities", "timeout_s"},
                 "unknown condition field")
        return {"kind": kind, "program": program, "libraries": libraries,
                "test_identities": identities, "timeout_s": timeout}
    path = condition.get("path")
    _require(isinstance(path, str) and path, "path required")
    _require(set(condition) <= {"kind", "path"}, "unknown condition field")
    return {"kind": kind, "path": path}


def validate_target(target: Any) -> dict[str, Any]:
    _require(isinstance(target, dict), "target must be an object")
    kind = target.get("kind")
    _require(kind in TARGET_KINDS, f"unknown target kind {kind!r}")
    required = target.get("required_capabilities", [])
    _require(isinstance(required, list) and
             all(isinstance(item, str) for item in required),
             "required_capabilities must be a string list")
    if kind == "mncs-test":
        _require(set(target) <= {"kind", "program", "libraries",
                                 "test_identities", "timeout_s",
                                 "required_capabilities"},
                 "unknown target field")
        program = target.get("program")
        _require(isinstance(program, str) and program, "program path required")
        libraries = target.get("libraries", [])
        _require(isinstance(libraries, list), "libraries must be a list")
        identities = target.get("test_identities", [])
        _require(isinstance(identities, list), "test_identities must be a list")
        timeout = target.get("timeout_s", 300)
        _require(isinstance(timeout, int) and 1 <= timeout <= 3600,
                 "timeout_s 1..3600")
        return {"kind": kind, "program": program, "libraries": libraries,
                "test_identities": identities, "timeout_s": timeout,
                "required_capabilities": required or ["test:execute"]}
    if kind == "mncs-call":
        _require(set(target) <= {"kind", "program", "libraries", "module",
                                 "function", "args", "grants", "timeout_s",
                                 "required_capabilities"},
                 "unknown target field")
        for field in ("program", "module", "function"):
            _require(isinstance(target.get(field), str) and target.get(field),
                     f"{field} required")
        args = target.get("args", [])
        _require(isinstance(args, list), "args must be a typed value list")
        for arg in args:
            validate_typed_value(arg)
        grants = target.get("grants", {})
        _require(isinstance(grants, dict), "grants must be an object")
        _require(set(grants) <= {"process", "structured", "fs"},
                 "unknown grant class")
        timeout = target.get("timeout_s", 120)
        _require(isinstance(timeout, int) and 1 <= timeout <= 3600,
                 "timeout_s 1..3600")
        return {"kind": kind, "program": target["program"],
                "libraries": target.get("libraries", []),
                "module": target["module"], "function": target["function"],
                "args": args, "grants": grants, "timeout_s": timeout,
                "required_capabilities": required or ["call:execute"]}
    _require(set(target) <= {"kind", "root", "changed_paths", "extra_args",
                             "timeout_s", "required_capabilities"},
             "unknown target field")
    root = target.get("root")
    _require(isinstance(root, str) and root, "root required")
    changed = target.get("changed_paths", [])
    _require(isinstance(changed, list), "changed_paths must be a list")
    extra = target.get("extra_args", [])
    _require(isinstance(extra, list), "extra_args must be a list")
    timeout = target.get("timeout_s", 300)
    _require(isinstance(timeout, int) and 1 <= timeout <= 3600,
             "timeout_s 1..3600")
    return {"kind": kind, "root": root, "changed_paths": changed,
            "extra_args": extra, "timeout_s": timeout,
            "required_capabilities": required or ["doctor:read"]}


def validate_typed_value(value: Any) -> None:
    """Validate one HostExecutionValue wire value (no shell strings)."""
    _require(isinstance(value, dict) and len(value) == 1,
             f"typed arg must be a single-tag object, got {value!r}")
    tag, body = next(iter(value.items()))
    _require(isinstance(body, dict), f"typed arg {tag} needs an object body")
    if tag == "integer":
        _require(isinstance(body.get("value"), int), "integer needs value")
    elif tag == "boolean":
        _require(isinstance(body.get("value"), bool), "boolean needs value")
    elif tag == "finite":
        _require(isinstance(body.get("type"), str) and
                 isinstance(body.get("variant"), str),
                 "finite needs type and variant names")
        _require(set(body) <= {"type", "variant", "payload"},
                 "unknown finite field")
    elif tag == "record":
        _require(isinstance(body.get("type"), str) and
                 isinstance(body.get("fields"), dict),
                 "record needs type and fields")
        for field in body["fields"].values():
            validate_typed_value(field)
    elif tag == "sequence":
        _require(isinstance(body.get("values"), list), "sequence needs values")
        for item in body["values"]:
            validate_typed_value(item)
    else:
        raise DefinitionError(f"unknown typed value tag {tag!r}")


def arg_int(value: int) -> dict[str, Any]:
    return {"integer": {"value": value}}


def arg_bool(value: bool) -> dict[str, Any]:
    return {"boolean": {"value": value}}


def arg_finite(type_name: str, variant: str) -> dict[str, Any]:
    return {"finite": {"type": type_name, "variant": variant}}


def validate_definition(document: Any, *, now_ms: int = 0) -> dict[str, Any]:
    _require(isinstance(document, dict), "definition must be an object")
    _require(document.get("schema_version") == DEFINITION_SCHEMA,
             f"schema_version must be {DEFINITION_SCHEMA}")
    name = document.get("name")
    _require(isinstance(name, str) and NAME_RE.match(name),
             "name must match [a-z0-9][a-z0-9-]{0,63}")
    allowed = {"schema_version", "name", "revision", "lifecycle", "trigger",
               "target", "granted_capabilities", "misfire", "catchup_bound",
               "history_bound", "expires_ms", "created_ms",
               "updated_ms", "target_accepted_digest"}
    _require(set(document) <= allowed, "unknown definition field")
    lifecycle = document.get("lifecycle", "active")
    _require(lifecycle in ("active", "paused"),
             "new definitions start active or paused")
    trigger = document.get("trigger")
    _require(isinstance(trigger, dict), "trigger required")
    tkind = trigger.get("kind")
    _require(tkind in ("schedule", "watch"), "trigger kind schedule|watch")
    if tkind == "schedule":
        _require(set(trigger) <= {"kind", "schedule"}, "unknown trigger field")
        clean_trigger: dict[str, Any] = {
            "kind": "schedule",
            "schedule": validate_schedule(trigger.get("schedule")),
        }
    else:
        _require(set(trigger) <= {"kind", "watch"}, "unknown trigger field")
        watch = trigger.get("watch")
        _require(isinstance(watch, dict), "watch required")
        _require(set(watch) <= {"cadence_ms", "edge", "condition"},
                 "unknown watch field")
        cadence = _ms(watch.get("cadence_ms"), "cadence_ms", minimum=1000)
        _require(watch.get("edge") in EDGES, "edge rising|while-true")
        clean_trigger = {"kind": "watch",
                         "watch": {"cadence_ms": cadence,
                                   "edge": watch["edge"],
                                   "condition": validate_condition(
                                       watch.get("condition"))}}
    target = validate_target(document.get("target"))
    granted = document.get("granted_capabilities", [])
    _require(isinstance(granted, list) and
             all(isinstance(item, str) for item in granted),
             "granted_capabilities must be a string list")
    misfire = document.get("misfire", "catch-up")
    _require(misfire in MISFIRE_POLICIES, "unknown misfire policy")
    bound = document.get("catchup_bound", 3)
    _require(isinstance(bound, int) and 0 <= bound <= 100000,
             "catchup_bound 0..100000")
    history = document.get("history_bound", 64)
    _require(isinstance(history, int) and 1 <= history <= 10000,
             "history_bound 1..10000")
    expires = document.get("expires_ms")
    if expires is not None:
        _ms(expires, "expires_ms", minimum=1)
    accepted = document.get("target_accepted_digest")
    if accepted is not None:
        _require(isinstance(accepted, str) and accepted.startswith("sha256:"),
                 "target_accepted_digest must be a sha256 digest")
    return {
        "schema_version": DEFINITION_SCHEMA,
        "name": name,
        "revision": int(document.get("revision", 1)),
        "lifecycle": lifecycle,
        "trigger": clean_trigger,
        "target": target,
        "granted_capabilities": sorted(set(granted)),
        "misfire": misfire,
        "catchup_bound": bound,
        "history_bound": history,
        "expires_ms": expires,
        "created_ms": int(document.get("created_ms", now_ms)),
        "updated_ms": int(document.get("updated_ms", now_ms)),
        "target_accepted_digest": accepted,
    }


def fresh_state(aid: str, revision: int) -> dict[str, Any]:
    from .codes import STATE_SCHEMA
    return {
        "schema_version": STATE_SCHEMA,
        "automation": aid,
        "definition_revision": revision,
        "prev_seq": 0,
        "last_at_ms": 0,
        "last_eval_ms": 0,
        "last_condition": 0,
        "last_observed": None,
        "pending": None,
        "wakeup_ms": 0,
        "eval_errors": 0,
        "last_block_reason": None,
    }
