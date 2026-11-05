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
import v31_pm_table_style as pm_style

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
        return headline+'<p style="margin:0 0 6px;font-size:14px;color:#6b7280;font-style:italic;">No PMs Needing Completion report uploaded for this shift.</p>'
    detail='<p>Uploaded report: '+escape(pm["filename"])+'</p>'
    if not pm["parsed"]:
        return headline+detail+'<p style="margin:0 0 6px;font-size:14px;color:#6b7280;font-style:italic;">Reference file attached; its contents were not machine-parsed.</p>'
    if not pm["rows"]:
        return headline+detail+'<p style="margin:0 0 6px;font-size:14px;color:#6b7280;font-style:italic;">Report contains no parsed PM rows.</p>'
    # Same bordered/zebra table styling as V3's real Outlook passdown --
    # single source of truth in v31_pm_table_style.py.
    border="border:1px solid #d1d5db;"
    th_s=f"padding:8px 12px;text-align:left;font-size:14px;font-weight:bold;background:#{pm_style.PM_HEADER_HEX};color:#fff;font-family:Calibri,Arial,sans-serif;{border}"
    td_s=f"padding:7px 12px;font-size:14px;font-family:Calibri,Arial,sans-serif;{border}"
    hdr=(f"<tr><th style='{th_s}'>WO #</th><th style='{th_s}'>Asset</th>"
         f"<th style='{th_s}'>PM Due Date</th><th style='{th_s}'>Promise Date / Time</th>"
         f"<th style='{th_s}'>WO Date / Time</th></tr>")
    body_rows=""
    for i,row in enumerate(pm["rows"]):
        row_bg=f"#{pm_style.pm_zebra_hex(i)}"
        body_rows+=(f"<tr style='background:{row_bg};'>"
                    f"<td style='{td_s}font-family:monospace;'>{escape(row['wo_number'])}</td>"
                    f"<td style='{td_s}font-weight:bold;'>{escape(row['asset_num'] or '')}</td>"
                    f"<td style='{td_s}font-weight:bold;'>{escape(row['pm_due_date'] or '')}</td>"
                    f"<td style='{td_s}'>{escape(row['promise_date'] or '')}</td>"
                    f"<td style='{td_s}color:#666;'>{escape(row['wo_date'] or '')}</td></tr>")
    table=(f"<table style='width:100%;border-collapse:collapse;margin-top:4px;'>"
           f"<thead>{hdr}</thead><tbody>{body_rows}</tbody></table>")
    return headline+detail+table
