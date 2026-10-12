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
- resource-headroom: read a fresh System Monitor projection with one exact
  Forge receipt; insufficient known headroom -> FALSE, sufficient -> TRUE,
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
    """Decide whether a compiler intent fits using Monitor and Forge facts.

    This consumes the System Monitor machine projection; it does not admit work or
    infer compiler semantics. A Forge receipt must match both its exact execution
    identity and subject digest. The next resource requirement is the larger of the
    compiler estimate and the scaled observed peak, so retained measurements can only
    raise the estimate. Missing, stale, ambiguous, or incomplete inputs remain UNKNOWN.
    """
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
    if document.get("schema") != "mncs.system-monitor.snapshot.v1":
        return unknown("unexpected-monitor-snapshot-schema")
    observed_at = document.get("observed_at")
    if not isinstance(observed_at, int) or isinstance(observed_at, bool) or observed_at < 0:
        return unknown("monitor-observation-time-unavailable")
    evaluated_at = int(now_ms) if now_ms is not None else int(time.time() * 1000)
    age_ms = evaluated_at - observed_at
    if age_ms < 0 or age_ms > max_age_ms:
        return unknown("monitor-observation-stale-or-in-future")

    host = document.get("host")
    available = host.get("memory_available_bytes") if isinstance(host, dict) else None
    if not isinstance(available, int) or isinstance(available, bool) or available < 0:
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
    if not isinstance(memory_limit, int) or isinstance(memory_limit, bool) or memory_limit <= 0:
        return unknown("forge-memory-envelope-unavailable")
    peak = (observations.get("cgroup_memory_peak_bytes")
            if isinstance(observations, dict) else None)
    if not isinstance(peak, int) or isinstance(peak, bool) or peak < 0:
        return unknown("forge-memory-measurement-unavailable")

    scaled_peak = (peak * measurement_scale_percent + 99) // 100
    required = max(requested_memory_bytes, scaled_peak)
    total_required = required + minimum_host_headroom_bytes
    envelope_ok = peak <= memory_limit
    enough_headroom = available >= total_required
    ready = envelope_ok and enough_headroom
    decision = "ready" if ready else "defer"
    reason = ("resource-headroom-sufficient" if ready else
              "previous-measurement-exceeded-envelope" if not envelope_ok else
              "current-host-headroom-insufficient")
    evidence = {
        "kind": "resource-headroom",
        "status": decision,
        "reason": reason,
        "observed_at_ms": observed_at,
        "age_ms": age_ms,
        "measurement_execution": measurement_execution,
        "measurement_subject_identity": measurement_subject_identity,
        "measured_peak_bytes": peak,
        "measurement_envelope_bytes": memory_limit,
        "measurement_scale_percent": measurement_scale_percent,
        "scaled_measurement_bytes": scaled_peak,
        "intent_identity": intent_identity,
        "compiler_estimate_bytes": requested_memory_bytes,
        "required_memory_bytes": required,
        "minimum_host_headroom_bytes": minimum_host_headroom_bytes,
        "host_available_bytes": available,
        "remaining_after_intent_bytes": available - required,
        "remaining_after_reserve_bytes": available - total_required,
    }
    return (COND_TRUE if ready else COND_FALSE), evidence


CONDITION_NAMES = {0: "false", 1: "true", 2: "unknown"}
