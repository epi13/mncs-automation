"""Condition evaluators: typed observations with tri-state results.

A condition answers with 0 (FALSE), 1 (TRUE), or 2 (UNKNOWN). UNKNOWN
covers backend errors, timeouts, and unreadable inputs -- it is a
first-class answer, never an exception, so the native edge logic can
treat it exactly once, in one place.

Kinds (bounded on purpose; no generic rules engine):
- test-verdict: run `mncs test` on a program; PASS -> TRUE, FAIL -> FALSE,
  anything else (infra, timeout, invalid) -> UNKNOWN with a note.
- path-changed: compare a file digest against the last observation;
  changed -> TRUE (and the new observation is returned for storage),
  same -> FALSE, unreadable -> UNKNOWN.
- resource-headroom: correlate a fresh System Monitor projection with one
  exact Forge receipt, then pass validated numeric facts to native Automation;
  missing or mismatched facts -> UNKNOWN.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any

from . import native
from .codes import COND_FALSE, COND_TRUE, COND_UNKNOWN


def _digest_file(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data)}


def evaluate_test_verdict(*, mncs: str, program: str,
                          libraries: list[str] | None,
                          test_identities: list[str] | None,
                          timeout_s: int) -> tuple[int, dict[str, Any]]:
    try:
        result = native.run_test_suite(
            mncs=mncs, program=program, libraries=libraries,
            test_identities=test_identities, timeout_s=timeout_s)
    except native.NativeError as error:
        return COND_UNKNOWN, {"note": f"runner unreachable: {error}"}
    classification = result.get("classification")
    summary = result.get("summary") or {}
    evidence = {"classification": classification,
                "verdict": result.get("verdict"),
                "passed": summary.get("passed"),
                "failed": summary.get("failed"),
                "run_identity": (result.get("execution") or {}).get(
                    "run_identity")}
    if classification == "passed":
        return COND_TRUE, evidence
    if classification in ("test_failure", "failed"):
        return COND_FALSE, evidence
    return COND_UNKNOWN, {**evidence,
                          "note": f"runner reported {classification}"}


def evaluate_path_changed(*, path: str, last_observed: dict[str, Any] | None,
                          allowed_roots: list[str]) -> tuple[int, dict[str, Any] | None]:
    """Return (cond, observation). Observation is None when unreadable."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate)
    resolved = candidate.resolve() if candidate.exists() else candidate
    roots = [Path(root).resolve() for root in allowed_roots] if allowed_roots else []
    if roots and not any(resolved == root or root in resolved.parents
                         for root in roots):
        return COND_UNKNOWN, None
    try:
        if not resolved.is_file():
            return COND_UNKNOWN, None
        observation = _digest_file(resolved)
        observation["path"] = str(resolved)
    except OSError:
        return COND_UNKNOWN, None
    if last_observed is None:
        # First observation establishes the baseline; a watch fires on
        # *change*, so bootstrapping the baseline is not a firing.
        return COND_FALSE, observation
    if observation["sha256"] != last_observed.get("sha256"):
        return COND_TRUE, observation
    return COND_FALSE, observation


