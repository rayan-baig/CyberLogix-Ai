"""Who is on call right now: rotations and quiet hours.

Two ways a business narrows its roster by the clock.

**A rotation** takes turns. Its members are on call one at a time, for a
shift of a whole number of days, handing over at a set local time ("08:00
every Monday" is a seven-day shift starting on a Monday). Contacts who are
in no rotation are on call all the time, as before.

**Quiet hours** are a contact's own: between, say, 22:00 and 07:00 they
are not texted, and are rung only after everybody awake has been tried.

The rule both obey is that neither can make a breach reach nobody. When
everyone a text would go to is in quiet hours, they are texted anyway; and
the voice ladder still ends with the people asleep, so an alert nobody
answers works its way to them. A rotation with nobody in it, or with the
person on shift muted, is named as a gap before an alert needs it
(contacts.roster_gaps).

Handovers are worked out in the rotation's own time zone, on the wall
clock, so "08:00" means 08:00 there in summer and in winter. On the night
the clocks go back the handover happens at the first 01:30, not again at
the second; on the night they go forward, a handover at a time that does
not exist that night (02:30) happens the moment the clock jumps past it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROTATION_KIND = "rotation"
ESTATE = "estate"

MAX_SHIFT_DAYS = 28


def zone(name: str) -> ZoneInfo:
    """The named IANA time zone, or ValueError."""
    try:
        return ZoneInfo((name or "").strip())
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(
            f"'{name}' is not a time zone this server knows. Use a name such "
            "as Europe/London or America/New_York."
        ) from exc


def clock(text: str) -> time:
    """'HH:MM' on a 24-hour clock, or ValueError."""
    try:
        hours, minutes = (int(part) for part in (text or "").strip().split(":"))
        return time(hours, minutes)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"'{text}' is not a time like 08:00 or 22:30.") from exc


def _wall(moment: datetime, tz: ZoneInfo) -> datetime:
    """The local wall-clock reading at an instant, without its zone."""
    return moment.astimezone(tz).replace(tzinfo=None)


def handover_at(day: date, at: time, tz: ZoneInfo) -> datetime:
    """The instant, in UTC, the local clock first reads `at` on `day`.

    Not simply `datetime.combine(...)`: on the night the clocks go back
    the time happens twice and the first is the handover, and on the night
    they go forward it may not happen at all, in which case the handover
    is the moment the clock jumps past it.
    """
    wanted = datetime.combine(day, at)
    first = wanted.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
    second = wanted.replace(tzinfo=tz, fold=1).astimezone(timezone.utc)
    candidate = min(first, second)
    if _wall(candidate, tz) == wanted:
        return candidate
    # In a gap. Walk forward a minute at a time (a gap is an hour or two)
    # to the first instant whose wall clock is at or past the handover.
    probe = candidate.replace(second=0, microsecond=0)
    for _ in range(24 * 60):
        if _wall(probe, tz) >= wanted:
            return probe
        probe += timedelta(minutes=1)
    return max(first, second)


@dataclass
class Rotation:
    tenant_id: str
    timezone: str
    handover: str
    shift_days: int
    starts_on: str
    members: List[str] = field(default_factory=list)
    site_id: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def rec_id(self) -> str:
        return rotation_id(self.tenant_id, self.site_id)

    def to_row(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "site_id": self.site_id,
            "timezone": self.timezone,
            "handover": self.handover,
            "shift_days": self.shift_days,
            "starts_on": self.starts_on,
            "members": list(self.members),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Rotation":
        return cls(
            tenant_id=row["tenant_id"],
            site_id=row.get("site_id"),
            timezone=row["timezone"],
            handover=row["handover"],
            shift_days=int(row.get("shift_days", 1)),
            starts_on=row["starts_on"],
            members=list(row.get("members") or []),
            updated_at=row.get("updated_at"),
        )

    def _shift(self, now: datetime) -> "tuple[int, datetime]":
        """(index of the shift running at `now`, when it started)."""
        tz = zone(self.timezone)
        at = clock(self.handover)
        start = date.fromisoformat(self.starts_on)
        today = now.astimezone(tz).date()
        # The most recent handover day at or before now.
        days = (today - start).days
        day_index = days // self.shift_days
        began_on = start + timedelta(days=day_index * self.shift_days)
        began = handover_at(began_on, at, tz)
        if began > now:
            day_index -= 1
            began = handover_at(
                start + timedelta(days=day_index * self.shift_days), at, tz)
        return day_index, began

    def on_shift(self, now: datetime) -> Optional[str]:
        """The contact id whose turn it is, or None for an empty rotation."""
        if not self.members:
            return None
        index, _ = self._shift(now)
        return self.members[index % len(self.members)]

    def next_handover(self, now: datetime) -> datetime:
        tz = zone(self.timezone)
        index, _ = self._shift(now)
        start = date.fromisoformat(self.starts_on)
        return handover_at(start + timedelta(days=(index + 1) * self.shift_days),
                           clock(self.handover), tz)

    def schedule(self, now: datetime, shifts: int = 6) -> List[Dict[str, Any]]:
        """The shift running now and the next few, for the console."""
        if not self.members:
            return []
        tz = zone(self.timezone)
        index, began = self._shift(now)
        start = date.fromisoformat(self.starts_on)
        out = []
        for n in range(index, index + shifts):
            begins = handover_at(start + timedelta(days=n * self.shift_days),
                                 clock(self.handover), tz)
            ends = handover_at(start + timedelta(days=(n + 1) * self.shift_days),
                               clock(self.handover), tz)
            out.append({
                "contact_id": self.members[n % len(self.members)],
                "starts_at": begins.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ends_at": ends.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "starts_local": begins.astimezone(tz).strftime("%a %d %b %H:%M %Z"),
                "ends_local": ends.astimezone(tz).strftime("%a %d %b %H:%M %Z"),
            })
        return out


def rotation_id(tenant_id: str, site_id: Optional[str]) -> str:
    return f"{tenant_id}:{site_id or ESTATE}"


def rotations_for(db, tenant_id: str) -> List[Rotation]:
    return [Rotation.from_row(row) for row in db.all(ROTATION_KIND)
            if row.get("tenant_id") == tenant_id]


def in_quiet_hours(quiet: Optional[Dict[str, str]], now: datetime) -> bool:
    """Whether `now` falls in a contact's quiet hours.

    `quiet` is {"start": "22:00", "end": "07:00", "timezone": "..."}; a
    window that ends earlier than it starts runs over midnight. A window
    that cannot be read is treated as no quiet hours: a typo must never
    be the reason somebody was not woken.
    """
    if not quiet:
        return False
    try:
        tz = zone(quiet["timezone"])
        start, end = clock(quiet["start"]), clock(quiet["end"])
    except (KeyError, ValueError):
        return False
    local = now.astimezone(tz).time().replace(second=0, microsecond=0)
    if start == end:
        return False
    if start < end:
        return start <= local < end
    return local >= start or local < end


def on_duty(contacts: List[Any], rotations: List[Rotation], now: datetime) -> List[Any]:
    """Contacts on call now: everyone not in a rotation, plus whoever's turn
    it is in each rotation."""
    in_a_rotation = {m for r in rotations for m in r.members}
    turns = {r.on_shift(now) for r in rotations}
    return [c for c in contacts
            if c.contact_id not in in_a_rotation or c.contact_id in turns]


def awake_first(contacts: List[Any], now: datetime) -> "tuple[List[Any], List[Any]]":
    """Split contacts into (awake, in quiet hours), keeping their order."""
    awake = [c for c in contacts if not in_quiet_hours(getattr(c, "quiet_hours", None), now)]
    asleep = [c for c in contacts if c not in awake]
    return awake, asleep
