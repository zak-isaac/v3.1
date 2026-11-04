"""Shift-scoped V2-compatible PMs Needing Completion report storage.

This is separate from completed-PM counting in the Cetaris work order import.
Uploaded reference files are stored in the private V3.1 SQLite database,
not in git or a public /static URL. Authoritative data for the passdown:
parsed CSV/XLSX/XLS rows, with PDF/image uploads clearly identified as
unparsed reference attachments rather than silently declaring 'none'.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape
import hashlib
import json
import sqlite3

from pm_pending_parser import parse_pm_pending

TZ=ZoneInfo("America/Indiana/Indianapolis")
ALLOWED_EXTS={".xlsx",".xls",".csv",".pdf",".png",".jpg",".jpeg"}
PARSABLE_EXTS={".xlsx",".xls",".csv"}
MAX_BYTES=12*1024*1024

def ensure(conn:sqlite3.Connection):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pm_needing_completion'").fetchone():
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS pm_needing_completion(
      shift_id INTEGER PRIMARY KEY,filename TEXT NOT NULL,
      extension TEXT NOT NULL,file_data BLOB NOT NULL,
      entries_json TEXT NOT NULL,uploaded_by TEXT NOT NULL,
      uploaded_at TEXT NOT NULL,sha256 TEXT NOT NULL)""")

def get(conn,shift_id,include_data=False):
    ensure(conn)
    row=conn.execute("SELECT * FROM pm_needing_completion WHERE shift_id=?",(shift_id,)).fetchone()
    if not row:
        return {"present":False,"filename":"","rows":[],"parsed":False,"count":0}
    entries=json.loads(row["entries_json"])
    result={"present":True,"filename":row["filename"],"extension":row["extension"],
            "rows":entries,"parsed":row["extension"] in PARSABLE_EXTS,
            "count":len(entries),"uploaded_at":row["uploaded_at"],
            "sha256":row["sha256"]}
    if include_data:
        result["file_data"]=bytes(row["file_data"])
    return result

def upload(conn,shift_id,filename,content,actor):
    filename=Path(str(filename or "").replace("\\","/")).name[:120]
    ext=Path(filename).suffix.casefold()
    if ext not in ALLOWED_EXTS:
        raise ValueError("PM report must be Excel, CSV, PDF or an image")
    if not content or len(content)>MAX_BYTES:
        raise ValueError("PM report must be nonempty and no larger than 12MB")
    entries=[]
    if ext in PARSABLE_EXTS:
        parsed=parse_pm_pending(content,filename)
        if not parsed:
            raise ValueError("No PM WO rows could be parsed; check report headings/format")
        # File may repeat a WO; count each outstanding PM once.
        seen=set()
        for p in parsed:
            wo=p.wo_number.strip()
            if not wo or wo in seen:
                continue
            seen.add(wo)
            entries.append({"wo_number":wo,"wo_date":p.wo_date,
                            "pm_due_date":p.pm_due_date,"promise_date":p.promise_date,
                            "asset_num":p.asset_num})
    ensure(conn)
    conn.execute("""INSERT INTO pm_needing_completion
      (shift_id,filename,extension,file_data,entries_json,uploaded_by,uploaded_at,sha256)
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(shift_id) DO UPDATE SET filename=excluded.filename,
      extension=excluded.extension,file_data=excluded.file_data,
      entries_json=excluded.entries_json,uploaded_by=excluded.uploaded_by,
      uploaded_at=excluded.uploaded_at,sha256=excluded.sha256""",
      (shift_id,filename,ext,content,json.dumps(entries),actor,
       datetime.now(TZ).isoformat(),hashlib.sha256(content).hexdigest()))
    return get(conn,shift_id)

def delete(conn,shift_id):
    ensure(conn)
    removed=conn.execute("DELETE FROM pm_needing_completion WHERE shift_id=?",(shift_id,))
    return removed.rowcount>0

def report_section(conn,shift_id):
    pm=get(conn,shift_id)
    headline='<p style="margin:16px 0 2px"><strong style="font-size:16px">PMs Needing Completion</strong></p>'
    if not pm["present"]:
        return headline+'<p>No PMs Needing Completion report uploaded for this shift.</p>'
    detail='<p>Uploaded report: '+escape(pm["filename"])+'</p>'
    if not pm["parsed"]:
        return headline+detail+'<p>Reference file attached; its contents were not machine-parsed.</p>'
    if not pm["rows"]:
        return headline+detail+'<p>Report contains no parsed PM rows.</p>'
    parts=[]
    for row in pm["rows"]:
        line='<strong>WO# '+escape(row["wo_number"])+'</strong>'
        if row["asset_num"]:line+=' — Asset '+escape(row["asset_num"])
        if row["pm_due_date"]:line+='; due '+escape(row["pm_due_date"])
        if row["promise_date"]:line+='; promised '+escape(row["promise_date"])
        parts.append('<li>'+line+'</li>')
    return headline+detail+'<ul style="padding-left:20px">'+''.join(parts)+'</ul>'
