"""Independent Drax roster cache. No connections to the Workshop V3 database.

Sources must be approved local copies/synced files. All roster data stays in
.local-data/ and is never stored in Git. Drax is read-only.
"""
from __future__ import annotations
import csv
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_CHECK_EVERY_SECONDS = 300
_last_check = {}
_TZ = ZoneInfo("America/Indiana/Indianapolis")


def _connect(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(database, timeout=8)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS roster_associates (
        number TEXT PRIMARY KEY, full_name TEXT NOT NULL,
        shift TEXT NOT NULL DEFAULT '', default_id TEXT NOT NULL DEFAULT '',
        job_title TEXT NOT NULL DEFAULT '', is_mst INTEGER NOT NULL DEFAULT 0,
        source_active INTEGER NOT NULL DEFAULT 1, manual INTEGER NOT NULL DEFAULT 0,
        override_mst INTEGER DEFAULT NULL, override_shift TEXT DEFAULT NULL,
        last_seen TEXT NOT NULL DEFAULT ''
    )""")
    columns = {row["name"] for row in con.execute("PRAGMA table_info(roster_associates)")}
    if "override_name" not in columns:
        con.execute("ALTER TABLE roster_associates ADD COLUMN override_name TEXT DEFAULT NULL")
    con.execute("""CREATE TABLE IF NOT EXISTS roster_meta (
        k TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    con.commit()
    return con


def _source(private: Path):
    configured = os.getenv("IND3_V31_ROSTER_PATH", "").strip()
    if configured:
        file = Path(configured).expanduser()
        return file if file.is_file() else None, configured
    choices = [private / "Drax  Associates.xlsx", private / "Drax Associates.xlsx",
               private / "mst-roster.xlsx", private / "mst-roster.csv"]
    return next((file for file in choices if file.is_file()), None), None


def _normalize_number(raw):
    if raw is None or isinstance(raw, bool):
        return ""
    if isinstance(raw, (float, int)):
        if raw != int(raw):
            return ""
        value = str(int(raw))
    else:
        value = str(raw).strip()
        if value.endswith(".0") and value[:-2].isdecimal():
            value = value[:-2]
    return value if value.isdecimal() and len(value) <= 12 else ""


def _parse(file: Path, reserved: set):
    if file.suffix.lower() == ".csv":
        with file.open(encoding="utf-8-sig", newline="") as reader:
            records = list(csv.DictReader(reader))
    elif file.suffix.lower() == ".xlsx":
        from openpyxl import load_workbook
        book = load_workbook(file, read_only=True, data_only=True)
        try:
            iterator = book.active.iter_rows(values_only=True)
            keys = next(iterator, None)
            if not keys:
                raise ValueError("Drax workbook has no header row")
            records = [dict(zip(keys, row)) for row in iterator]
        finally:
            book.close()
    else:
        raise ValueError("Roster source must be .xlsx or .csv")
    if records and not {"name", "assoc. #", "shift", "job title"}.issubset(
        {str(k).strip().casefold() for k in records[0]}
    ):
        raise ValueError("Drax columns Name, Assoc. #, Shift, Job Title are required")
    result = {}
    for source_row in records:
        row = {str(key or "").strip().casefold(): value for key,value in source_row.items()}
        num = _normalize_number(row.get("assoc. #", row.get("assoc #")))
        if not num or num in reserved:
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        result[num] = (
            num, name[:180], str(row.get("shift") or "").strip().upper()[:10],
            str(row.get("default id") or "").strip()[:120],
            str(row.get("job title") or "").strip()[:100],
            int(str(row.get("job title") or "").strip().upper() == "MST")
        )
    return result


def _meta(conn, key, value):
    conn.execute("INSERT INTO roster_meta (k,value) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET value=excluded.value",
                 (key, str(value)))


def sync(private: Path, database: Path, reserved: set, force=False):
    """Check at most every five minutes unless forced. Mtime avoids extra parses.

    Never wipe existing cached records when a source is temporarily offline
    or invalid. Admin overrides remain intact after Drax reload.
    """
    global _last_check
    current = time.monotonic()
    key = str(database.resolve())
    previous = _last_check.get(key)
    # Always perform the first roster read, even if host uptime is <5 minutes.
    if not force and previous is not None and current - previous < _CHECK_EVERY_SECONDS:
        return
    _last_check[key] = current
    source, configured = _source(private)
    with _connect(database) as conn:
        _meta(conn, "last_checked", datetime.now(_TZ).isoformat())
        if source is None:
            _meta(conn, "error", "Configured roster file unavailable" if configured else "No local Drax roster configured")
            return
        try:
            stat = source.stat()
            identity = str(source.resolve()) + "|" + str(stat.st_mtime_ns) + "|" + str(stat.st_size)
            old = conn.execute("SELECT value FROM roster_meta WHERE k='signature'").fetchone()
            if not force and old and old["value"] == identity:
                _meta(conn, "error", "")
                return
            entries = _parse(source, reserved)
            # Require a valid readable header even when workbook contains no MST.
            seen_at = datetime.now(_TZ).isoformat()
            for item in entries.values():
                conn.execute("""INSERT INTO roster_associates
                    (number,full_name,shift,default_id,job_title,is_mst,source_active,manual,last_seen)
                    VALUES (?,?,?,?,?,?,1,0,?)
                    ON CONFLICT(number) DO UPDATE SET
                    full_name=CASE WHEN roster_associates.manual=1 THEN roster_associates.full_name ELSE excluded.full_name END,
                    shift=CASE WHEN roster_associates.manual=1 THEN roster_associates.shift ELSE excluded.shift END,
                    default_id=excluded.default_id,
                    job_title=CASE WHEN roster_associates.manual=1 THEN roster_associates.job_title ELSE excluded.job_title END,
                    is_mst=CASE WHEN roster_associates.manual=1 THEN roster_associates.is_mst ELSE excluded.is_mst END,
                    source_active=1,last_seen=excluded.last_seen
                """, (*item, seen_at))
            # A removed Drax row is disabled, not allowed to keep logging in.
            conn.execute("UPDATE roster_associates SET source_active=0 WHERE manual=0 AND last_seen<>?",
                         (seen_at,))
            _meta(conn, "signature", identity)
            _meta(conn, "source_name", source.name)
            _meta(conn, "last_synced", seen_at)
            _meta(conn, "error", "")
        except Exception as exc:
            _meta(conn, "error", "Roster synchronization failed: " + str(exc)[:220])


def profiles(private, database, management, rules):
    sync(private, database, set(management))
    data = {number: dict(profile) for number, profile in management.items()}
    with _connect(database) as conn:
        rows = conn.execute("SELECT * FROM roster_associates WHERE source_active=1").fetchall()
    for row in rows:
        is_mst = row["override_mst"] if row["override_mst"] is not None else row["is_mst"]
        assigned = row["override_shift"] if row["override_shift"] is not None else row["shift"]
        if is_mst and assigned in rules and row["number"] not in management:
            data[row["number"]] = {"name": row["override_name"] or row["full_name"], "all_shifts":False,
                                   "shift":assigned, "default_id":row["default_id"], "is_mst":True}
    return data


def status(database, include_rows=False):
    with _connect(database) as conn:
        rows = conn.execute("SELECT * FROM roster_associates ORDER BY full_name COLLATE NOCASE").fetchall()
        meta = {r["k"]:r["value"] for r in conn.execute("SELECT k,value FROM roster_meta")}
    result = {"source":meta.get("source_name", ""), "last_checked":meta.get("last_checked", ""),
              "last_synced":meta.get("last_synced", ""), "error":meta.get("error", ""),
              "entries":sum(bool(r["source_active"]) for r in rows)}
    if include_rows:
        result["associates"] = [{
            "number":r["number"], "name":r["override_name"] or r["full_name"],
            "shift":r["override_shift"] if r["override_shift"] is not None else r["shift"],
            "drax_shift":r["shift"], "is_mst":bool(r["override_mst"] if r["override_mst"] is not None else r["is_mst"]),
            "drax_is_mst":bool(r["is_mst"]), "overridden":r["override_mst"] is not None or r["override_shift"] is not None or r["override_name"] is not None,
            "active":bool(r["source_active"]), "manual":bool(r["manual"])
        } for r in rows]
    return result


def set_override(database, number, *, mst=None, shift=None, reset=False):
    with _connect(database) as conn:
        row = conn.execute("SELECT * FROM roster_associates WHERE number=?",(number,)).fetchone()
        if not row: raise KeyError("Associate not found in Drax cache")
        if reset:
            conn.execute("UPDATE roster_associates SET override_mst=NULL,override_shift=NULL,override_name=NULL WHERE number=?",(number,))
        else:
            if mst is not None:
                conn.execute("UPDATE roster_associates SET override_mst=? WHERE number=?",(int(mst),number))
            if shift is not None:
                conn.execute("UPDATE roster_associates SET override_shift=? WHERE number=?",(shift,number))


def add_manual(database, number, name, shift, mst):
    with _connect(database) as conn:
        if conn.execute("SELECT 1 FROM roster_associates WHERE number=?",(number,)).fetchone():
            raise ValueError("Associate already exists")
        conn.execute("""INSERT INTO roster_associates
            (number,full_name,shift,job_title,is_mst,source_active,manual,last_seen)
            VALUES (?,?,?,?,?,1,1,?)""",(
              number, name, shift, "MST" if mst else "Associate",int(mst),
              datetime.now(_TZ).isoformat()))


def edit_associate(database, number, full_name, new_number=None):
    """Admin display-name overrides survive Drax resync.

    Source associate numbers are immutable. Manual numbers can be changed only
    when unused in shift history and overtime authorization tables.
    """
    final_number = new_number or number
    with _connect(database) as conn:
        row = conn.execute("SELECT * FROM roster_associates WHERE number=?", (number,)).fetchone()
        if row is None:
            raise KeyError("Associate does not exist")
        if final_number != number:
            if not row["manual"]:
                raise ValueError("Update associate numbers in the Drax workbook, then reload")
            if conn.execute("SELECT 1 FROM roster_associates WHERE number=?", (final_number,)).fetchone():
                raise ValueError("The new associate number is already used")
            for table, column in (("shifts","owner"),("overtime_grants","associate")):
                if conn.execute(f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1",(number,)).fetchone():
                    raise ValueError("Cannot change an associate number referenced by shift history or overtime")
            conn.execute("UPDATE roster_associates SET number=? WHERE number=?", (final_number,number))
        if row["manual"]:
            conn.execute("UPDATE roster_associates SET full_name=? WHERE number=?",(full_name,final_number))
        else:
            conn.execute("UPDATE roster_associates SET override_name=? WHERE number=?",(full_name,final_number))
