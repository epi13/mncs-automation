"""Persistent state: atomic JSON documents and append-only occurrence logs.

Layout under the state directory::

    definitions/<aid>.json     validated automation definitions
    state/<aid>.json           evaluation state (survives restarts)
    occurrences/<aid>.jsonl    append-only occurrence history (bounded)
    artifacts/<aid>/<inv>.json execution result artifacts
    policy.json                global admission policy (optional)
    lock                       daemon mutual-exclusion lock file

Every write is atomic (temp file + fsync + rename) so power loss cannot
leave a torn definition or state document. The occurrence log is the
audit trail: pruning drops old lines but never rewrites history.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def layout(state_dir: Path) -> dict[str, Path]:
    return {
        "definitions": state_dir / "definitions",
        "state": state_dir / "state",
        "occurrences": state_dir / "occurrences",
        "artifacts": state_dir / "artifacts",
        "lock": state_dir / "lock",
        "policy": state_dir / "policy.json",
    }


def ensure(state_dir: Path) -> dict[str, Path]:
    paths = layout(state_dir)
    for key in ("definitions", "state", "occurrences", "artifacts"):
        paths[key].mkdir(parents=True, exist_ok=True)
    paths["lock"].touch(exist_ok=True)
    return paths


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read {path}: {error}")


def write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        parent_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = [json.loads(line) for line in lines if line.strip()]
    if limit is not None:
        records = records[-limit:]
    return records


def prune_jsonl(path: Path, bound: int) -> int:
    """Keep at most `bound` recent lines. Returns lines dropped."""
    records = read_jsonl(path)
    if len(records) <= bound:
        return 0
    kept = records[-bound:]
    text = "".join(json.dumps(record, sort_keys=True) + "\n" for record in kept)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(records) - bound


@contextmanager
def locked(state_dir: Path) -> Iterator[None]:
    ensure(state_dir)
    with open(layout(state_dir)["lock"], "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": "mncs.automation-policy/1",
        "allowed_target_kinds": ["mncs-test", "mncs-call", "doctor"],
        "program_roots": [],
        "doctor_binary": "",
        "mncs_binary": "",
        "max_automations": 256,
    }


def read_policy(state_dir: Path) -> dict[str, Any]:
    policy = read_json(layout(state_dir)["policy"], None)
    if policy is None:
        return default_policy()
    merged = default_policy()
    merged.update(policy)
    return merged
