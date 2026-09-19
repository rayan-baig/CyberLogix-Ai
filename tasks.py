"""Things somebody agreed to do, and whether they did it.

The meeting summariser already turns a transcript into action items with
an owner and a priority, and then throws them away: the caller gets a
JSON reply and nothing is kept. So the one useful output of the whole
feature -- the list of what people agreed to -- lives for as long as the
browser tab does.

This is the other half. An action item becomes a row with an owner and a
date, it shows up on the console until it is done, and it goes stale
loudly rather than quietly.

Deliberately free to run. Nothing here calls a model, a mail host or a
telephony provider: a task is created, listed, finished or dropped, and
all of that is the database and the browser. The summariser needs a paid
API key to be any good; this works with a key, without one, and for
people who never hold a meeting and just type the thing in.

Priorities are the summariser's own vocabulary -- High, Med, Low -- kept
rather than renamed, so an item saved from a transcript reads the same
as the transcript's own output.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounts import require_role
from auth import require_tenant, write_audit
from store import STORE, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.tasks")

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])

TASK_KIND = "task"

PRIORITIES = ("High", "Med", "Low")

# How long a task with no date given gets before it is considered late.
# A week, because the alternative is either no deadline at all -- which
# makes "overdue" meaningless and the list unsortable -- or asking for a
# date at the moment somebody is trying to save eleven of them at once.
DEFAULT_DAYS = 7


def _today() -> date:
    return utc_now().date()


def _parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    ).date()


def tasks_for(tenant_id: str) -> List[Dict[str, Any]]:
    rows = [r for r in STORE._db.all(TASK_KIND)
            if r.get("tenant_id") == tenant_id]
    # Open first, then by how late; finished ones sink to the bottom in
    # the order they were finished.
    return sorted(rows, key=lambda r: (
        bool(r.get("done_at")),
        r.get("due_on") or "9999-99-99",
        r.get("created_at") or "",
    ))


def days_left(row: Dict[str, Any], today: Optional[date] = None) -> Optional[int]:
    due = row.get("due_on")
    if not due:
        return None
    try:
        return (_parse_day(due) - (today or _today())).days
    except ValueError:
        return None


def state_of(row: Dict[str, Any], today: Optional[date] = None) -> str:
    if row.get("done_at"):
        return "done"
    left = days_left(row, today)
    if left is None:
        return "open"
    if left < 0:
        return "late"
    if left == 0:
        return "today"
    return "open"


def task_state(row: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    left = days_left(row, today)
    return {
        **row,
        "state": state_of(row, today),
        "days_left": left,
        "when": (
            "Done" if row.get("done_at")
            else "No date" if left is None
            else f"{abs(left)} day(s) late" if left < 0
            else "Due today" if left == 0
            else f"in {left} day(s)"
        ),
    }


def summary(tenant_id: str, today: Optional[date] = None) -> Dict[str, Any]:
    """What is outstanding, for the console band and the daily digest."""
    today = today or _today()
    rows = [task_state(r, today) for r in tasks_for(tenant_id)]
    open_rows = [r for r in rows if r["state"] != "done"]
    late = [r for r in open_rows if r["state"] == "late"]
    return {
        "tasks": rows,
        "count": len(rows),
        "open": len(open_rows),
        "late": len(late),
        "due_today": len([r for r in open_rows if r["state"] == "today"]),
        "oldest_late": late[-1]["title"] if late else None,
        "note": (
            f"{len(late)} thing(s) somebody agreed to do are past their "
            "date."
            if late else
            f"{len(open_rows)} thing(s) still to do, none of them late."
            if open_rows else
            "Nothing outstanding."
        ),
    }


def fleet_summary(today: Optional[date] = None) -> Dict[str, Any]:
    """Every account in one pass, for the operator's daily digest.

    One pass rather than summary() per tenant, which full-scans the table
    each time -- the same mistake the licence digest made, and the reason
    the whole suite went from under two minutes to not finishing.
    """
    today = today or _today()
    open_count = late_count = 0
    for row in STORE._db.all(TASK_KIND):
        if row.get("done_at"):
            continue
        open_count += 1
        if state_of(row, today) == "late":
            late_count += 1
    return {"open": open_count, "late": late_count}


# --- routes ----------------------------------------------------------------


class NewTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=2, max_length=300)
    owner: str = Field("", max_length=120,
                       description="Who agreed to do it.")
    priority: str = Field("Med", max_length=10)
    due_on: Optional[str] = Field(
        None, description="YYYY-MM-DD. Left out, it gets a week.")
    source: str = Field("", max_length=160,
                        description="Where it came from, e.g. a meeting.")

    @field_validator("priority")
    @classmethod
    def a_known_priority(cls, value: str) -> str:
        found = {p.lower(): p for p in PRIORITIES}.get(value.strip().lower())
        if found is None:
            raise ValueError(f"priority must be one of {list(PRIORITIES)}")
        return found

    @field_validator("due_on")
    @classmethod
    def a_real_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            _parse_day(value)
        except ValueError:
            raise ValueError("due_on must look like 2026-03-14")
        return value


class TasksFromMeeting(BaseModel):
    """What the summariser hands back, saved rather than discarded."""

    model_config = ConfigDict(extra="forbid")

    action_items: List[NewTask] = Field(..., max_length=100)
    source: str = Field("", max_length=160)


def _make(tenant: Tenant, payload: NewTask, source: str = "") -> Dict[str, Any]:
    task_id = STORE._next_id("TSK")
    due = payload.due_on or (
        _today() + timedelta(days=DEFAULT_DAYS)).strftime("%Y-%m-%d")
    row = {
        "task_id": task_id,
        "tenant_id": tenant.tenant_id,
        "title": payload.title.strip(),
        "owner": payload.owner.strip(),
        "priority": payload.priority,
        "due_on": due,
        "source": (payload.source or source).strip(),
        "created_at": iso(utc_now()),
        "done_at": None,
        "done_by": "",
    }
    STORE._db.put(TASK_KIND, task_id, row)
    return row


@router.get("")
def list_tasks(
    include_done: bool = Query(True),
    tenant: Tenant = Depends(require_tenant),
):
    """Everything outstanding, worst first."""
    seen = summary(tenant.tenant_id)
    if not include_done:
        seen = {**seen, "tasks": [t for t in seen["tasks"]
                                  if t["state"] != "done"]}
    return seen


@router.post("", status_code=status.HTTP_201_CREATED)
def add_task(
    payload: NewTask,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Write down one thing somebody agreed to do."""
    row = _make(tenant, payload)
    write_audit(tenant, operator, "task.added", row["title"])
    return {"task": task_state(row)}


