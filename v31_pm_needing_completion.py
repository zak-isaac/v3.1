"""Daily due-PM inventory for IND3 shift reporting.

A staged WO Details file can expose today's PMs (including unassigned
To-Be-Done WOs). The separate Today’s DAILY PMs upload remains supported
because a filtered WO Details export might omit unassigned pending work.
The two are merged on WO number, and completed PMs are reconciled without
assuming that merely importing a WO means work was performed.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,date
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

def _as_of_date(code, shift_day):
    """Date for the running shift, including the after-midnight S3/S5 half.

    The source WO Due Date is a calendar-day deadline (11:59 PM local).
    A shift started yesterday must show yesterday's PM as RED after
    midnight and today's PM as YELLOW, without carrying old PM records.
    """
    start=date.fromisoformat(shift_day)
    if code in ("S3","S5"):
        # Use app's shared clock so this matches live Indiana time as well
        # as the isolated shift-time simulation.
        import app as _app
        now=_app.datetime.now(TZ).date()
        if now==start+__import__("datetime").timedelta(days=1):
            return now
    return start


def scan_wo_details(conn,shift_id,filename,content):
    """Called at WO Details staging, independent of technician selection."""
    ensure(conn)
    code,shift_day=_shift_info(conn,shift_id)
    report_day=_as_of_date(code,shift_day)
    try:
        result=scan(content,code,report_day)
    except ValueError as exc:
        result={"rows":[],"available":False,
                "target_dates":[report_day.isoformat()],
                "matching":0,"missing_due":0,"to_be_done_seen":0,
                "scanned_rows":0,"notes":[str(exc)]}
    # Save ONLY this shift's newly staged WO Details snapshot. Never
    # write PM observations or pull overdue WOs from earlier uploads.
    # Historical tables may exist in production; leave them untouched
    # for compatibility, but never read them for current PM urgency.
    scanned=datetime.now(TZ).isoformat()
    conn.execute("""INSERT INTO pm_due_wo_details
      (shift_id,filename,result_json,scanned_at) VALUES(?,?,?,?)
      ON CONFLICT(shift_id) DO UPDATE SET filename=excluded.filename,
      result_json=excluded.result_json,scanned_at=excluded.scanned_at""",
      (shift_id,Path(filename).name[:120],json.dumps(result),scanned))
    return {"available":result["available"],"due_count":len(result["rows"]),
            "target_dates":result["target_dates"],
            "to_be_done_seen":result["to_be_done_seen"],
            "notes":result["notes"]}


def get(conn,shift_id,include_data=False):
    """Show only the PM statuses present in THIS shift's latest WO Details file.

    RED: unfinished PM whose due date is before the report date.
    YELLOW: unfinished PM due on the report date, through 11:59 PM.
    Closed/Completed PMs are not overdue or urgent. Future PMs are excluded.
    No earlier upload is consulted, and missing records are never invented.
    """
    ensure(conn)
    code,shift_day=_shift_info(conn,shift_id)
    reference=_as_of_date(code,shift_day)
    auto=conn.execute("SELECT * FROM pm_due_wo_details WHERE shift_id=?",
                      (shift_id,)).fetchone()
    raw=json.loads(auto["result_json"]) if auto else {}
    available=bool(raw.get("available"))
    notes=list(raw.get("notes",[]))
    by_wo={}
    # catalog_rows contains all PM due dates in this ONE file; old stored
    # snapshots without catalog_rows can still display their known due rows.
    for item in raw.get("catalog_rows",raw.get("rows",[])):
        if item.get("wo_number"):
            by_wo[item["wo_number"]]=dict(item)
    today=[]
    overdue=[]
    for item in by_wo.values():
        due=due_date(item.get("due_iso") or item.get("pm_due_date"))
        if not due:
            continue
        if due>reference:
            continue
        item["due_bucket"]="past_due" if due<reference else "due_today"
        item["urgency"]=(
            "none" if item.get("state")=="completed"
            else "red" if due<reference else "yellow"
        )
        if due<reference:
            if item.get("state")!="completed":
                overdue.append(item)
        else:
            today.append(item)
    today.sort(key=lambda p:(p["due_iso"],p["wo_number"]))
    overdue.sort(key=lambda p:(p["due_iso"],p["wo_number"]))
    outstanding=[p for p in today if p.get("state")!="completed"]
    if not available:
        notes.append(
            "No usable WO Details export for this shift: daily and past-due PM counts are unverified."
        )
    elif not raw.get("to_be_done_seen"):
        notes.append(
            "No To-Be-Done PMs were included. Check that the WO Details export includes all pending and unassigned PMs."
        )
    return {
      "present":False,"filename":auto["filename"] if auto else "",
      "extension":"","parsed":available,"has_source":available,
      "auto_available":available,"auto_filename":auto["filename"] if auto else "",
      "uploaded_daily_pm":False,
      "rows":today,"outstanding_rows":outstanding,"past_due_rows":overdue,
      "due_count":len(today),"completed_count":len(today)-len(outstanding),
      "count":len(outstanding),"past_due_count":len(overdue),
      "outstanding_total":len(outstanding)+len(overdue),
      "notes":list(dict.fromkeys(notes)),
      "target_dates":[reference.isoformat()],"as_of_date":reference.isoformat(),
      "uploaded_at":auto["scanned_at"] if auto else "",
      "sha256":"",
    }


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
    """Outlook-compatible, urgency-colored PM status from this export only."""
    pm=get(conn,shift_id)
    headline='<p style="margin:16px 0 3px"><strong style="font-size:16px">PMs Needing Completion</strong></p>'
    if not pm["has_source"]:
        return headline+(
            '<p style="color:#9A3412;font-size:14px;">WO Details not available; '
            'PM urgency has not been verified.</p>'
        )
    summary=(
        f'<p style="font-size:14px;">PM status as of {escape(pm["as_of_date"])}: '
        f'<strong>{pm["due_count"]} due today</strong> · '
        f'<strong>{pm["count"]} outstanding today</strong> · '
        f'{pm["completed_count"]} completed · '
        f'<strong style="color:#991B1B;">{pm["past_due_count"]} past due</strong></p>'
    )
    notes="".join(
        f'<p style="font-size:12px;color:#92400E;">{escape(note)}</p>'
        for note in pm["notes"]
    )
    border="border:1px solid #D1D5DB;"
    th=(f"padding:8px 10px;text-align:left;font-size:14px;font-weight:bold;"
        f"background-color:#{pm_style.PM_HEADER_HEX};color:#FFFFFF;"
        f"font-family:Calibri,Arial,sans-serif;{border}")
    heading=(f"<tr><th style='{th}'>WO #</th>"
             f"<th style='{th}'>Asset</th>"
             f"<th style='{th}'>PM Due Date</th>"
             f"<th style='{th}'>WO Status</th>"
             f"<th style='{th}'>Urgency</th>"
             f"<th style='{th}'>Promise Date / Time</th>"
             f"<th style='{th}'>WO Date / Time</th></tr>")

    def render_table(rows,urgency):
        body=[]
        for row in rows:
            # Outlook's Word HTML engine may ignore background on <tr>.
            # Put urgency background on EVERY <td>.
            if row.get("state")=="completed":
                bg,ink,label="#F3F4F6","#374151","Completed"
            elif urgency=="past":
                bg,ink,label="#FDE8E8","#991B1B","PAST DUE"
            else:
                bg,ink,label="#FFF2C7","#805500","DUE TODAY — 11:59 PM"
            cell=(f"padding:8px 10px;font-size:14px;font-family:Calibri,Arial,"
                  f"sans-serif;background-color:{bg};color:{ink};{border}")
            bold=cell+"font-weight:bold;"
            body.append(
                "<tr>"
                f"<td style='{bold}'>{escape(row['wo_number'])}</td>"
                f"<td style='{cell}'>{escape(row.get('asset_num') or '')}</td>"
                f"<td style='{bold}'>{escape(row.get('pm_due_date') or '')}</td>"
                f"<td style='{cell}'>{escape(row.get('wo_status') or '')}</td>"
                f"<td style='{bold}'>{escape(label)}</td>"
                f"<td style='{cell}'>{escape(row.get('promise_date') or '')}</td>"
                f"<td style='{cell}'>{escape(row.get('wo_date') or '')}</td>"
                "</tr>"
            )
        return (f"<table style='width:100%;border-collapse:collapse;margin-top:4px;'>"
                f"<thead>{heading}</thead><tbody>{''.join(body)}</tbody></table>")

    due_title=(
        '<p style="margin:12px 0 3px;"><strong style="font-size:15px;'
        'color:#805500;">Due Today — until 11:59 PM</strong></p>'
    )
    due=render_table(pm["rows"],"today") if pm["rows"] else (
        '<p style="font-size:13px;">No PMs due today in this WO Details export.</p>'
    )
    past_title=(
        '<p style="margin:16px 0 3px;"><strong style="font-size:15px;'
        'color:#991B1B;">Past Due PMs</strong></p>'
    )
    overdue=render_table(pm["past_due_rows"],"past") if pm["past_due_rows"] else (
        '<p style="font-size:13px;">No unfinished past-due PMs in this WO Details export.</p>'
    )
    return headline+summary+notes+due_title+due+past_title+overdue
