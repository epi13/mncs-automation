"""Wall-clock bridge: IANA timezones, DST policy, UTC normalization.

The language has no timezone database, so local-time recurrence resolves
here -- but the bridge only maps (timezone, local time, date) to an exact
UTC instant and a stable local-day serial. All firing policy stays native.

Policies (explicit, persisted, tested):
- nonexistent local times (spring-forward gap): shift forward to the
  first valid instant at or after the requested wall time;
- ambiguous local times (fall-back overlap): take the FIRST occurrence
  (fold=0);
- occurrence identity uses the UTC instant plus the local-day serial, so
  a machine move across timezones cannot silently change semantics: the
  IANA name travels with the definition.

`next_daily_local` answers: the first valid instant strictly after
`after_utc_ms` whose local wall time is hour:minute in `tz`.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any
from zoneinfo import ZoneInfo

DAY_MS = 86_400_000
EPOCH_DATE = _dt.date(1970, 1, 1)


def local_serial(local_date: _dt.date) -> int:
    return (local_date - EPOCH_DATE).days


def resolve_wall_time(tz_name: str, local_date: _dt.date,
                      hour: int, minute: int) -> tuple[int, str]:
    """Return (utc_ms, note) for a wall time under the DST policy."""
    zone = ZoneInfo(tz_name)
    utc = _dt.timezone.utc
    naive = _dt.datetime(local_date.year, local_date.month, local_date.day,
                         hour, minute)
    first_utc = naive.replace(tzinfo=zone, fold=0).astimezone(utc)
    second_utc = naive.replace(tzinfo=zone, fold=1).astimezone(utc)
    first_back = first_utc.astimezone(zone)
    second_back = second_utc.astimezone(zone)
    wanted = (hour, minute)
    if ((first_back.hour, first_back.minute) == wanted
            and (second_back.hour, second_back.minute) == wanted
            and first_utc != second_utc):
        return int(first_utc.timestamp() * 1000), "overlap-first"
    if (first_back.hour, first_back.minute) == wanted:
        return int(first_utc.timestamp() * 1000), "ok"
    # Gap (spring forward): walk forward minute by minute to the first
    # valid wall time. Gaps are under 24h everywhere in the IANA data.
    probe = naive
    for _ in range(24 * 60 + 1):
        probe += _dt.timedelta(minutes=1)
        candidate = probe.replace(tzinfo=zone, fold=0)
        instant = candidate.astimezone(utc)
        if instant.astimezone(zone).replace(fold=0) == candidate:
            return int(instant.timestamp() * 1000), "gap-shifted-forward"
    raise ValueError(f"no valid wall time near {naive} in {tz_name}")


def next_daily_local(*, tz_name: str, hour: int, minute: int,
                     after_utc_ms: int) -> dict[str, Any]:
    """First valid (utc_ms, local serial) strictly after `after_utc_ms`."""
    zone = ZoneInfo(tz_name)
    after_utc = _dt.datetime.fromtimestamp(after_utc_ms / 1000,
                                           tz=_dt.timezone.utc)
    local_day = after_utc.astimezone(zone).date()
    for _ in range(366 + 2):
        utc_ms, note = resolve_wall_time(tz_name, local_day, hour, minute)
        if utc_ms > after_utc_ms:
            return {"at_utc_ms": utc_ms, "serial": local_serial(local_day),
                    "note": note, "local_date": local_day.isoformat()}
        local_day += _dt.timedelta(days=1)
    raise ValueError("no daily occurrence within a year (unreachable)")
