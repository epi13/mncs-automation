"""Compiler bridge: `mncs call`, inventories, and the typed-value wire.

All MNCS semantics flow through this module. The host never re-derives a
schedule, edge, or firing decision -- it encodes arguments, invokes the
native function, and decodes the returned record. A subprocess that fails
to return a decision is an evaluation error, never a fabricated decision.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
NATIVE_DIR = REPO_ROOT / "native"


def cache_dir(explicit: str | None = None) -> str:
    """Shared compiler cache (content-keyed by the compiler itself)."""
    if explicit:
        return explicit
    env = os.environ.get("MNCS_CACHE_DIR")
    if env:
        return env
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    return os.path.join(base, "mncs-automation")


def find_mncs(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("MNCS_BIN") or os.environ.get("MNCS_BINARY")
    if env:
        return env
    root = os.environ.get("MNCS_LANGUAGE_ROOT")
    if root:
        candidate = Path(root) / "target" / "debug" / "mncs"
        if candidate.is_file():
            return str(candidate)
    return "mncs"


def default_libraries(extra: list[str] | None = None) -> list[str]:
    language_root = os.environ.get("MNCS_LANGUAGE_ROOT")
    libraries: list[str] = []
    if language_root:
        candidate = Path(language_root) / "library"
        if candidate.is_dir():
            libraries.append(str(candidate))
    test_native = os.environ.get("MNCS_TEST_NATIVE")
    if test_native:
        libraries.append(test_native)
    for item in extra or []:
        if item not in libraries:
            libraries.append(item)
    return libraries


class NativeError(RuntimeError):
    pass


def _run(command: list[str], timeout_s: int, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout_s, **kwargs)
    except (OSError, subprocess.SubprocessError) as error:
        raise NativeError(f"cannot start compiler: {error}")


def call_function(*, mncs: str, program: str, module: str, function: str,
                  args: list[dict[str, Any]], libraries: list[str] | None = None,
                  grants: dict[str, Any] | None = None,
                  timeout_s: int = 120,
                  cache: str | None = None) -> dict[str, Any]:
    """Invoke a native function; return the decoded `call` document."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as handle:
        json.dump(args, handle)
        args_path = handle.name
    command = [mncs, "call", program, "--module", module,
               "--function", function, "--args", args_path,
               "--cache-dir", cache or cache_dir()]
    for library in default_libraries(libraries):
        command += ["--library", library]
    grants = grants or {}
    for capability in grants.get("process", []):
        command += ["--grant-process", capability]
    if grants.get("structured"):
        command += ["--grant-structured", "capability=default"]
    for root in grants.get("fs", []):
        command += ["--grant-fs", root]
    try:
        completed = _run(command, timeout_s)
    finally:
        try:
            os.unlink(args_path)
        except OSError:
            pass
    try:
        document = json.loads(completed.stdout)
    except ValueError:
        raise NativeError(f"compiler returned non-JSON: "
                          f"{completed.stderr.strip()[:400]}")
    return document


def decode_record(returned: list[dict[str, Any]]) -> dict[str, Any]:
    """Decode the first returned value into plain JSON scalars."""
    if not returned:
        raise NativeError("compiler returned no values")
    return _plain(returned[0])


def _plain(value: dict[str, Any]) -> Any:
    if not isinstance(value, dict) or len(value) != 1:
        raise NativeError(f"unexpected wire value {value!r}")
    tag, body = next(iter(value.items()))
    if tag == "integer":
        return body["value"]
    if tag == "boolean":
        return body["value"]
    if tag == "finite":
        return {"variant": body["variant"],
                "type": body.get("type"),
                "discriminant": body.get("discriminant")}
    if tag == "record":
        fields = body["fields"]
        if isinstance(fields, dict):
            return {name: _plain(item) for name, item in fields.items()}
        return {name: _plain(item) for name, item in fields}
    if tag == "sequence":
        return [_plain(item) for item in body["values"]]
    raise NativeError(f"unknown wire tag {tag!r}")


def declaration_inventory(*, mncs: str, program: str,
                          libraries: list[str] | None = None,
                          timeout_s: int = 120) -> dict[str, Any]:
    command = [mncs, "declaration-inventory", program]
    completed = _run(command, timeout_s)
    try:
        return json.loads(completed.stdout)
    except ValueError:
        raise NativeError(f"inventory returned non-JSON for {program}")


def run_test_suite(*, mncs: str, program: str,
                   libraries: list[str] | None = None,
                   test_identities: list[str] | None = None,
                   result_path: str | None = None,
                   timeout_s: int = 300) -> dict[str, Any]:
    command = [mncs, "test", program]
    for library in default_libraries(libraries):
        command += ["--library", library]
    for identity in test_identities or []:
        command += ["--test-identity", identity]
    if result_path:
        command += ["--result", result_path]
    completed = _run(command, timeout_s)
    try:
        return json.loads(completed.stdout)
    except ValueError:
        raise NativeError(f"test runner returned non-JSON for {program}: "
                          f"{completed.stderr.strip()[:400]}")


def evaluate_tick(*, mncs: str, libraries: list[str] | None = None,
                  timeout_s: int = 60, **fields: Any) -> dict[str, Any]:
    """Call native `evaluate_tick`; return the decoded TickDecision."""
    order = ("sched_kind", "p1", "p2", "trig_kind", "edge", "prev_cond",
             "cond", "prev_seq", "last_at_ms", "now_ms", "misfire", "bound",
             "first")
    args = [{"integer": {"value": int(fields[name])}} for name in order]
    document = call_function(
        mncs=mncs,
        program=str(NATIVE_DIR / "mncs" / "automation" / "evaluation.mncs"),
        module="mncs.automation.evaluation.v1",
        function="evaluate_tick",
        args=args,
        libraries=default_libraries((libraries or []) + [str(NATIVE_DIR)]),
        timeout_s=timeout_s,
    )
    if document.get("status") != "returned":
        raise NativeError(f"native evaluation failed: "
                          f"{document.get('error', document) }")
    decision = decode_record(document["call"]["returned"])
    for key in ("fire", "seq", "at_ms", "wakeup_ms", "reason",
                "missed_from", "missed_to", "new_prev"):
        if key not in decision:
            raise NativeError(f"native decision missing {key}")
    return decision
