"""The one file whose loss is the company.

Every tenant, every sensor, every reading, every invoice and the whole
hash-chained compliance vault live in one SQLite file. It survives a
restart. It does not survive a deleted volume, a mistyped `rm`, a
migration that goes sideways, or the disk it sits on — and the vault is
the part that hurts most, because a customer's evidence that their
freezer held temperature for two years cannot be reconstructed from
anywhere else. An insurer will not accept "we lost it".

Nothing here was backing anything up. The README said to point
`CYBERLOGIX_DB_PATH` at a mounted volume, which protects against the
container going away and against nothing else.

Three deliberate choices:

* **SQLite's own online backup**, never a file copy. `cp` on a live
  database can catch a torn page or an unwritten WAL, and produces a file
  that looks like a backup right up until the afternoon somebody needs
  it.
* **Every snapshot is verified before it counts.** It is reopened, its
  integrity checked and its rows counted. An unread backup is a guess.
* **Retention is by count, oldest deleted first**, so the directory
  cannot quietly fill the disk that the live database is also on — which
  would turn a backup policy into an outage.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from auth import require_platform_admin
from store import STORE, iso, utc_now

logger = logging.getLogger("cyberlogix.backup")

router = APIRouter(prefix="/api/admin", tags=["Backups"])

# Where snapshots go. Default is a directory beside the database, which
# is better than nothing and worse than a different disk — the status
# endpoint says so rather than letting a deployment believe otherwise.
BACKUP_DIR = os.environ.get("CYBERLOGIX_BACKUP_DIR", "").strip()

# How many to keep. Fourteen daily snapshots is two weeks to notice that
# something went wrong, which is about how long it takes.
try:
    BACKUP_KEEP = max(1, int(os.environ.get("CYBERLOGIX_BACKUP_KEEP", "14")))
except ValueError:
    BACKUP_KEEP = 14

PREFIX = "cyberlogix-"
SUFFIX = ".sqlite3"


def _directory() -> Optional[Path]:
    """Where snapshots belong, or None when there is nowhere to put them.

    An in-memory database has no beside-it to default to, and backing up
    the test suite's store into the working directory on every pass is
    not something a default should do quietly.
    """
    if BACKUP_DIR:
        return Path(BACKUP_DIR)
    live = STORE._db.path
    if live == ":memory:":
        return None
    return Path(live).resolve().parent / "backups"


def existing() -> List[Path]:
    """Snapshots on disk, oldest first. Named so the sort is by time."""
    directory = _directory()
    if directory is None or not directory.is_dir():
        return []
    return sorted(directory.glob(f"{PREFIX}*{SUFFIX}"))


def take(now: Optional[datetime] = None, label: str = "") -> Dict[str, Any]:
    """Write, verify and prune. Returns what happened, never raises.

    Never raises because the daily one runs inside the unattended pass,
    and a backup failure must not be able to stop the company invoicing.
    It is reported loudly instead: a silent backup failure is worse than
    no backup at all, because somebody is relying on it.
    """
    now = now or utc_now()
    directory = _directory()
    if directory is None:
        return {
            "taken": False,
            "status": "nowhere_to_write",
            "detail": (
                "The database is in memory, so there is nothing durable to "
                "snapshot. Set CYBERLOGIX_DB_PATH, and CYBERLOGIX_BACKUP_DIR "
                "to a different disk."
            ),
        }

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{label}" if label else ""
    target = directory / f"{PREFIX}{stamp}{suffix}{SUFFIX}"

    try:
        directory.mkdir(parents=True, exist_ok=True)
        result = STORE._db.snapshot(str(target))
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        logger.exception("Backup to %s failed (%s).", target, exc)
        return {
            "taken": False,
            "status": "failed",
            "detail": f"Could not write a snapshot to {target}: {exc}",
        }

    pruned = prune()
    size = target.stat().st_size
    logger.info(
        "Backup: %s, %d records, %.1f KiB, %d pruned.",
        target.name, result["records"], size / 1024.0, len(pruned),
    )
    return {
        "taken": True,
        "status": "ok",
        "path": str(target),
        "size_bytes": size,
        "records": result["records"],
        "kinds": result["kinds"],
        "pruned": [p.name for p in pruned],
        "taken_at": iso(now),
    }


def verify(path: str) -> Dict[str, Any]:
    """Open a snapshot and confirm it is a database with rows in it.

    A backup nobody has opened is a file, not a backup. This is what
    `restore` runs first, and it is exposed on its own so the check can
    be made on a schedule rather than on the morning it matters.
    """
    import sqlite3

    target = Path(path)
    if not target.exists():
        return {"ok": False, "detail": f"No such snapshot: {path}"}
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return {"ok": False, "detail": f"integrity_check said {integrity}"}
            rows = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            kinds = conn.execute(
                "SELECT COUNT(DISTINCT kind) FROM records"
            ).fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - an unreadable file is the answer
        return {"ok": False, "detail": f"Could not read it: {exc}"}
    return {
        "ok": True, "records": rows, "kinds": kinds,
        "size_bytes": target.stat().st_size,
        "detail": f"{rows} record(s) across {kinds} kind(s)",
    }


def restore(name: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Put a snapshot back, after taking one of what is being replaced.

    There was no way to do this. Snapshots were written daily, verified,
    pruned and counted, and nothing could put one back -- so the answer
    to a corrupted database was a person with a shell, at whatever hour
    it happened, which on a deployment meant to run itself is the same
    as no answer.

    Deliberately not automatic. Nothing in this application decides on
    its own that the live data is wrong and yesterday's is right; that
    judgement loses every write since the snapshot and belongs to a
    person. What this removes is the part that needed a shell -- the
    database is reopened and the working set rebuilt in place, so the
    service is serving the restored data before the call returns.

    The database being replaced is snapshotted first, labelled
    `pre-restore`, because the most likely mistake here is restoring the
    wrong one and the second most likely is being right about the
    corruption and wrong about which copy was good.
    """
    now = now or utc_now()
    directory = _directory()
    if directory is None:
        return {"restored": False, "status": "nowhere_to_read",
                "detail": "There is no snapshot directory on this deployment."}

    # _directory() already refuses an in-memory database when no backup
    # directory is configured -- but configure one and it stops refusing,
    # and there is still nothing on disk to replace. Copying over the
    # literal path ":memory:" would create a file of that name and leave
    # the reopened database empty.
    if STORE._db.path == ":memory:":
        return {
            "restored": False, "status": "nothing_to_replace",
            "detail": (
                "This deployment's database is in memory, so there is no "
                "file to restore over. Set CYBERLOGIX_DB_PATH."
            ),
        }

    target = directory / Path(name).name  # no traversal out of the directory
    checked = verify(str(target))
    if not checked["ok"]:
        return {"restored": False, "status": "unusable",
                "detail": checked["detail"]}

    safety = take(now=now, label="pre-restore")
    if not safety.get("taken"):
        return {
            "restored": False, "status": "no_safety_copy",
            "detail": (
                "Refusing to overwrite the live database without first "
                f"snapshotting it: {safety.get('detail')}"
            ),
        }

    live = Path(STORE._db.path)
    try:
        import shutil

        # Swap the file, then reopen and rebuild the working set from what
        # is now on disk. Closing and stopping there was the first version,
        # and it left the process holding a closed database: every request
        # afterwards answered 500 until somebody restarted the container.
        # A recovery step that needs a human at the end is not one.
        STORE._db.close()
        shutil.copyfile(target, live)
        STORE._db.reopen()
        STORE.forget()
        STORE.load()
    except Exception as exc:  # noqa: BLE001 - reported, and the copy remains
        logger.exception("Restore from %s failed (%s).", target, exc)
        try:
            STORE._db.reopen()
            STORE.forget()
            STORE.load()
        except Exception:  # noqa: BLE001 - nothing left to try
            logger.exception("Could not reopen the database after a failed "
                             "restore. This process needs restarting.")
        return {
            "restored": False, "status": "failed",
            "detail": (
                f"Could not put {target.name} back: {exc}. The database "
                f"you had was snapshotted first, to {safety.get('path')}."
            ),
        }

    logger.warning(
        "Restored %s over the live database (%d records). The database it "
        "replaced is in %s.",
        target.name, checked["records"], safety.get("path"),
    )
    return {
        "restored": True,
        "status": "ok",
        "from": target.name,
        "records": checked["records"],
        "previous_database_saved_to": safety.get("path"),
        "detail": (
            "Restored and reloaded in place -- no restart needed. The "
            "database that was replaced was snapshotted first, so "
            "restoring the wrong one is survivable."
        ),
    }


