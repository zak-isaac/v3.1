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
    columns={r["name"] for r in conn.execute("PRAGMA table_info(manual_oos)")}
    if "direct_rts" not in columns:
        conn.execute("ALTER TABLE manual_oos ADD COLUMN direct_rts INTEGER NOT NULL DEFAULT 0")


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


def record_direct_rts(conn: sqlite3.Connection, *, asset: str,
                      actor: str, actor_name: str, shift_id: int,
                      shift_code: str, now_iso: str) -> dict:
    """Explicit RTS without an earlier manually logged OOS incident.

    Keep an auditable distinct event; do not fabricate a repaired WO or add
    this direct-only confirmation to the breakdown-only Outlook RTS section.
    """
    _table(conn)
    cur=conn.execute("""INSERT INTO manual_oos(
        asset,cause,notes,logged_by,logged_by_name,logged_shift_id,
        logged_shift_code,logged_at,open,rts_by,rts_by_name,rts_shift_id,
        rts_shift_code,rts_at,direct_rts)
        VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?,1)""",
        (asset,"Direct RTS confirmation (no prior OOS entry)","",
         actor,actor_name,shift_id,shift_code,now_iso,
         actor,actor_name,shift_id,shift_code,now_iso))
    return get(conn,cur.lastrowid)


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


def import_legacy_oos(conn: sqlite3.Connection, legacy_archive) -> dict:
    """One-time bridge: carry forward units that were still OOS (never RTS'd)
    in the old V2/V3 app at V3.1 cutover, so they don't silently vanish from
    the live OOS board just because the legacy import is otherwise read-only
    history (see v31_legacy_history.import_snapshot).

    The legacy `oos_units` table only ever recorded unit + added_at -- no
    technician or shift attribution -- so these come in clearly marked as a
    legacy import rather than invented into a real associate's name.

    Idempotent by asset: skips anything that already has an OPEN manual_oos
    row, from any source, so re-running this (or a tech independently
    re-logging the same asset first) never creates a duplicate open entry.
    """
    _table(conn)
    with sqlite3.connect(f"file:{legacy_archive}?mode=ro", uri=True) as src:
        src.row_factory = sqlite3.Row
        legacy_rows = src.execute("SELECT unit, added_at FROM oos_units").fetchall()
    already_open = {r["asset"] for r in conn.execute("SELECT asset FROM manual_oos WHERE open=1")}
    imported, skipped = [], []
    for row in legacy_rows:
        if row["unit"] in already_open:
            skipped.append(row["unit"])
            continue
        conn.execute("""INSERT INTO manual_oos
            (asset,cause,notes,logged_by,logged_by_name,logged_shift_id,
             logged_shift_code,logged_at,open)
            VALUES(?,?,?,?,?,?,?,?,1)""",
            (row["unit"], "Carried over from legacy V2/V3 OOS list at V3.1 cutover", "",
             "legacy-import", "Legacy V2/V3 import", 0, "LEGACY", row["added_at"]))
        imported.append(row["unit"])
    conn.commit()
    return {"imported": imported, "skipped_already_open": skipped}


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
