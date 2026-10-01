"""Codes shared with native/mncs/automation/*.mncs.

These integers cross the `mncs call` boundary. They duplicate the native
`_code()` functions by design; tests/test_automation.py cross-checks every
value against the native modules, so drift fails loudly instead of
silently mis-deciding.
"""

from __future__ import annotations

DEFINITION_SCHEMA = "mncs.automation-definition/1"
STATE_SCHEMA = "mncs.automation-state/1"

SCHED_PINNED = 0
SCHED_INTERVAL = 1
SCHED_DAILY_UTC = 2
SCHED_DAILY_LOCAL = 3

TRIG_SCHEDULE = 0
TRIG_WATCH = 1

COND_FALSE = 0
COND_TRUE = 1
COND_UNKNOWN = 2

EDGE_RISING = 0
EDGE_WHILE_TRUE = 1

MISFIRE_FIRE_NOW = 0
MISFIRE_SKIP = 1
MISFIRE_CATCH_UP = 2
MISFIRE_MARK_MISSED = 3

LIFECYCLE_ACTIVE = 0
LIFECYCLE_PAUSED = 1
LIFECYCLE_COMPLETED = 2
LIFECYCLE_EXPIRED = 3
LIFECYCLE_INVALID = 4
LIFECYCLE_BLOCKED = 5

REASON_DUE_FIRE = 0
REASON_WAITING = 1
REASON_ALREADY_FIRED = 2
REASON_COND_FALSE = 3
REASON_COND_UNKNOWN = 4
REASON_EDGE_CLOSED = 5
REASON_MISFIRE_SKIPPED = 6
# Reserved, never emitted: the engine holds the state lock across
# evaluate+execute, so two invocations of one automation cannot overlap
# on a single node by construction. Overlap policy would only matter
# with async executors (out of scope).
REASON_CONCURRENCY = 7
REASON_STALE_TARGET = 8
REASON_TERMINAL = 9

REASON_NAMES = {
    0: "due-fire",
    1: "waiting",
    2: "already-fired",
    3: "cond-false",
    4: "cond-unknown",
    5: "edge-closed",
    6: "misfire-skipped",
    7: "concurrency-suppressed",
    8: "stale-target",
    9: "terminal",
}

SCHEDULE_KINDS = ("one-shot", "interval", "daily-utc", "daily-local")
MISFIRE_POLICIES = ("fire-now", "skip", "catch-up", "mark-missed")
LIFECYCLES = ("active", "paused", "completed", "expired", "invalid", "blocked")
EDGES = ("rising", "while-true")
CONDITION_KINDS = ("test-verdict", "path-changed")
TARGET_KINDS = ("mncs-test", "mncs-call", "doctor",
                "environment-reconcile")

MISFIRE_CODES = {
    "fire-now": MISFIRE_FIRE_NOW,
    "skip": MISFIRE_SKIP,
    "catch-up": MISFIRE_CATCH_UP,
    "mark-missed": MISFIRE_MARK_MISSED,
}

# Reconciliation (native/mncs/automation/reconcile.mncs). Verification
# verdict codes mirror COND_* positions: fail is False, pass is True.
RECONCILE_SCHEMA = "mncs.reconcile-decision/1"

VERDICT_FAIL = 0
VERDICT_PASS = 1
VERDICT_UNKNOWN = 2

RECON_UP_TO_DATE = 0
RECON_REGENERATE = 1
RECON_AWAIT_VERIFICATION = 2
RECON_BLOCKED = 3
RECON_FAILED_VERIFICATION = 4

RECON_REASON_CURRENT = 0
RECON_REASON_CANONICAL_ADVANCED = 1
RECON_REASON_VERIFICATION_UNKNOWN = 2
RECON_REASON_VERIFICATION_FAILED = 3
RECON_REASON_OBSERVED_AHEAD = 4

RECON_PUBLISH_NONE = 0
RECON_PUBLISH_THRESHOLD = 1
RECON_PUBLISH_LATENCY = 2

RECON_ADOPTED = 0
RECON_ADOPT_STALE = 1
RECON_ADOPT_REGRESSION = 2

RECON_ACTION_NAMES = {
    0: "up-to-date",
    1: "regenerate",
    2: "await-verification",
    3: "blocked",
    4: "failed-verification",
}

RECON_REASON_NAMES = {
    0: "current",
    1: "canonical-advanced",
    2: "verification-unknown",
    3: "verification-failed",
    4: "observed-ahead",
}

VERDICT_NAMES = {
    0: "fail",
    1: "pass",
    2: "unknown",
}
