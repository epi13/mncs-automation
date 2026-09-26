"""Command line: define, inspect, operate, and run automations.

Read paths resolve against the current directory; the state directory
holds everything Automation persists. Time is injectable everywhere via
--now-ms (milliseconds since the Unix epoch); without it the wall clock
is used. `tick --dry-run` evaluates without persisting or executing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import engine, native, store, targets
from .model import (DefinitionError, automation_id, fresh_state,
                    validate_definition)


def _state_dir(args: argparse.Namespace) -> Path:
    raw = getattr(args, 'state_dir', None) or "~/.local/share/mncs-automation"
    return Path(raw).expanduser().resolve()


def _mncs(args: argparse.Namespace) -> str:
    return native.find_mncs(getattr(args, 'mncs', None))


def _load_definition(paths: dict[str, Path], name: str) -> tuple[str, dict[str, Any]]:
    aid = automation_id(name)
    path = paths["definitions"] / f"{aid}.json"
    definition = store.read_json(path)
    if not isinstance(definition, dict):
        raise DefinitionError(f"unknown automation {name!r}")
    return aid, definition


def _accept_target(definition: dict[str, Any]) -> dict[str, Any]:
    target = definition["target"]
    program = target.get("program")
    if isinstance(program, str) and program:
        definition["target_accepted_digest"] = targets.program_digest(program)
    else:
        definition["target_accepted_digest"] = None
    return definition


def cmd_define(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    try:
        document = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"define: cannot read {args.file}: {error}", file=sys.stderr)
        return 2
    try:
        clean = validate_definition(document, now_ms=getattr(args, 'now_ms', None) or _ms_now())
    except DefinitionError as error:
        print(f"define: invalid: {error}", file=sys.stderr)
        return 2
    aid = automation_id(clean["name"])
    if len(list(paths["definitions"].glob("*.json"))) >= store.read_policy(
            paths["definitions"].parent).get("max_automations", 256):
        print("define: automation limit reached", file=sys.stderr)
        return 2
    try:
        clean = _accept_target(clean)
    except targets.TargetError as error:
        print(f"define: target unreachable: {error}", file=sys.stderr)
        return 2
    store.write_json(paths["definitions"] / f"{aid}.json", clean)
    if not (paths["state"] / f"{aid}.json").exists():
        store.write_json(paths["state"] / f"{aid}.json",
                         fresh_state(aid, clean["revision"]))
    print(json.dumps({"automation": aid, "name": clean["name"],
                      "revision": clean["revision"]}, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    rows = []
    for path in sorted(paths["definitions"].glob("*.json")):
        definition = store.read_json(path)
        if not isinstance(definition, dict):
            continue
        aid = automation_id(definition["name"])
        state = store.read_json(paths["state"] / f"{aid}.json") or {}
        rows.append({"automation": aid, "name": definition["name"],
                     "revision": definition["revision"],
                     "lifecycle": definition["lifecycle"],
                     "trigger": definition["trigger"]["kind"],
                     "target": definition["target"]["kind"],
                     "prev_seq": state.get("prev_seq", 0),
                     "wakeup_ms": state.get("wakeup_ms", 0)})
    print(json.dumps(rows, indent=2))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    try:
        aid, definition = _load_definition(paths, args.name)
    except DefinitionError as error:
        print(f"show: {error}", file=sys.stderr)
        return 2
    state = store.read_json(paths["state"] / f"{aid}.json") or {}
    recent = store.read_jsonl(paths["occurrences"] / f"{aid}.jsonl",
                              limit=args.limit)
    print(json.dumps({"definition": definition, "state": state,
                      "recent_occurrences": recent}, indent=2))
    return 0


def _set_lifecycle(args: argparse.Namespace, lifecycle: str) -> int:
    paths = store.ensure(_state_dir(args))
    try:
        aid, definition = _load_definition(paths, args.name)
    except DefinitionError as error:
        print(f"{lifecycle}: {error}", file=sys.stderr)
        return 2
    definition["lifecycle"] = lifecycle
    store.write_json(paths["definitions"] / f"{aid}.json", definition)
    print(json.dumps({"automation": aid, "lifecycle": lifecycle}))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    try:
        aid, old = _load_definition(paths, args.name)
    except DefinitionError as error:
        print(f"update: {error}", file=sys.stderr)
        return 2
    try:
        document = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"update: cannot read {args.file}: {error}", file=sys.stderr)
        return 2
    if document.get("name", old["name"]) != old["name"]:
        print("update: rename via delete + define", file=sys.stderr)
        return 2
    document["name"] = old["name"]
    document["created_ms"] = old.get("created_ms", 0)
    try:
        clean = validate_definition(document, now_ms=getattr(args, 'now_ms', None) or _ms_now())
    except DefinitionError as error:
        print(f"update: invalid: {error}", file=sys.stderr)
        return 2
    clean["revision"] = int(old.get("revision", 1)) + 1
    # A new revision restarts sequencing: past occurrences keep their
    # revision-stamped identities, future ones cannot collide.
    clean["lifecycle"] = "active" if clean["lifecycle"] == "active" else clean[
        "lifecycle"]
    try:
        clean = _accept_target(clean)
    except targets.TargetError as error:
        print(f"update: target unreachable: {error}", file=sys.stderr)
        return 2
    store.write_json(paths["definitions"] / f"{aid}.json", clean)
    state = store.read_json(paths["state"] / f"{aid}.json") or fresh_state(
        aid, clean["revision"])
    # A new revision restarts history: re-baseline sequencing, timing,
    # and the condition edge from now. Past occurrences keep their
    # revision-stamped identities and are never rewritten.
    state["definition_revision"] = clean["revision"]
    state["prev_seq"] = 0
    state["last_at_ms"] = 0
    state["last_eval_ms"] = 0
    state["last_condition"] = 0
    state["last_block_reason"] = None
    store.write_json(paths["state"] / f"{aid}.json", state)
    print(json.dumps({"automation": aid, "revision": clean["revision"]},
                     indent=2))
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    try:
        aid, definition = _load_definition(paths, args.name)
    except DefinitionError as error:
        print(f"delete: {error}", file=sys.stderr)
        return 2
    (paths["definitions"] / f"{aid}.json").unlink(missing_ok=True)
    (paths["state"] / f"{aid}.json").unlink(missing_ok=True)
    if args.purge:
        (paths["occurrences"] / f"{aid}.jsonl").unlink(missing_ok=True)
    else:
        print(f"note: occurrence history retained; use --purge to erase",
              file=sys.stderr)
    print(json.dumps({"automation": aid, "deleted": True,
                      "purged": bool(args.purge)}))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        document = json.loads(Path(args.file).read_text(encoding="utf-8"))
        clean = validate_definition(document)
    except (OSError, ValueError, DefinitionError) as error:
        print(f"validate: invalid: {error}", file=sys.stderr)
        return 1
    if args.verbose:
        print(json.dumps(clean, indent=2))
    else:
        print(f"valid: {clean['name']}")
    return 0


def cmd_revalidate(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    try:
        aid, definition = _load_definition(paths, args.name)
    except DefinitionError as error:
        print(f"revalidate: {error}", file=sys.stderr)
        return 2
    try:
        definition = _accept_target(definition)
    except targets.TargetError as error:
        print(f"revalidate: {error}", file=sys.stderr)
        return 2
    definition["revision"] = int(definition.get("revision", 1)) + 1
    definition["updated_ms"] = getattr(args, 'now_ms', None) or _ms_now()
    store.write_json(paths["definitions"] / f"{aid}.json", definition)
    state = store.read_json(paths["state"] / f"{aid}.json") or {}
    state["last_block_reason"] = None
    store.write_json(paths["state"] / f"{aid}.json", state)
    print(json.dumps({"automation": aid, "revision": definition["revision"],
                      "accepted": definition["target_accepted_digest"]},
                     indent=2))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    rows = []
    names = [args.name] if args.name else None
    for path in sorted(paths["definitions"].glob("*.json")):
        definition = store.read_json(path)
        if not isinstance(definition, dict):
            continue
        if names and definition["name"] not in names:
            continue
        aid = automation_id(definition["name"])
        state = store.read_json(paths["state"] / f"{aid}.json") or {}
        rows.append({"automation": aid, "name": definition["name"],
                     "lifecycle": definition["lifecycle"],
                     "wakeup_ms": state.get("wakeup_ms", 0),
                     "prev_seq": state.get("prev_seq", 0)})
    print(json.dumps(rows, indent=2))
    return 0


def cmd_occurrences(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    try:
        aid, _ = _load_definition(paths, args.name)
    except DefinitionError as error:
        print(f"occurrences: {error}", file=sys.stderr)
        return 2
    print(json.dumps(store.read_jsonl(paths["occurrences"] / f"{aid}.jsonl",
                                      limit=args.limit), indent=2))
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    paths = store.ensure(_state_dir(args))
    try:
        aid, definition = _load_definition(paths, args.name)
    except DefinitionError as error:
        print(f"why: {error}", file=sys.stderr)
        return 2
    state = store.read_json(paths["state"] / f"{aid}.json") or {}
    recent = store.read_jsonl(paths["occurrences"] / f"{aid}.jsonl", limit=1)
    last = recent[-1] if recent else None
    explanation = {
        "automation": aid,
        "lifecycle": definition["lifecycle"],
        "trigger": definition["trigger"],
        "target": {"kind": definition["target"]["kind"]},
        "prev_seq": state.get("prev_seq", 0),
        "last_at_ms": state.get("last_at_ms", 0),
        "last_condition": state.get("last_condition", 0),
        "wakeup_ms": state.get("wakeup_ms", 0),
        "pending": state.get("pending"),
        "eval_errors": state.get("eval_errors", 0),
        "last_block_reason": state.get("last_block_reason"),
        "last_occurrence": last,
    }
    print(json.dumps(explanation, indent=2))
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    report = engine.tick(state_dir=state_dir, mncs=_mncs(args),
                         now_ms=getattr(args, 'now_ms', None), dry_run=args.dry_run,
                         execute=not args.no_execute)
    print(json.dumps(report, indent=2))
    return 0 if not report["errors"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    engine.recover(state_dir=state_dir)
    engine.run_loop(state_dir=state_dir, mncs=_mncs(args),
                    max_idle_s=args.max_idle_s, max_passes=args.passes)
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    markers = engine.recover(state_dir=_state_dir(args))
    print(json.dumps(markers, indent=2))
    return 0


def _ms_now() -> int:
    return int(time.time() * 1000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mncs-automation",
                                     description=__doc__)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--mncs", default=None)
    parser.add_argument("--now-ms", type=int, default=None)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", default=argparse.SUPPRESS)
    common.add_argument("--mncs", default=argparse.SUPPRESS)
    common.add_argument("--now-ms", type=int, default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    define = sub.add_parser("define", parents=[common])
    define.add_argument("--file", required=True)
    define.set_defaults(func=cmd_define)

    sub.add_parser("list", parents=[common]).set_defaults(func=cmd_list)

    show = sub.add_parser("show", parents=[common])
    show.add_argument("name")
    show.add_argument("--limit", type=int, default=5)
    show.set_defaults(func=cmd_show)

    enable = sub.add_parser("enable", parents=[common])
    enable.add_argument("name")
    enable.set_defaults(func=lambda a: _set_lifecycle(a, "active"))

    disable = sub.add_parser("disable", parents=[common])
    disable.add_argument("name")
    disable.set_defaults(func=lambda a: _set_lifecycle(a, "paused"))

    update = sub.add_parser("update", parents=[common])
    update.add_argument("name")
    update.add_argument("--file", required=True)
    update.set_defaults(func=cmd_update)

    delete = sub.add_parser("delete", parents=[common])
    delete.add_argument("name")
    delete.add_argument("--purge", action="store_true")
    delete.set_defaults(func=cmd_delete)

    validate = sub.add_parser("validate", parents=[common])
    validate.add_argument("--file", required=True)
    validate.add_argument("--verbose", action="store_true")
    validate.set_defaults(func=cmd_validate)

    revalidate = sub.add_parser("revalidate", parents=[common])
    revalidate.add_argument("name")
    revalidate.set_defaults(func=cmd_revalidate)

    next_cmd = sub.add_parser("next", parents=[common])
    next_cmd.add_argument("name", nargs="?")
    next_cmd.set_defaults(func=cmd_next)

    occurrences = sub.add_parser("occurrences", parents=[common])
    occurrences.add_argument("name")
    occurrences.add_argument("--limit", type=int, default=20)
    occurrences.set_defaults(func=cmd_occurrences)

    why = sub.add_parser("why", parents=[common])
    why.add_argument("name")
    why.set_defaults(func=cmd_why)

    tick = sub.add_parser("tick", parents=[common])
    tick.add_argument("--dry-run", action="store_true")
    tick.add_argument("--no-execute", action="store_true")
    tick.set_defaults(func=cmd_tick)

    run = sub.add_parser("run", parents=[common])
    run.add_argument("--max-idle-s", type=int, default=60)
    run.add_argument("--passes", type=int, default=None)
    run.set_defaults(func=cmd_run)

    sub.add_parser("recover", parents=[common]).set_defaults(func=cmd_recover)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