def prune() -> List[Path]:
    """Delete the oldest until only BACKUP_KEEP remain."""
    snapshots = existing()
    doomed = snapshots[: max(0, len(snapshots) - BACKUP_KEEP)]
    for path in doomed:
        try:
            path.unlink()
        except OSError as exc:
            logger.error("Could not remove old backup %s (%s).", path, exc)
    return doomed


def run_daily_backup(now: Optional[datetime] = None) -> Dict[str, Any]:
    """One a day, from the money pass. Skips if today's already exists.

    Checked by filename rather than by a stored flag, so a snapshot
    somebody deletes by hand is taken again rather than assumed to be
    there — which is the failure this whole module exists to prevent.
    """
    now = now or utc_now()
    today = now.strftime("%Y%m%d")
    if any(p.name.startswith(f"{PREFIX}{today}") for p in existing()):
        return {"taken": False, "status": "already_today"}
    return take(now)


def status() -> Dict[str, Any]:
    """Whether this deployment could actually recover from losing its disk."""
    directory = _directory()
    snapshots = existing()
    live = STORE._db.path
    same_disk = (
        directory is not None
        and live != ":memory:"
        and directory.resolve().parent == Path(live).resolve().parent
    )
    return {
        "directory": str(directory) if directory else None,
        "keep": BACKUP_KEEP,
        "count": len(snapshots),
        "latest": snapshots[-1].name if snapshots else None,
        "latest_bytes": snapshots[-1].stat().st_size if snapshots else 0,
        "on_the_same_disk_as_the_database": same_disk,
        "note": (
            "Snapshots are taken with SQLite's online backup and verified "
            "before they count. "
            + (
                "They are beside the live database, which survives a bad "
                "migration and not a lost disk — set CYBERLOGIX_BACKUP_DIR "
                "to somewhere else."
                if same_disk else
                "Copy them off this machine on a schedule; a backup that "
                "only exists here is not one."
            )
        ),
    }


