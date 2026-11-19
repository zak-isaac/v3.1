"""Daily due-PM inventory for IND3 shift reporting.

A staged WO Details file can expose today's PMs (including unassigned
To-Be-Done WOs). The separate Today’s DAILY PMs upload remains supported
because a filtered WO Details export might omit unassigned pending work.
The two are merged on WO number, and completed PMs are reconciled without
assuming that merely importing a WO means work was performed.
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
from v31_pm_due_scanner import scan, due_dates_for_shift, due_date, DONE_CODES
from v31_cetaris_codes import normalize_wo_status
import v31_pm_table_style as pm_style

TZ=ZoneInfo("America/Indiana/Indianapolis")
ALLOWED_EXTS={".xlsx",".xls",".csv",".pdf",".png",".jpg",".jpeg"}
PARSABLE_EXTS={".xlsx",".xls",".csv"}
MAX_BYTES=12*1024*1024

def ensure(conn:sqlite3.Connection):
    conn.execute("""CREATE TABLE IF NOT EXISTS pm_needing_completion(
      shift_id INTEGER PRIMARY KEY,filename TEXT NOT NULL,
      extension TEXT NOT NULL,file_data BLOB NOT NULL,
      entries_json TEXT NOT NULL,uploaded_by TEXT NOT NULL,
      uploaded_at TEXT NOT NULL,sha256 TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pm_due_wo_details(
      shift_id INTEGER PRIMARY KEY,filename TEXT NOT NULL,
      result_json TEXT NOT NULL,scanned_at TEXT NOT NULL)""")

def _shift_info(conn,shift_id):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shifts'").fetchone():
        row=conn.execute("SELECT code,shift_day FROM shifts WHERE id=?",(shift_id,)).fetchone()
        if row:
            return row["code"],row["shift_day"]
    return "S1",datetime.now(TZ).date().isoformat()

def scan_wo_details(conn,shift_id,filename,content):
    """Called at WO Details staging, independent of technician selection."""
    ensure(conn)
    code,shift_day=_shift_info(conn,shift_id)
    try:
        result=scan(content,code,shift_day)
    except ValueError as exc:
        result={"rows":[],"available":False,
                "target_dates":sorted(d.isoformat() for d in due_dates_for_shift(code,shift_day)),
                "matching":0,"missing_due":0,"to_be_done_seen":0,
                "scanned_rows":0,"notes":[str(exc)]}
    conn.execute("""INSERT INTO pm_due_wo_details
      (shift_id,filename,result_json,scanned_at) VALUES(?,?,?,?)
      ON CONFLICT(shift_id) DO UPDATE SET filename=excluded.filename,
      result_json=excluded.result_json,scanned_at=excluded.scanned_at""",
      (shift_id,Path(filename).name[:120],json.dumps(result),
       datetime.now(TZ).isoformat()))
    return {"available":result["available"],"due_count":len(result["rows"]),
            "target_dates":result["target_dates"],
            "to_be_done_seen":result["to_be_done_seen"],
            "notes":result["notes"]}

def _completed_wo_numbers(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_pm'").fetchone():
        return set()
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT wo_number FROM report_pm WHERE selection_hidden=0")}

def get(conn,shift_id,include_data=False):
    ensure(conn)
    code,shift_day=_shift_info(conn,shift_id)
    dates=due_dates_for_shift(code,shift_day)
    row=conn.execute("SELECT * FROM pm_needing_completion WHERE shift_id=?",(shift_id,)).fetchone()
    auto=conn.execute("SELECT * FROM pm_due_wo_details WHERE shift_id=?",(shift_id,)).fetchone()
    raw_auto=json.loads(auto["result_json"]) if auto else {}
    auto_available=bool(raw_auto.get("available"))
    parsed_manual=bool(row and row["extension"] in PARSABLE_EXTS)
    notes=list(raw_auto.get("notes",[]))
    matched={}
    for item in raw_auto.get("rows",[]):
        matched[item["wo_number"]]=dict(item)
    manual_excluded=manual_missing_due=0
    if parsed_manual:
        for p in json.loads(row["entries_json"]):
            due=due_date(p.get("pm_due_date"))
            if not due:
                manual_missing_due+=1
                continue
            if due not in dates:
                manual_excluded+=1
                continue
            wo=p["wo_number"].strip()
            if not wo:
                continue
            raw_status=p.get("wo_status") or "To be Done"
            status,status_code=normalize_wo_status(raw_status)
            if raw_status.casefold() in ("to be done","to-be-done","t"):
                status,status_code="To be Done","T"
            status=status or raw_status
            state="completed" if status_code in DONE_CODES else "outstanding"
            manual={"wo_number":wo,"wo_date":p.get("wo_date",""),
                    "pm_due_date":due.strftime("%m/%d/%Y"),
                    "due_iso":due.isoformat(),"promise_date":p.get("promise_date",""),
                    "asset_num":p.get("asset_num",""),"wo_status":status,
                    "status_code":status_code,"state":state,
                    "source":"Today's DAILY PMs"}
            if wo in matched:
                earlier=matched[wo]
                # A Completed/Closed status in WO Details (or a verified
                # completed PM below) wins over an earlier To-Be-Done export.
                if earlier["state"]=="completed" and state!="completed":
                    continue
                # Prefer the dedicated PM report's asset/due data, but don't
                # discard a known Cetaris status or existing asset ID.
                manual["asset_num"]=manual["asset_num"] or earlier.get("asset_num","")
            matched[wo]=manual
    if manual_excluded:
        notes.append(f"{manual_excluded} rows in uploaded PM list were due on other dates and were excluded")
    if manual_missing_due:
        notes.append(f"{manual_missing_due} uploaded PM rows have no valid PM Due Date and cannot be assigned")
    if not row and not auto_available:
        notes.append("Upload Today's DAILY PMs or a WO Details export containing PM Due Date to verify due work")
    if auto_available and not row and not raw_auto.get("to_be_done_seen"):
        # Do not declare facility PM coverage complete from a filtered
        # WO Details export that includes no To-Be-Done WOs.
        notes.append("WO Details alone may not include the full list of To-Be-Done PMs")
    completed=_completed_wo_numbers(conn)
    for item in matched.values():
        if item["wo_number"] in completed:
            item["state"]="completed"
            item["wo_status"]="Completed (verified in shift PM history)"
    ordered=sorted(matched.values(),key=lambda x:(x["due_iso"],x["wo_number"]))
    outstanding=[r for r in ordered if r["state"]!="completed"]
    result={
        "present":bool(row), "filename":row["filename"] if row else (auto["filename"] if auto else ""),
        "extension":row["extension"] if row else "", "parsed":parsed_manual or auto_available,
        "has_source":bool(row or auto_available),
        "auto_available":auto_available,
        "auto_filename":auto["filename"] if auto else "",
        "uploaded_daily_pm":parsed_manual,
        "rows":ordered,"outstanding_rows":outstanding,
        "due_count":len(ordered),"completed_count":len(ordered)-len(outstanding),
        "count":len(outstanding),"notes":list(dict.fromkeys(notes)),
        "target_dates":sorted(d.isoformat() for d in dates),
        "uploaded_at":row["uploaded_at"] if row else "",
        "sha256":row["sha256"] if row else "",
    }
    if include_data and row:
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
        # Store all source dates in the original uploaded file. get() filters
        # on the shift calendar date (S3/S5 include after midnight).
        seen=set()
        for p in parsed:
            wo=p.wo_number.strip()
            if not wo or wo in seen:
                continue
            seen.add(wo)
            entries.append({"wo_number":wo,"wo_date":p.wo_date,
                            "pm_due_date":p.pm_due_date,"promise_date":p.promise_date,
                            "asset_num":p.asset_num,"wo_status":p.wo_status})
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
    dates=", ".join(pm["target_dates"])
    headline='<p style="margin:16px 0 2px"><strong style="font-size:16px">PMs Needing Completion</strong></p>'
    if not pm["has_source"]:
        return headline+'<p style="margin:0 0 6px;font-size:14px;color:#6b7280;font-style:italic;">No daily PM report or qualifying WO Details due-date data available. Outstanding PM count is unverified.</p>'
    if not pm["parsed"]:
        return headline+'<p style="margin:0 0 6px;font-size:14px;color:#6b7280;font-style:italic;">PM reference attached; its contents were not parsed.</p>'
    summary=(f'<p style="font-size:14px;">PM due {escape(dates)}: '
             f'<strong>{pm["due_count"]} identified</strong> · '
             f'<strong>{pm["count"]} still needing completion</strong> · '
             f'{pm["completed_count"]} completed</p>')
    caveat=(''.join(f'<p style="font-size:12px;color:#9a6700;">{escape(n)}</p>'
                    for n in pm["notes"]))
    if not pm["rows"]:
        return headline+summary+caveat+'<p style="font-style:italic;">No due PMs found in the available export(s); confirm the To-Be-Done list covers these dates.</p>'
    border="border:1px solid #d1d5db;"
    th_s=f"padding:8px 12px;text-align:left;font-size:14px;font-weight:bold;background:#{pm_style.PM_HEADER_HEX};color:#fff;font-family:Calibri,Arial,sans-serif;{border}"
    td_s=f"padding:7px 12px;font-size:14px;font-family:Calibri,Arial,sans-serif;{border}"
    hdr=(f"<tr><th style='{th_s}'>WO #</th><th style='{th_s}'>Asset</th>"
         f"<th style='{th_s}'>PM Due Date</th><th style='{th_s}'>WO Status</th>"
         f"<th style='{th_s}'>Promise Date / Time</th>"
         f"<th style='{th_s}'>WO Date / Time</th></tr>")
    body_rows=""
    for i,row in enumerate(pm["rows"]):
        row_bg=f"#{pm_style.pm_zebra_hex(i)}"
        bold="font-weight:bold;" if row["state"]!="completed" else ""
        status=(row["wo_status"] or "Status not provided")
        if row["state"]!="completed":
            status+=" — NEEDS COMPLETION"
        body_rows+=(f"<tr style='background:{row_bg};'>"
                    f"<td style='{td_s}font-family:monospace;'>{escape(row['wo_number'])}</td>"
                    f"<td style='{td_s}{bold}'>{escape(row['asset_num'] or '')}</td>"
                    f"<td style='{td_s}{bold}'>{escape(row['pm_due_date'] or '')}</td>"
                    f"<td style='{td_s}{bold}'>{escape(status)}</td>"
                    f"<td style='{td_s}'>{escape(row['promise_date'] or '')}</td>"
                    f"<td style='{td_s}color:#666;'>{escape(row['wo_date'] or '')}</td></tr>")
    table=(f"<table style='width:100%;border-collapse:collapse;margin-top:4px;'>"
           f"<thead>{hdr}</thead><tbody>{body_rows}</tbody></table>")
    return headline+summary+caveat+table
