"""Conflict-aware bridge for ORIGINAL V3 roster DB edits -> shared Drax workbook.

Never writes to V3's database. V3's OneDrive workbook is ONLY edited when
IND3_V31_DRAX_WRITE_ENABLED=1 and source paths are explicitly configured.
First scan is a non-writing baseline. All workbook edits are narrowly scoped
to changed cells, with a local before-image backup and conflict checks.
Drax -> V3.1 is handled by v31_roster.py; original V3 already polls Drax.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

TZ = ZoneInfo("America/Indiana/Indianapolis")
FIELDS=("name","shift","is_mst")


class BridgeError(RuntimeError):
    pass


def _read_only(path):
    if not Path(path).is_file():
        raise BridgeError("Original V3 database is unavailable")
    return sqlite3.connect(f"file:{quote(str(Path(path).resolve()),safe='/:')}?mode=ro",uri=True,timeout=7)


def _bridge_db(private):
    private=Path(private)
    private.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(private/"drax-bridge.sqlite3")
    conn.execute("""CREATE TABLE IF NOT EXISTS baseline(
        assoc TEXT PRIMARY KEY, v3_values TEXT NOT NULL, drax_values TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS activity(
        id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, assoc TEXT NOT NULL,
        field TEXT NOT NULL, result TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
    )""")
    conn.commit()
    return conn


def _normalize_id(value):
    if value is None:return ""
    if isinstance(value,float) and value.is_integer():value=int(value)
    string=str(value).strip()
    return string if string.isdecimal() else ""


def _v3_values(path):
    with _read_only(path) as c:
        c.row_factory=sqlite3.Row
        columns={r["name"] for r in c.execute('PRAGMA table_info("associates")')}
        if not {"assoc_num","full_name","shift","is_mst"}.issubset(columns):
            raise BridgeError("Original V3 associates table is missing expected columns")
        result={}
        for row in c.execute('SELECT assoc_num,full_name,shift,is_mst FROM associates WHERE is_manager=0'):
            number=_normalize_id(row["assoc_num"])
            if number:
                result[number]={
                    "name":str(row["full_name"] or "").strip(),
                    "shift":str(row["shift"] or "").strip().upper(),
                    "is_mst":bool(row["is_mst"]),
                }
        return result


def _workbook_values(path):
    book=load_workbook(path,read_only=True,data_only=True)
    try:
        it=book.active.iter_rows(values_only=True)
        keys=next(it,None)
        if not keys:raise BridgeError("Drax workbook has no header row")
        hdr={str(v or "").strip():i for i,v in enumerate(keys)}
        required={"Name","Assoc. #","Shift","Job Title"}
        if not required.issubset(hdr):
            raise BridgeError("Drax workbook missing required columns")
        rows={}
        for number,row in enumerate(it,start=2):
            identity=_normalize_id(row[hdr["Assoc. #"]] if hdr["Assoc. #"] < len(row) else None)
            if not identity:continue
            if identity in rows:raise BridgeError("Duplicate Drax associate number; cannot safely sync")
            value=lambda column:row[hdr[column]] if hdr[column]<len(row) else None
            rows[identity]={"index":number, "name":str(value("Name") or "").strip(),
                           "shift":str(value("Shift") or "").strip().upper(),
                           "is_mst":str(value("Job Title") or "").strip().upper()=="MST"}
        return hdr,rows
    finally:
        book.close()


def _digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def enabled():
    return os.getenv("IND3_V31_DRAX_WRITE_ENABLED")=="1"


def write_admin_override(drax_path, private, assoc, *, name=None, shift=None, is_mst=None):
    """Push a single Administration-page override straight into the live
    Drax workbook, immediately, instead of staying a local-only V3.1 patch.

    Approved 2026-11-09 for this facility's deployment (IT-cleared) -- see
    README's Drax write-back section. Reuses the exact same guarded
    single-cell primitive as the old-V3 bridge above: only Name/Shift/Job
    Title cells are touched, a timestamped before-image backup is kept, and
    the write aborts cleanly (no partial edit) if the workbook changed on
    disk since we last read it. A manually-added local-only associate (one
    with no real Drax row at all) is a silent no-op here -- there is
    nothing to write back to.
    """
    if not enabled():
        return {"written": False, "reason": "write_disabled"}
    drax_path = Path(drax_path)
    if not drax_path.is_file():
        return {"written": False, "reason": "workbook_unavailable"}
    changes = {}
    if name is not None: changes["name"] = name
    if shift is not None: changes["shift"] = shift
    if is_mst is not None: changes["is_mst"] = is_mst
    if not changes:
        return {"written": False, "reason": "no_changes"}
    try:
        expected = _digest(drax_path)
        _, drax = _workbook_values(drax_path)
    except Exception as exc:
        # Covers a non-.xlsx fallback source (e.g. a CSV roster, which can
        # legitimately be the active source when OneDrive is unreachable) --
        # there's no real workbook to write into, so this degrades to a
        # local-only save instead of crashing the whole admin action. Broad
        # on purpose: this is a best-effort secondary step layered on top of
        # an already-successful local save, so any failure here must always
        # degrade gracefully, never bubble up and fail the admin's request.
        return {"written": False, "reason": "workbook_unreadable: " + str(exc)[:150]}
    if assoc not in drax:
        return {"written": False, "reason": "not_in_drax"}
    try:
        backup = _apply_cells(drax_path, {assoc: changes}, expected, private)
    except Exception as exc:
        return {"written": False, "reason": str(exc)[:150]}
    return {"written": True, "backup": str(backup)}


def _save_with_backup(book, workbook_path, expected_digest, private, label):
    """Shared safe-write tail: backup, write to a temp file, verify nothing
    changed on disk since we last read it, then atomically replace. Used by
    every write path below so there is exactly one place that knows how to
    safely commit a workbook change.
    """
    private=Path(private)
    private.mkdir(parents=True,exist_ok=True)
    # Keep a local backup of the original approved workbook, NOT in Git.
    backup=private/(f"drax-before-{label}-"+datetime.now(TZ).strftime("%Y%m%d-%H%M%S-%f")+".xlsx")
    shutil.copy2(workbook_path,backup)
    backup.chmod(0o600)
    fd,tmp=tempfile.mkstemp(prefix=".v31-drax-",suffix=".xlsx",dir=str(Path(workbook_path).parent))
    os.close(fd)
    temp=Path(tmp)
    try:
        book.save(temp)
        if _digest(workbook_path)!=expected_digest:
            raise BridgeError(f"Drax changed while preparing {label}; no write performed")
        os.replace(temp,workbook_path)
    finally:
        temp.unlink(missing_ok=True)
    return backup


def _apply_cells(workbook_path, changed, expected_digest, private):
    """Write only Name, Shift, and Job Title cells; never recreate entire rows."""
    book=load_workbook(workbook_path)
    try:
        ws=book.active
        headers={str(cell.value or "").strip():cell.column for cell in ws[1]}
        if not {"Name","Assoc. #","Shift","Job Title"}.issubset(headers):
            raise BridgeError("Drax columns changed during sync")
        for assoc,changes in changed.items():
            positions=[i for i in range(2,ws.max_row+1)
                       if _normalize_id(ws.cell(i,headers["Assoc. #"]).value)==assoc]
            if len(positions)!=1:
                raise BridgeError("Drax row missing or duplicated during write")
            row=positions[0]
            for field,value in changes.items():
                if field=="name":ws.cell(row,headers["Name"]).value=value
                elif field=="shift":ws.cell(row,headers["Shift"]).value=value
                elif field=="is_mst":
                    current=str(ws.cell(row,headers["Job Title"]).value or "").strip()
                    # Promote/demote MST without inventing an unrelated job title.
                    ws.cell(row,headers["Job Title"]).value=("MST" if value else
                         ("Associate" if current.upper()=="MST" else current))
        return _save_with_backup(book,workbook_path,expected_digest,private,"sync")
    finally:
        book.close()


def _delete_row(workbook_path, assoc, expected_digest, private):
    """Delete a single associate's row entirely.

    Row deletion shifts every subsequent row up by one -- a materially
    bigger change than a single-cell edit, since anything elsewhere in the
    workbook anchored to absolute row positions (formulas, named ranges)
    could be affected. Refuses to proceed if the associate number is
    ambiguous or already gone, exactly like the cell-edit path above.
    """
    book=load_workbook(workbook_path)
    try:
        ws=book.active
        headers={str(cell.value or "").strip():cell.column for cell in ws[1]}
        if "Assoc. #" not in headers:
            raise BridgeError("Drax columns changed during sync")
        positions=[i for i in range(2,ws.max_row+1)
                   if _normalize_id(ws.cell(i,headers["Assoc. #"]).value)==assoc]
        if len(positions)!=1:
            raise BridgeError("Drax row missing or duplicated during delete")
        ws.delete_rows(positions[0],1)
        return _save_with_backup(book,workbook_path,expected_digest,private,"delete")
    finally:
        book.close()


def delete_admin_row(drax_path, private, assoc):
    """Delete a single associate's row from the live Drax workbook,
    triggered by an Administration-page Delete action on a real
    Drax-sourced associate. A manually-added local-only associate has no
    Drax row at all and must never reach this function -- that path is
    v31_roster.delete_manual() instead, a plain local-only delete.

    Unlike the cell-edit write-back, there is no sensible local-only
    fallback here: deleting someone locally without also deleting them from
    Drax would just have them resync right back within 5 minutes. So this
    either genuinely removes the row, or reports exactly why it couldn't --
    callers should treat any non-deleted result as a hard failure, not a
    silent partial success.
    """
    if not enabled():
        return {"deleted": False, "reason": "write_disabled"}
    drax_path=Path(drax_path)
    if not drax_path.is_file():
        return {"deleted": False, "reason": "workbook_unavailable"}
    try:
        expected=_digest(drax_path)
        _,drax=_workbook_values(drax_path)
    except Exception as exc:
        return {"deleted": False, "reason": "workbook_unreadable: "+str(exc)[:150]}
    if assoc not in drax:
        return {"deleted": False, "reason": "not_in_drax"}
    try:
        backup=_delete_row(drax_path,assoc,expected,private)
    except Exception as exc:
        return {"deleted": False, "reason": str(exc)[:150]}
    return {"deleted": True, "backup": str(backup)}


def reconcile(v3_path, drax_path, private, *, write=False):
    """Compare three-way snapshots; no silent initial writes or blind overwrites."""
    v3_path=Path(v3_path)
    drax_path=Path(drax_path)
    if not v3_path.is_file() or not drax_path.is_file():
        raise BridgeError("Both the original V3 database and synchronized Drax workbook must be accessible")
    if not write or not enabled():
        write=False
    expected=_digest(drax_path)
    v3=_v3_values(v3_path)
    _,drax=_workbook_values(drax_path)
    pending={}
    conflicts=[]
    with _bridge_db(private) as conn:
        baseline={r[0]:(json.loads(r[1]),json.loads(r[2]))
                  for r in conn.execute("SELECT assoc,v3_values,drax_values FROM baseline")}
        first=not bool(baseline)
        new_baselines={}
        for assoc, v3_row in v3.items():
            if assoc not in drax:
                conflicts.append({"assoc":assoc,"field":"row","reason":"Absent from Drax; add manually"})
                continue
            workbook_row={k:drax[assoc][k] for k in FIELDS}
            old=baseline.get(assoc)
            if old is None:
                new_baselines[assoc]=(v3_row,workbook_row)
                continue
            prev_v3,prev_drax=old
            successful={}
            current_v3=dict(prev_v3)
            current_drax=dict(prev_drax)
            for field in FIELDS:
                source_changed=v3_row[field]!=prev_v3.get(field)
                workbook_changed=workbook_row[field]!=prev_drax.get(field)
                if source_changed and workbook_changed and v3_row[field]!=workbook_row[field]:
                    conflicts.append({"assoc":assoc,"field":field,"reason":"Changed in both V3 and Drax"})
                elif source_changed and not workbook_changed and v3_row[field]!=workbook_row[field]:
                    pending.setdefault(assoc,{})[field]=v3_row[field]
                    current_v3[field]=v3_row[field] if write else prev_v3.get(field)
                    current_drax[field]=v3_row[field] if write else prev_drax.get(field)
                else:
                    current_v3[field]=v3_row[field]
                    current_drax[field]=workbook_row[field]
            new_baselines[assoc]=(current_v3,current_drax)
        backup=None
        if pending and write:
            backup=_apply_cells(drax_path,pending,expected,private)
        # Never acknowledge changed source fields until workbook write succeeded.
        for assoc,(left,right) in new_baselines.items():
            conn.execute("""INSERT INTO baseline(assoc,v3_values,drax_values) VALUES(?,?,?)
                ON CONFLICT(assoc) DO UPDATE SET
                  v3_values=excluded.v3_values,drax_values=excluded.drax_values""",
                (assoc,json.dumps(left,sort_keys=True),json.dumps(right,sort_keys=True)))
        # V3 deletions are NOT mirrored automatically; require explicit review.
        deletions=sorted(set(baseline)-set(v3))
        for assoc in deletions:
            conflicts.append({"assoc":assoc,"field":"row","reason":"Removed from V3; manual review required"})
        conn.execute("INSERT INTO activity(created_at,assoc,field,result,note) VALUES (?,?,?,?,?)",
            (datetime.now(TZ).isoformat(),"*","scan","write" if write else "dry-run",
             f"pending={sum(len(x) for x in pending.values())}; conflicts={len(conflicts)}"))
    return {"first_baseline":first,"write_enabled":bool(write),
            "candidate_cell_changes":sum(len(x) for x in pending.values()),
            "workbook_updated":bool(write and pending),
            "backup_created":str(backup) if backup else None,
            "conflicts":conflicts,
            "note":"Drax edits flow into V3.1 through its existing 5-minute workbook refresh."}
