"""Typed invocation targets: canonical operations, never shell strings.

A target is a tagged record naming an exact capability and its typed
arguments. Each executor builds a FIXED argv shape from the record --
there is no command template, no shell, no string interpolation. Policy
(admission) and authorization (capabilities) are checked before every
invocation, so a stale automation cannot bypass current requirements
just because it was created earlier.

Target kinds:
- mncs-test: `mncs test <program> [--library ...] [--test-identity ...]`
  with results captured to an automation-owned artifact file.
- mncs-call: `mncs call <program> --module --function --args <wire>`
  with capability grants passed only when granted.
- doctor: `<doctor-binary> --root <root> doctor --json` plus an
  allowlisted subset of extra flags (read-only; never fix/migrate).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import native

REQUIRED_BY_KIND = {
    "mncs-test": ["test:execute"],
    "mncs-call": ["call:execute"],
    "doctor": ["doctor:read"],
}

DOCTOR_EXTRA_ALLOWLIST = {"--changed-path", "--with-language-backend",
                          "--explain", "--verbose"}


class TargetError(RuntimeError):
    pass


def check_policy(*, target: dict[str, Any], policy: dict[str, Any],
                 mncs_bin: str) -> None:
    allowed = policy.get("allowed_target_kinds", [])
    if target["kind"] not in allowed:
        raise TargetError(f"target kind {target['kind']} not admitted "
                          f"by policy")
    roots = [Path(root).resolve() for root in policy.get("program_roots", [])]
    if roots:
        for key in ("program", "root"):
            value = target.get(key)
            if not value:
                continue
            resolved = Path(value).resolve()
            if not any(resolved == root or root in resolved.parents
                       for root in roots):
                raise TargetError(f"{key} {value} outside policy roots")
    pinned = policy.get("mncs_binary")
    if pinned and Path(mncs_bin).resolve() != Path(pinned).resolve():
        raise TargetError("mncs binary is not the policy-pinned binary")
    if target["kind"] == "doctor":
        pinned_doctor = policy.get("doctor_binary")
        if pinned_doctor and Path(_doctor_binary()).resolve() != Path(
                pinned_doctor).resolve():
            raise TargetError("doctor binary is not the policy-pinned binary")


def check_authorization(*, target: dict[str, Any],
                        granted: list[str]) -> None:
    required = list(target.get("required_capabilities", []))
    if target["kind"] == "mncs-call":
        grants = target.get("grants", {})
        for capability in grants.get("process", []):
            required.append(f"grant-process:{capability}")
        if grants.get("structured"):
            required.append("grant-structured")
        for root in grants.get("fs", []):
            required.append(f"grant-fs:{root}")
    missing = [item for item in required if item not in granted]
    if missing:
        raise TargetError(f"missing capabilities: {', '.join(missing)}")


def program_digest(path: str) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise TargetError(f"cannot read target program {path}: {error}")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def validate_call_target(*, mncs: str, target: dict[str, Any],
                         libraries: list[str] | None,
                         timeout_s: int = 60) -> None:
    """Confirm module/function/arity against compiler inventory."""
    try:
        inventory = native.declaration_inventory(
            mncs=mncs, program=target["program"], libraries=libraries,
            timeout_s=timeout_s)
    except native.NativeError as error:
        raise TargetError(f"target inventory failed: {error}")
    if not inventory.get("valid"):
        raise TargetError("target program is not valid MNCS: "
                          f"{json.dumps(inventory.get('diagnostics', []))[:300]}")
    callables = {item.get("function_identity"): item
                 for item in (inventory.get("inventory") or {}).get(
                     "callables", [])}
    wanted = f"mncs:0.2:function:{target['module']}::{target['function']}"
    match = callables.get(wanted)
    if match is None:
        # Fall back to name matching across identity versions.
        candidates = [item for item in callables.values()
                      if item.get("module") == target["module"]
                      and item.get("name") == target["function"]]
        if not candidates:
            raise TargetError(f"target function {target['module']}::"
                              f"{target['function']} not in inventory")
        match = candidates[0]
    expected = len(match.get("inputs", []))
    actual = len(target.get("args", []))
    if expected != actual:
        raise TargetError(f"target arity changed: definition has {actual} "
                          f"args, inventory has {expected}")


def _doctor_binary() -> str:
    explicit = os.environ.get("MNCS_DOCTOR_BIN")
    if explicit:
        return explicit
    return "mncs-doctor"


def execute(*, target: dict[str, Any], mncs: str,
            libraries: list[str] | None, artifacts_dir: Path,
            invocation_id: str, timeout_s: int | None = None
            ) -> dict[str, Any]:
    kind = target["kind"]
    if kind == "mncs-test":
        return _execute_test(target, mncs, libraries, artifacts_dir,
                             invocation_id, timeout_s)
    if kind == "mncs-call":
        return _execute_call(target, mncs, libraries, artifacts_dir,
                             invocation_id, timeout_s)
    if kind == "doctor":
        return _execute_doctor(target, artifacts_dir, invocation_id,
                               timeout_s)
    raise TargetError(f"unknown target kind {kind}")


def _execute_test(target: dict[str, Any], mncs: str,
                  libraries: list[str] | None, artifacts_dir: Path,
                  invocation_id: str, timeout_s: int | None) -> dict[str, Any]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    result_path = artifacts_dir / f"{invocation_id}.test-result.json"
    try:
        result = native.run_test_suite(
            mncs=mncs, program=target["program"], libraries=libraries,
            test_identities=target.get("test_identities") or None,
            result_path=str(result_path),
            timeout_s=timeout_s or target.get("timeout_s", 300))
    except native.NativeError as error:
        return {"status": "unknown", "reason": "runner-unreachable",
                "note": str(error)}
    classification = result.get("classification")
    execution = result.get("execution") or {}
    if classification == "passed":
        status = "ok"
    elif classification in ("test_failure", "failed", "compile_failure"):
        status = "failed"
    else:
        status = "unknown"
    return {"status": status, "classification": classification,
            "verdict": result.get("verdict"),
            "run_identity": execution.get("run_identity"),
            "artifact_sha256": execution.get("artifact_sha256"),
            "test_case_identities": execution.get("test_case_identities"),
            "result_artifact": str(result_path),
            "result_digest": program_digest(str(result_path))
            if result_path.exists() else None}


def _execute_call(target: dict[str, Any], mncs: str,
                  libraries: list[str] | None, artifacts_dir: Path,
                  invocation_id: str, timeout_s: int | None) -> dict[str, Any]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    try:
        document = native.call_function(
            mncs=mncs, program=target["program"],
            module=target["module"], function=target["function"],
            args=target.get("args", []), libraries=libraries,
            grants=target.get("grants", {}),
            timeout_s=timeout_s or target.get("timeout_s", 120))
    except native.NativeError as error:
        return {"status": "unknown", "reason": "runner-unreachable",
                "note": str(error)}
    if document.get("status") != "returned":
        return {"status": "failed", "reason": "call-rejected",
                "error": str(document.get("error"))[:500]}
    try:
        returned = native.decode_record(document["call"]["returned"])
    except native.NativeError as error:
        return {"status": "unknown", "reason": "undecodable-result",
                "note": str(error)}
    digest = "sha256:" + hashlib.sha256(
        json.dumps(returned, sort_keys=True).encode()).hexdigest()
    artifact = artifacts_dir / f"{invocation_id}.call-result.json"
    artifact.write_text(json.dumps(document, indent=2, sort_keys=True),
                        encoding="utf-8")
    return {"status": "ok", "returned_digest": digest,
            "artifact_sha256": (document.get("call") or {}).get(
                "artifact_sha256"),
            "result_artifact": str(artifact)}


def _execute_doctor(target: dict[str, Any], artifacts_dir: Path,
                    invocation_id: str,
                    timeout_s: int | None) -> dict[str, Any]:
    import subprocess
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    # Doctor parses globals after the subcommand: `doctor --root R --json`.
    command = [_doctor_binary(), "doctor", "--root", target["root"], "--json"]
    extra = target.get("extra_args", [])
    changed = target.get("changed_paths", [])
    for flag in extra:
        if flag not in DOCTOR_EXTRA_ALLOWLIST:
            return {"status": "unknown", "reason": "policy-denied-flag",
                    "note": f"{flag} is not an allowlisted doctor flag"}
        command.append(flag)
    for path in changed:
        resolved = Path(path)
        root = Path(target["root"]).resolve()
        absolute = (resolved if resolved.is_absolute()
                    else root / resolved).resolve()
        if absolute != root and root not in absolute.parents:
            return {"status": "unknown", "reason": "path-outside-root",
                    "note": path}
        command += ["--changed-path", str(absolute)]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True,
            timeout=timeout_s or target.get("timeout_s", 300))
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "unknown", "reason": "runner-unreachable",
                "note": str(error)}
    artifact = artifacts_dir / f"{invocation_id}.doctor.json"
    artifact.write_text(completed.stdout, encoding="utf-8")
    try:
        report = json.loads(completed.stdout or "{}")
    except ValueError:
        return {"status": "unknown", "reason": "undecodable-result",
                "exit_code": completed.returncode,
                "stderr": completed.stderr.strip()[-500:],
                "result_artifact": str(artifact)}
    checks = report.get("checks", [])
    nested = sum(len(check.get("findings", []) or [])
                 for check in checks if isinstance(check, dict))
    nested += len(report.get("file_diagnostics", []) or [])
    nested += len(report.get("findings", []) or [])
    nested += len(report.get("diagnostics", []) or [])
    strange = [check.get("status") for check in checks
               if isinstance(check, dict)
               and check.get("status") not in ("pass", "ok")]
    return {"status": "ok", "exit_code": completed.returncode,
            "report_exit_code": report.get("exit_code"),
            "finding_count": nested,
            "nonpassing_checks": strange,
            "result_artifact": str(artifact),
            "result_digest": program_digest(str(artifact))}