def evaluate_resource_headroom(
    *,
    mncs: str | None = None,
    path: str,
    measurement_execution: str,
    measurement_subject_identity: str,
    intent_identity: str,
    requested_memory_bytes: int,
    measurement_scale_percent: int,
    minimum_host_headroom_bytes: int,
    max_age_ms: int,
    now_ms: int | None = None,
) -> tuple[int, dict[str, Any]]:
    """Validate Monitor/Forge provenance, then ask native Automation for the decision."""
    def unknown(reason: str) -> tuple[int, dict[str, Any]]:
        return COND_UNKNOWN, {
            "kind": "resource-headroom",
            "status": "unknown",
            "reason": reason,
            "intent_identity": intent_identity,
            "measurement_execution": measurement_execution,
            "measurement_subject_identity": measurement_subject_identity,
        }

    try:
        source = Path(path)
        if not source.is_file():
            return unknown("monitor-projection-not-a-regular-file")
        with source.open("rb") as handle:
            raw = handle.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            return unknown("monitor-projection-exceeds-size-bound")
        document = json.loads(raw)
    except (OSError, ValueError) as error:
        return unknown(f"monitor-projection-unavailable:{str(error)[:160]}")
    if not isinstance(document, dict):
        return unknown("monitor-projection-malformed")
    schema = document.get("schema")
    resource_sample = schema == "mncs.system-monitor.resource-sample.v1"
    if schema not in {
        "mncs.system-monitor.snapshot.v1",
        "mncs.system-monitor.resource-sample.v1",
    }:
        return unknown("unexpected-monitor-snapshot-schema")
    observed_at = document.get("observed_at")
    max_i64 = 2**63 - 1
    if (not isinstance(observed_at, int) or isinstance(observed_at, bool)
            or observed_at < 0 or observed_at > max_i64):
        return unknown("monitor-observation-time-unavailable")
    if (now_ms is not None
            and (not isinstance(now_ms, int) or isinstance(now_ms, bool)
                 or now_ms < 0 or now_ms > max_i64)):
        return unknown("automation-evaluation-time-unavailable")
    evaluated_at = now_ms if now_ms is not None else int(time.time() * 1000)
    if (not isinstance(evaluated_at, int) or isinstance(evaluated_at, bool)
            or evaluated_at < 0 or evaluated_at > max_i64):
        return unknown("automation-evaluation-time-unavailable")
    numeric_inputs = {
        "max_age_ms": max_age_ms,
        "requested_memory_bytes": requested_memory_bytes,
        "measurement_scale_percent": measurement_scale_percent,
        "minimum_host_headroom_bytes": minimum_host_headroom_bytes,
    }
    for name, value in numeric_inputs.items():
        if (not isinstance(value, int) or isinstance(value, bool)
                or value < 0 or value > max_i64):
            return unknown(f"automation-resource-input-invalid:{name}")
    if not 100 <= measurement_scale_percent <= 500:
        return unknown("automation-resource-input-invalid:measurement_scale_percent")

    host = document.get("host")
    if (resource_sample and isinstance(host, dict)
            and host.get("memory_available_status") != "observed"):
        return unknown("host-memory-availability-not-observed")
    available = host.get("memory_available_bytes") if isinstance(host, dict) else None
    if (not isinstance(available, int) or isinstance(available, bool)
            or available < 0 or available > max_i64):
        return unknown("host-memory-availability-unavailable")
    records_document = document.get("execution_records")
    if not isinstance(records_document, dict) or records_document.get("schema") != (
        "mncs.system-monitor.execution-records.v1"
    ):
        return unknown("forge-execution-records-unavailable")
    records = records_document.get("records")
    if not isinstance(records, list):
        return unknown("forge-execution-records-malformed")
    matches = [
        row for row in records
        if isinstance(row, dict) and row.get("execution") == measurement_execution
    ]
    if len(matches) != 1:
        return unknown("measurement-execution-missing-or-ambiguous")
    measurement = matches[0]
    if measurement.get("source") != "forge_receipt":
        return unknown("measurement-is-not-a-forge-receipt")
    if measurement.get("subject_identity") != measurement_subject_identity:
        return unknown("measurement-subject-identity-mismatch")
    envelope = measurement.get("resource_envelope")
    observations = measurement.get("resource_observations")
    if not isinstance(envelope, dict) or envelope.get("declared") is not True:
        return unknown("forge-resource-envelope-unavailable")
    memory_limit = envelope.get("memory_max_bytes")
    if (not isinstance(memory_limit, int) or isinstance(memory_limit, bool)
            or memory_limit <= 0 or memory_limit > max_i64):
        return unknown("forge-memory-envelope-unavailable")
    peak = (observations.get("cgroup_memory_peak_bytes")
            if isinstance(observations, dict) else None)
    if (not isinstance(peak, int) or isinstance(peak, bool)
            or peak < 0 or peak > max_i64):
        return unknown("forge-memory-measurement-unavailable")

    try:
        decision = native.evaluate_resource_headroom(
            mncs=native.find_mncs(mncs),
            observed_at_ms=observed_at,
            now_ms=evaluated_at,
            max_age_ms=max_age_ms,
            host_available_bytes=available,
            envelope_memory_max_bytes=memory_limit,
            measured_peak_bytes=peak,
            requested_memory_bytes=requested_memory_bytes,
            scale_percent=measurement_scale_percent,
            minimum_host_headroom_bytes=minimum_host_headroom_bytes,
        )
    except native.NativeError as error:
        return unknown(f"automation-resource-decision-unavailable:{str(error)[:120]}")
    condition = decision.get("condition")
    reason_code = decision.get("reason")
    if (not isinstance(condition, int) or isinstance(condition, bool)
            or condition not in (COND_FALSE, COND_TRUE, COND_UNKNOWN)
            or not isinstance(reason_code, int) or isinstance(reason_code, bool)):
        return unknown("automation-resource-decision-malformed")
    reasons = {
        0: "resource-headroom-input-invalid",
        1: "monitor-observation-in-future",
        2: "monitor-observation-stale-or-in-future",
        3: "previous-measurement-exceeded-envelope",
        4: "current-host-headroom-insufficient",
        5: "resource-headroom-sufficient",
    }
    reason = reasons.get(reason_code, "automation-resource-reason-unknown")
    status = ("ready" if condition == COND_TRUE else
              "defer" if condition == COND_FALSE else "unknown")
    evidence = {
        "kind": "resource-headroom",
        "status": status,
        "reason": reason,
        "observed_at_ms": observed_at,
        "age_ms": decision["age_ms"],
        "measurement_execution": measurement_execution,
        "measurement_subject_identity": measurement_subject_identity,
        "measured_peak_bytes": peak,
        "measurement_envelope_bytes": memory_limit,
        "measurement_scale_percent": measurement_scale_percent,
        "scaled_measurement_bytes": decision["scaled_measurement_bytes"],
        "intent_identity": intent_identity,
        "compiler_estimate_bytes": requested_memory_bytes,
        "required_memory_bytes": decision["required_memory_bytes"],
        "total_required_memory_bytes": decision["total_required_memory_bytes"],
        "minimum_host_headroom_bytes": minimum_host_headroom_bytes,
        "host_available_bytes": available,
        "remaining_after_intent_bytes": decision["remaining_after_intent_bytes"],
        "remaining_after_reserve_bytes": decision["remaining_after_reserve_bytes"],
    }
    return condition, evidence


CONDITION_NAMES = {0: "false", 1: "true", 2: "unknown"}
