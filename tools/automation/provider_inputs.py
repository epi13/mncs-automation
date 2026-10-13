"""Read bounded, provenance-bearing inputs supplied by MNCS Environment.

Automation consumes the Environment provider-input contract without opening
Store paths or importing Environment internals. The manifest is transport
metadata, not an authorization grant; Environment remains responsible for
source admission and canonical persistence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

INPUT_SCHEMA = "mncs.environment.provider-input-artifacts/1"
MAX_MANIFEST_BYTES = 32 * 1024
MAX_ARTIFACTS = 8
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,79}\Z")
_CAPABILITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")
_BINDING = re.compile(r"cap_[A-Za-z0-9_-]{1,120}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ProviderInputError(ValueError):
    """The Environment input manifest or selected file failed verification."""


def _relative_path(value: Any, field: str) -> PurePosixPath:
    if (not isinstance(value, str) or not value or len(value) > 1024
            or "\\" in value or "\x00" in value):
        raise ProviderInputError(f"provider input {field} is invalid")
    parts = value.split("/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in parts):
        raise ProviderInputError(f"provider input {field} escapes its bundle")
    return path


def _read_file(root_fd: int, relative: PurePosixPath, expected_size: int) -> bytes:
    directory_fd = os.dup(root_fd)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(
                part, os.O_RDONLY | directory_flag | nofollow_flag,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        descriptor = os.open(
            relative.parts[-1], os.O_RDONLY | nofollow_flag, dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(before.st_mode)
                    or before.st_mode & 0o222
                    or before.st_size != expected_size
                    or before.st_size > MAX_ARTIFACT_BYTES):
                raise ProviderInputError(
                    "provider input file is not a bounded read-only regular file"
                )
            content = handle.read(MAX_ARTIFACT_BYTES + 1)
            after = os.fstat(handle.fileno())
        before_identity = (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if len(content) != expected_size or before_identity != after_identity:
            raise ProviderInputError("provider input file changed while read")
        return content
    except OSError as error:
        raise ProviderInputError(f"provider input file is unavailable: {error}") from error
    finally:
        os.close(directory_fd)


def read_environment_input(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return one verified JSON document and its immutable Store provenance."""
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ProviderInputError("provider input name is invalid")
    raw_root = os.environ.get("MNCS_ENV_INPUT_ARTIFACT_DIR")
    raw_manifest = os.environ.get("MNCS_ENV_INPUT_ARTIFACTS_JSON")
    if not isinstance(raw_root, str) or not raw_root or not isinstance(raw_manifest, str):
        raise ProviderInputError("Environment provider input is unavailable")
    try:
        encoded = raw_manifest.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ProviderInputError("Environment provider input manifest is invalid UTF-8") from error
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ProviderInputError("Environment provider input manifest exceeds its size bound")
    try:
        manifest = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderInputError("Environment provider input manifest is malformed") from error
    if (not isinstance(manifest, dict)
            or set(manifest) != {"schema_version", "session_id", "artifacts"}
            or manifest.get("schema_version") != INPUT_SCHEMA
            or not isinstance(manifest.get("session_id"), str)
            or not manifest["session_id"].startswith("ses_")
            or len(manifest["session_id"]) > 160):
        raise ProviderInputError("Environment provider input manifest identity is invalid")
    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or len(rows) > MAX_ARTIFACTS:
        raise ProviderInputError("Environment provider input count exceeds its bound")

    selected: dict[str, Any] | None = None
    names: set[str] = set()
    local_paths: set[str] = set()
    total = 0
    required_fields = {
        "name", "capability", "binding_id", "store_reference", "path",
        "byte_count", "sha256", "local_path",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != required_fields:
            raise ProviderInputError("Environment provider input entry is malformed")
        row_name = row.get("name")
        if (not isinstance(row_name, str) or _NAME.fullmatch(row_name) is None
                or row_name in names):
            raise ProviderInputError("Environment provider input names are invalid or duplicated")
        names.add(row_name)
        if (not isinstance(row.get("capability"), str)
                or _CAPABILITY.fullmatch(row["capability"]) is None
                or not isinstance(row.get("binding_id"), str)
                or _BINDING.fullmatch(row["binding_id"]) is None
                or not isinstance(row.get("store_reference"), str)
                or not row["store_reference"].startswith("mncs-store:")
                or len(row["store_reference"]) > 512):
            raise ProviderInputError("Environment provider input source identity is invalid")
        _relative_path(row.get("path"), "source path")
        local_path = _relative_path(row.get("local_path"), "local path")
        local_key = local_path.as_posix()
        if local_key in local_paths:
            raise ProviderInputError("Environment provider input paths are duplicated")
        local_paths.add(local_key)
        size = row.get("byte_count")
        if (type(size) is not int or size < 0 or size > MAX_ARTIFACT_BYTES
                or not isinstance(row.get("sha256"), str)
                or _SHA256.fullmatch(row["sha256"]) is None):
            raise ProviderInputError("Environment provider input size or digest is invalid")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ProviderInputError("Environment provider input total exceeds its size bound")
        if row_name == name:
            selected = row
    if selected is None:
        raise ProviderInputError(f"Environment provider input {name!r} is absent")

    root = Path(raw_root)
    if not root.is_absolute():
        raise ProviderInputError("Environment provider input directory must be absolute")
    try:
        root_fd = os.open(
            root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise ProviderInputError(f"Environment provider input directory is unavailable: {error}") from error
    try:
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            raise ProviderInputError("Environment provider input root is not a directory")
        relative = _relative_path(selected["local_path"], "local path")
        content = _read_file(root_fd, relative, selected["byte_count"])
    finally:
        os.close(root_fd)
    digest = hashlib.sha256(content).hexdigest()
    if digest != selected["sha256"]:
        raise ProviderInputError("Environment provider input failed SHA-256 verification")
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderInputError("selected Automation input is not valid JSON") from error
    if not isinstance(document, dict):
        raise ProviderInputError("selected Automation input must be a JSON object")
    provenance = {
        "environment_session_id": manifest["session_id"],
        "name": selected["name"],
        "capability": selected["capability"],
        "binding_id": selected["binding_id"],
        "store_reference": selected["store_reference"],
        "path": selected["path"],
        "byte_count": selected["byte_count"],
        "sha256": digest,
    }
    return document, provenance