@router.get("/backups")
def read_backups(_: None = Depends(require_platform_admin)):
    """What snapshots exist, and whether they are worth anything."""
    return {
        "generated_at": iso(utc_now()),
        **status(),
        "snapshots": [
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "taken_at": iso(
                    datetime.fromtimestamp(path.stat().st_mtime).astimezone()
                ),
            }
            for path in reversed(existing())
        ],
    }


@router.post("/backups", status_code=201)
def make_backup(_: None = Depends(require_platform_admin)):
    """Take one now — before a migration, or before anything frightening."""
    result = take(label="manual")
    if not result["taken"]:
        raise HTTPException(status_code=503, detail=result["detail"])
    return result


class RestoreRequest(BaseModel):
    """Naming the snapshot is the confirmation. There is no "latest"."""

    model_config = ConfigDict(extra="forbid")

    snapshot: str = Field(..., min_length=1, max_length=255)
    confirm: bool = Field(
        ..., description="Must be true. Restoring discards every write "
                         "made since the snapshot was taken."
    )


@router.post("/backups/restore")
def do_restore(
    payload: RestoreRequest, _: None = Depends(require_platform_admin)
):
    """Put a named snapshot back over the live database."""
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail=(
                "Restoring discards every write made since that snapshot. "
                "Send confirm=true once that is what you mean."
            ),
        )
    result = restore(payload.snapshot)
    if not result["restored"]:
        raise HTTPException(status_code=400, detail=result["detail"])
    return result


@router.get("/backups/{name}/verify")
def do_verify(name: str, _: None = Depends(require_platform_admin)):
    """Open a snapshot and confirm it is a database with rows in it."""
    directory = _directory()
    if directory is None:
        raise HTTPException(status_code=404, detail="No snapshot directory.")
    return verify(str(directory / Path(name).name))
