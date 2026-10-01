#!/usr/bin/env python3
"""Provider entrypoint for native projection decisions (transport only).

Verbs:
  plan     native `mncs.automation.projection.v1::plan_tick` ->
           `mncs.reconcile-plan/1` envelope.
  adopt    native `mncs.automation.reconcile.v1::adopt_observed` ->
           `mncs.adopt-decision/1` envelope.
  revisit  native `mncs.automation.projection.v1::revisit_tick` ->
           `mncs.revisit-decision/1` envelope.

All policy lives in the native modules; this script encodes arguments
and decodes decisions.

Plan request keys (all integers; see docs/MODEL.md for code tables):
  canonical_gen, observed_gen, inputs_changed, verdict,
  require_verified, repo, branch, claim, target, region, output,
  splice_ok, defer_count, defer_bound, unpublished, threshold,
  oldest_unpublished_ms, now_ms, max_latency_ms
Adopt request keys: observed_gen, regenerated_gen, canonical_gen.
Revisit request keys: wait, event.
Each request may carry optional "projection" through to the envelope.
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


def adopt(request: dict, mncs: str, timeout_s: int) -> dict:
    fields = {}
    for name in ("observed_gen", "regenerated_gen", "canonical_gen"):
        if name not in request:
            raise ValueError(f"adopt request missing {name!r}")
        fields[name] = int(request[name])
    result = native.adopt_observed(mncs=mncs, libraries=libraries(),
                                   timeout_s=timeout_s, **fields)
    return {
        "schema_version": codes.ADOPT_SCHEMA,
        "projection": request.get("projection"),
        "accept": bool(result["accept"]),
        "new_observed": result["new_observed"],
        "reason": result["reason"],
        "reason_name": codes.ADOPT_REASON_NAMES.get(
            result["reason"], "unknown"),
    }


def revisit(request: dict, mncs: str, timeout_s: int) -> dict:
    for name in ("wait", "event"):
        if name not in request:
            raise ValueError(f"revisit request missing {name!r}")
    decision = native.revisit_tick(
        mncs=mncs, libraries=libraries(), timeout_s=timeout_s,
        wait=int(request["wait"]), event=int(request["event"]))
    return {
        "schema_version": codes.REVISIT_SCHEMA,
        "projection": request.get("projection"),
        "wait": int(request["wait"]),
        "wait_name": codes.WAIT_NAMES.get(int(request["wait"]), "unknown"),
        "event": int(request["event"]),
        "event_name": codes.REVISIT_EVENT_NAMES.get(
            int(request["event"]), "unknown"),
        "revisit": bool(decision),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verb", choices=("plan", "adopt", "revisit"))
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--mncs", type=str, default=None)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError(f"{args.verb} request must be a JSON object")
        binary = find_mncs(args.mncs)
        if binary is None:
            raise ValueError("mncs compiler binary unavailable")
        if args.verb == "plan":
            print(json.dumps(plan(request, binary, args.timeout)))
        elif args.verb == "adopt":
            print(json.dumps(adopt(request, binary, args.timeout)))
        else:
            print(json.dumps(revisit(request, binary, args.timeout)))
    except (OSError, ValueError, native.NativeError) as error:
        print(f"reconcile {args.verb} failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
