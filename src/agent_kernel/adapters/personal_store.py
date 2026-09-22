"""Personal ownership, saved items and application state on PlatformStore.

Reads use PlatformStore._rows (its effective connection/fork-family lock).
Writes use its short BEGIN IMMEDIATE transaction, including target checks.
"""
from __future__ import annotations

import time
from typing import Any

from .platform_store import PlatformStore


TABLES = {"job": "jobs", "project": "projects", "news": "news"}
ALIASES = {"job": "job", "jobs": "job", "project": "project", "projects": "project",
           "news": "news"}
ACTION_NAMES = ("favorite", "watch", "read_later", "read", "applied")
STAGES = ("saved", "preparing", "applied", "interviewing", "offer", "rejected", "archived")
APPLICATION_FIELDS = ("stage", "next_step", "next_at", "note", "next_step_done")


def normalize_kind(kind: str) -> str | None:
    return ALIASES.get(kind.strip().lower())


def ensure_personal_schema(store: PlatformStore) -> None:
    """Run before creating routers; repeated migrations preserve existing data."""
    with store._write_transaction():
        columns = {row["name"] for row in store.conn.execute("PRAGMA table_info(jobs)")}
        if "created_by" not in columns:
            store.conn.execute("ALTER TABLE jobs ADD COLUMN created_by INTEGER NULL")
        store.conn.execute(
            "CREATE TABLE IF NOT EXISTS applications("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, "
            "job_id INTEGER NOT NULL, stage TEXT NOT NULL, next_step TEXT, next_at TEXT, note TEXT, "
            "next_step_done INTEGER NOT NULL DEFAULT 0, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(user_id, job_id))"
        )
        store.conn.execute(
            "CREATE TABLE IF NOT EXISTS application_history("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER NOT NULL, "
            "user_id INTEGER NOT NULL, job_id INTEGER NOT NULL, stage TEXT NOT NULL, "
            "next_step TEXT, next_at TEXT, note TEXT, "
            "next_step_done INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)"
        )
        store.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_user_updated "
            "ON applications(user_id, updated_at DESC, id DESC)"
        )
        store.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_application_history_application "
            "ON application_history(application_id, id ASC)"
        )


def get_entity(store: PlatformStore, kind: str, target_id: int,
               uid: int | None) -> dict[str, Any] | None:
    """Return a visible raw entity; only explicitly manual jobs are private."""
    kind = normalize_kind(kind)
    if kind is None:
        return None
    rows = store._rows(f"SELECT * FROM {TABLES[kind]} WHERE id=?", (target_id,))
    if not rows:
        return None
    entity = rows[0]
    if kind == "job" and entity.get("origin") == "manual":
        if uid is None or entity.get("created_by") != uid:
            return None
    return entity


def target_visible(store: PlatformStore, kind: str, target_id: int, uid: int | None) -> bool:
    return get_entity(store, kind, target_id, uid) is not None


def visible_jobs(store: PlatformStore, uid: int | None,
                 **filters: str | int | None) -> list[dict[str, Any]]:
    """Retain list_jobs fields, filters and ordering without exposing private jobs."""
    return [row for row in store.list_jobs(**filters)
            if row.get("origin") != "manual"
            or (uid is not None and row.get("created_by") == uid)]


def get_action_state(store: PlatformStore, kind: str, target_id: int,
                     uid: int | None) -> dict[str, bool]:
    state = {name: False for name in ACTION_NAMES}
    if uid is not None:
        rows = store._rows(
            "SELECT action FROM user_actions WHERE user_id=? AND target_type=? AND target_id=?",
            (uid, normalize_kind(kind), target_id),
        )
        for row in rows:
            if row["action"] in state:
                state[row["action"]] = True
    return state


def set_action_states(store: PlatformStore, kind: str, target_id: int, uid: int,
                      states: dict[str, bool]) -> dict[str, bool]:
    """Apply flags atomically; unavailable targets only allow clearing one's existing actions."""
    kind = normalize_kind(kind)
    if kind is None or any(name not in ACTION_NAMES or type(value) is not bool
                           for name, value in states.items()):
        raise ValueError("unsupported action target or state")
    with store._write_transaction():
        if not target_visible(store, kind, target_id, uid):
            owned = get_action_state(store, kind, target_id, uid)
            if not states or any(states.values()) or not any(owned.values()):
                raise LookupError("target not found")
        now = time.time()
        for name, enabled in states.items():
            if enabled:
                store.conn.execute(
                    "INSERT OR IGNORE INTO user_actions"
                    "(user_id, target_type, target_id, action, created_at) VALUES(?,?,?,?,?)",
                    (uid, kind, target_id, name, now),
                )
            else:
                store.conn.execute(
                    "DELETE FROM user_actions WHERE user_id=? AND target_type=? "
                    "AND target_id=? AND action=?", (uid, kind, target_id, name),
                )
        return get_action_state(store, kind, target_id, uid)