@router.post("/from-meeting", status_code=status.HTTP_201_CREATED)
def save_meeting_actions(
    payload: TasksFromMeeting,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Keep what a meeting agreed, instead of printing it once.

    The summariser returns `action_items_assigned` and the caller was
    left holding it. This is where that list is meant to go.
    """
    made = [_make(tenant, item, payload.source)
            for item in payload.action_items]
    write_audit(tenant, operator, "task.from_meeting",
                f"{len(made)} item(s) from {payload.source or 'a meeting'}")
    return {"saved": len(made),
            "tasks": [task_state(row) for row in made],
            "outstanding": summary(tenant.tenant_id)}


@router.post("/{task_id}/done")
def finish_task(
    task_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Mark it done. Who and when are recorded, because that is the point."""
    row = STORE._db.get(TASK_KIND, task_id)
    if row is None or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such task on this account.")
    row = {**row, "done_at": iso(utc_now()), "done_by": operator.full_name}
    STORE._db.put(TASK_KIND, task_id, row)
    write_audit(tenant, operator, "task.done", row["title"])
    return {"task": task_state(row), "outstanding": summary(tenant.tenant_id)}


@router.post("/{task_id}/reopen")
def reopen_task(
    task_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Put it back. Ticked by mistake is commoner than done by mistake."""
    row = STORE._db.get(TASK_KIND, task_id)
    if row is None or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such task on this account.")
    row = {**row, "done_at": None, "done_by": ""}
    STORE._db.put(TASK_KIND, task_id, row)
    write_audit(tenant, operator, "task.reopened", row["title"])
    return {"task": task_state(row), "outstanding": summary(tenant.tenant_id)}


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def drop_task(
    task_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Owner only: a deleted task leaves no record that it was agreed."""
    row = STORE._db.get(TASK_KIND, task_id)
    if row is None or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such task on this account.")
    STORE._db.delete(TASK_KIND, task_id)
    write_audit(tenant, operator, "task.dropped", row["title"])
    return None
