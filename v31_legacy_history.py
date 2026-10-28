"""Exact-schema read-only V2/V3 history archive for V3.1.

The old facility database is NEVER opened for writing.
Copies all its tables and row data using SQLite's online backup API, keeping
historical V2/V3 schema, IDs, shifts, reports, entries and ancillary tables.
V3.1's new live shift database remains isolated and unchanged.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

REQUIRED = {"shifts", "entries", "reports", "associates"}
TZ = ZoneInfo("America/Indiana/Indianapolis")


class LegacyHistoryError(Exception):
    pass


def _ro(path: Path):
    return sqlite3.connect(f"file:{quote(str(path.resolve()), safe='/:')}?mode=ro", uri=True, timeout=8)


def _tables(conn):
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _counts(conn, names):
    return {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}


def _validate(conn):
    tables = _tables(conn)
    if not REQUIRED.issubset(tables):
        raise LegacyHistoryError("Not a V2/V3 shift database; expected: " +
                                 ", ".join(sorted(REQUIRED)))
    cols = {r[1] for r in conn.execute('PRAGMA table_info("shifts")')}
    if not {"id", "date", "shift_type", "lead_name", "created_at", "is_active"}.issubset(cols):
        raise LegacyHistoryError("Unrecognized legacy shifts schema")
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise LegacyHistoryError("Legacy database did not pass SQLite integrity check")
    return tables


def inspect(source: Path):
    source = Path(source).expanduser()
    if not source.is_file():
        raise LegacyHistoryError("Legacy database file not found")
    with _ro(source) as conn:
        tables = _validate(conn)
        counts = _counts(conn, tables)
    return {"tables":len(tables), "rows":counts, "total_shift_records":counts["shifts"],
            "total_report_records":counts["reports"]}


def import_snapshot(source: Path, private: Path):
    """Snapshot source read-only into a *new* private archive DB, verify before swap."""
    source = Path(source).expanduser()
    private = Path(private).expanduser()
    if not source.is_file():
        raise LegacyHistoryError("Legacy database file not found")
    private.mkdir(parents=True, exist_ok=True)
    archive = private / "legacy-v2-v3-archive.sqlite3"
    with _ro(source) as origin:
        source_tables = _validate(origin)
        source_counts = _counts(origin, source_tables)
        fd, temp_name = tempfile.mkstemp(prefix=".legacy-backup-", suffix=".sqlite3",
                                         dir=str(private))
        os.close(fd)
        staging = Path(temp_name)
        try:
            with sqlite3.connect(staging) as copy:
                origin.backup(copy)
            with _ro(staging) as copied:
                copied_tables = _validate(copied)
                copied_counts = _counts(copied, copied_tables)
                if copied_tables != source_tables or copied_counts != source_counts:
                    raise LegacyHistoryError("History backup verification failed")
            # Replace only the local V3.1 historical snapshot.
            staging.replace(archive)
            archive.chmod(0o600)
        finally:
            staging.unlink(missing_ok=True)
    metadata = {"source": str(source), "archived_at":datetime.now(TZ).isoformat(),
                "table_count":len(source_tables), "counts":source_counts,
                "format":"Exact V2/V3 SQLite backup; independent from V3.1 sessions"}
    meta = private / "legacy-history-metadata.json"
    temporary = private / ".legacy-history-metadata.tmp"
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary.replace(meta)
    meta.chmod(0o600)
    return {"archive":str(archive), "table_count":len(source_tables),
            "shifts":source_counts["shifts"], "entries":source_counts["entries"],
            "reports":source_counts["reports"]}


def history(private: Path, associate: str | None, limit=50, offset=0):
    """Read past shifts without changing original IDs or mixing current live sessions."""
    file = Path(private) / "legacy-v2-v3-archive.sqlite3"
    if not file.is_file():
        return {"connected":False,"rows":[],"total":0,"format":"v2_v3_exact"}
    limit = max(1,min(int(limit),100))
    offset = max(0,int(offset))
    with _ro(file) as conn:
        conn.row_factory = sqlite3.Row
        tables = _validate(conn)
        cols = {r[1] for r in conn.execute('PRAGMA table_info("shifts")')}
        user_column = "opened_by_assoc" if "opened_by_assoc" in cols else None
        if associate is not None and user_column is None:
            return {"connected":True,"rows":[],"total":0,"format":"v2_v3_exact",
                    "note":"Legacy records do not contain individual opener IDs"}
        where, args = ("",[]) if associate is None else (
            "WHERE opened_by_assoc=?", [associate])
        total = conn.execute(f"SELECT COUNT(*) FROM shifts {where}",args).fetchone()[0]
        selected = conn.execute(
            f"SELECT * FROM shifts {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*args,limit,offset)).fetchall()
        results=[]
        for row in selected:
            item=dict(row)
            sid=item["id"]
            item["entry_count"]=conn.execute(
                "SELECT COUNT(*) FROM entries WHERE shift_id=?",(sid,)).fetchone()[0]
            item["report_count"]=conn.execute(
                "SELECT COUNT(*) FROM reports WHERE shift_id=?",(sid,)).fetchone()[0]
            results.append(item)
    return {"connected":True,"rows":results,"total":total,"format":"v2_v3_exact",
            "limit":limit,"offset":offset}
