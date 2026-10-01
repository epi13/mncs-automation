#!/usr/bin/env python3
"""Provider entrypoint for native projection planning (transport only).

Reads one JSON plan request, calls native
`mncs.automation.projection.v1::plan_tick`, and prints the
`mncs.reconcile-plan/1` envelope on stdout. All policy lives in the
native module; this script encodes arguments and decodes the plan.

Request keys (all integers; see docs/MODEL.md for code tables):
  canonical_gen, observed_gen, inputs_changed, verdict,
  require_verified, repo, branch, claim, target, region, output,
  splice_ok, defer_count, defer_bound, unpublished, threshold,
  oldest_unpublished_ms, now_ms, max_latency_ms
plus optional "projection" carried through to the envelope.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from automation import codes, native  # noqa: E402

FIELDS = ("canonical_gen", "observed_gen", "inputs_changed", "verdict",
          "require_verified", "repo", "branch", "claim", "target",
          "region", "output", "splice_ok", "defer_count", "defer_bound",
          "unpublished", "threshold", "oldest_unpublished_ms", "now_ms",
          "max_latency_ms")


def find_mncs(explicit: str | None = None) -> str | None:
    from shutil import which

    for candidate in (
        explicit,
        os.environ.get("MNCS_BIN"),
        os.environ.get("MNCS_BINARY"),
        which("mncs"),
        str(REPO.parent / "mncs-language" / "target" / "release" / "mncs"),
        str(REPO.parent / "mncs-language" / "target" / "debug" / "mncs"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def libraries() -> list[str]:
    found = []
    override = os.environ.get("MNCS_TEST_NATIVE")
    test_native = Path(override) if override else REPO.parent / "mncs-test" / "native"
    if test_native.is_dir():
        found.append(str(test_native))
    language_root = os.environ.get("MNCS_LANGUAGE_ROOT")
    language_library = (Path(language_root) / "library" if language_root
                        else REPO.parent / "mncs-language" / "library")
    if language_library.is_dir():
        found.append(str(language_library))
    return found


def plan(request: dict, mncs: str, timeout_s: int) -> dict:
    fields = {}
    for name in FIELDS:
        if name not in request:
            raise ValueError(f"plan request missing {name!r}")
        fields[name] = int(request[name])
    result = native.plan_tick(mncs=mncs, libraries=libraries(),
                              timeout_s=timeout_s, **fields)
    envelope = {
        "schema_version": codes.PLAN_SCHEMA,
        "projection": request.get("projection"),
        "gate": result["gate"],
        "gate_name": codes.GATE_NAMES.get(result["gate"], "unknown"),
        "gate_reason": result["gate_reason"],
        "gate_reason_name": codes.GATE_REASON_NAMES.get(
            result["gate_reason"], "unknown"),
        "action": result["action"],
        "action_name": codes.RECON_ACTION_NAMES.get(
            result["action"], "unknown"),
        "reason": result["reason"],
        "reason_name": codes.RECON_REASON_NAMES.get(
            result["reason"], "unknown"),
        "new_canonical": result["new_canonical"],
        "new_observed": result["new_observed"],
        "publish": bool(result["publish"]),
        "publish_reason": result["publish_reason"],
        "wakeup_ms": result["wakeup_ms"],
        "execute": bool(result["execute"]),
    }
    return envelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=("plan",))
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--mncs", type=str, default=None)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("plan request must be a JSON object")
        binary = find_mncs(args.mncs)
        if binary is None:
            raise ValueError("mncs compiler binary unavailable")
        print(json.dumps(plan(request, binary, args.timeout)))
    except (OSError, ValueError, native.NativeError) as error:
        print(f"reconcile plan failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
