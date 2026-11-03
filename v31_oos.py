"""Manually-reported Breakdown/OOS tracking for IND3 V3.1.

Independent of Cetaris-imported work orders: an MST can log an asset as
Down/Degraded with no backing WO at all (equipment noticed broken before
a WO exists, or the eventual fix never went through Cetaris). The record
stays open *across shifts* -- not scoped to the shift that logged it --
until any signed-in associate marks it RTS, since the actual fix might
come from a completely different shift or source than the one that
originally logged it down.

Kept separate from the Cetaris-derived OOS/RTS rows in report_wo on
purpose: these two things have different lifecycles and different
reliability guarantees, and merging them into one table would make it
harder to reason about which rows came from where.
"""
from __future__ import annotations
import sqlite3


def _table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS manual_oos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset TEXT NOT NULL,
        cause TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        logged_by TEXT NOT NULL,
        logged_by_name TEXT NOT NULL DEFAULT '',
        logged_shift_id INTEGER NOT NULL,
        logged_shift_code TEXT NOT NULL,
        logged_at TEXT NOT NULL,
        open INTEGER NOT NULL DEFAULT 1,
        rts_by TEXT NOT NULL DEFAULT '',
        rts_by_name TEXT NOT NULL DEFAULT '',
        rts_shift_id INTEGER,
        rts_shift_code TEXT NOT NULL DEFAULT '',
        rts_at TEXT NOT NULL DEFAULT '',
        rts_notes TEXT NOT NULL DEFAULT ''
    )""")


def get(conn: sqlite3.Connection, oos_id: int) -> dict | None:
    _table(conn)
    row = conn.execute("SELECT * FROM manual_oos WHERE id=?", (oos_id,)).fetchone()
    return dict(row) if row else None


def create(conn: sqlite3.Connection, *, asset: str, cause: str, notes: str,
           logged_by: str, logged_by_name: str, shift_id: int, shift_code: str,
           now_iso: str) -> dict:
    _table(conn)
    cur = conn.execute("""INSERT INTO manual_oos
        (asset,cause,notes,logged_by,logged_by_name,logged_shift_id,logged_shift_code,logged_at)
        VALUES(?,?,?,?,?,?,?,?)""",
        (asset, cause, notes, logged_by, logged_by_name, shift_id, shift_code, now_iso))
    return get(conn, cur.lastrowid)


def list_records(conn: sqlite3.Connection, open_only: bool = True) -> list[dict]:
    _table(conn)
    query = "SELECT * FROM manual_oos"
    if open_only:
        query += " WHERE open=1"
    query += " ORDER BY logged_at DESC"
    return [dict(r) for r in conn.execute(query).fetchall()]


def resolved_this_shift(conn: sqlite3.Connection, shift_id: int) -> list[dict]:
    """Manual OOS entries RTS'd during this specific shift -- for that
    shift's own passdown RTS section, not an all-time history dump."""
    _table(conn)
    rows = conn.execute(
        "SELECT * FROM manual_oos WHERE rts_shift_id=? ORDER BY rts_at", (shift_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_rts(conn: sqlite3.Connection, oos_id: int, *, rts_by: str, rts_by_name: str,
             shift_id: int, shift_code: str, now_iso: str, notes: str = ""):
    """Returns (record, error). error is None on success, else one of
    'not_found' / 'already_resolved'."""
    _table(conn)
    row = conn.execute("SELECT * FROM manual_oos WHERE id=?", (oos_id,)).fetchone()
    if row is None:
        return None, "not_found"
    if not row["open"]:
        return None, "already_resolved"
    conn.execute("""UPDATE manual_oos SET open=0, rts_by=?, rts_by_name=?, rts_shift_id=?,
        rts_shift_code=?, rts_at=?, rts_notes=? WHERE id=?""",
        (rts_by, rts_by_name, shift_id, shift_code, now_iso, notes, oos_id))
    return get(conn, oos_id), None
