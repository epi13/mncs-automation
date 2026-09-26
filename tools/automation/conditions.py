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
"""

from __future__ import annotations

import hashlib
from pathlib import Path
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


CONDITION_NAMES = {0: "false", 1: "true", 2: "unknown"}