def set_action_state(store: PlatformStore, kind: str, target_id: int, uid: int,
                     action: str, enabled: bool) -> None:
    set_action_states(store, kind, target_id, uid, {action: enabled})


def list_saved_targets(store: PlatformStore, uid: int, *, kind: str | None = None,
                       action: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    """Paginate own kind:id groups, including unavailable targets, without reading their content."""
    conditions = ["a.user_id=?", "a.target_type IN ('job','project','news')"]
    params: list[Any] = [uid]
    if kind is not None:
        conditions.append("a.target_type=?")
        params.append(kind)
    if action is not None:
        conditions.append("a.action=?")
        params.append(action)
    return store._rows(
        "SELECT a.target_type AS kind, a.target_id AS id, MAX(a.created_at) AS saved_at "
        "FROM user_actions a "
        "WHERE " + " AND ".join(conditions) + " "
        "GROUP BY a.target_type, a.target_id "
        "ORDER BY COALESCE(MAX(a.created_at),0) DESC, MAX(a.id) DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )


def get_application(store: PlatformStore, uid: int, job_id: int) -> dict[str, Any] | None:
    rows = store._rows("SELECT * FROM applications WHERE user_id=? AND job_id=?", (uid, job_id))
    return rows[0] if rows else None


def application_history(store: PlatformStore, application_id: int | None) -> list[dict[str, Any]]:
    return store._rows(
        "SELECT * FROM application_history WHERE application_id=? ORDER BY id ASC", (application_id,),
    ) if application_id is not None else []


def list_application_records(store: PlatformStore, uid: int) -> list[dict[str, Any]]:
    """A stored application wins; legacy applied actions require no migration/write."""
    return store._rows(
        "SELECT * FROM (SELECT id,user_id,job_id,stage,next_step,next_at,note,next_step_done,"
        "created_at,updated_at FROM applications WHERE user_id=? UNION ALL "
        "SELECT NULL,a.user_id,a.target_id,'applied',NULL,NULL,NULL,0,a.created_at,a.created_at "
        "FROM user_actions a WHERE a.user_id=? AND a.target_type='job' AND a.action='applied' "
        "AND NOT EXISTS(SELECT 1 FROM applications p WHERE p.user_id=a.user_id AND p.job_id=a.target_id)) "
        "ORDER BY COALESCE(updated_at,0) DESC, job_id DESC", (uid, uid),
    )


def save_application(store: PlatformStore, uid: int, job_id: int, *, stage: str,
                     **changes: Any) -> dict[str, Any]:
    """Merge only supplied fields inside the transaction; retries add no history."""
    if stage not in STAGES or any(key not in APPLICATION_FIELDS[1:] for key in changes):
        raise ValueError("unsupported application state")
    with store._write_transaction():
        if not target_visible(store, "job", job_id, uid):
            raise LookupError("target not found")
        current = get_application(store, uid, job_id)
        state = {"next_step": None, "next_at": None, "note": None, "next_step_done": False}
        if current:
            state.update({key: current[key] for key in state})
        state.update(changes, stage=stage)
        state["next_step_done"] = int(bool(state["next_step_done"]))
        values = tuple(state[key] for key in APPLICATION_FIELDS)
        now = time.time()
        changed = current is None or any(current[key] != state[key] for key in APPLICATION_FIELDS)
        if current is None:
            cursor = store.conn.execute(
                "INSERT INTO applications(user_id,job_id,stage,next_step,next_at,note,"
                "next_step_done,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (uid, job_id, *values, now, now),
            )
            application_id = int(cursor.lastrowid)
        else:
            application_id = current["id"]
            if changed:
                store.conn.execute(
                    "UPDATE applications SET stage=?,next_step=?,next_at=?,note=?,"
                    "next_step_done=?,updated_at=? WHERE id=?", (*values, now, application_id),
                )
        if changed:
            store.conn.execute(
                "INSERT INTO application_history(application_id,user_id,job_id,stage,next_step,"
                "next_at,note,next_step_done,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (application_id, uid, job_id, *values, now),
            )
        # Applied records a past personal action, not the current stage or an external submission.
        if stage == "applied":
            store.conn.execute(
                "INSERT OR IGNORE INTO user_actions"
                "(user_id,target_type,target_id,action,created_at) VALUES(?,'job',?,'applied',?)",
                (uid, job_id, now),
            )
        return get_application(store, uid, job_id)
