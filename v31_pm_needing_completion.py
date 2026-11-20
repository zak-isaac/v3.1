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
    # Chronological observations make the PM backlog survive daily exports
    # without rewriting older shift history or needing a second PM file.
    conn.execute("""CREATE TABLE IF NOT EXISTS pm_due_observations(
      shift_id INTEGER NOT NULL,wo_number TEXT NOT NULL,
      due_iso TEXT NOT NULL, asset_num TEXT NOT NULL DEFAULT '',
      wo_status TEXT NOT NULL DEFAULT '', status_code TEXT NOT NULL DEFAULT '',
      state TEXT NOT NULL DEFAULT '',source_updated_at TEXT NOT NULL DEFAULT '',
      item_json TEXT NOT NULL,observed_at TEXT NOT NULL,
      PRIMARY KEY(shift_id,wo_number))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS pm_due_observations_due
      ON pm_due_observations(due_iso,shift_id)""")

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
    seen=datetime.now(TZ).isoformat()
    # The same WO can appear on multiple labor rows and across daily files.
    # Keep the newest source timestamp, and never regress a known Completed
    # state to a stale pending status without newer Cetaris evidence.
    for item in result.pop("catalog_rows",[]):
        latest=conn.execute("""SELECT source_updated_at,state FROM pm_due_observations
            WHERE wo_number=? ORDER BY shift_id DESC LIMIT 1""",
            (item["wo_number"],)).fetchone()
        timestamp=item.pop("_updated","")
        if latest:
            old_timestamp=latest["source_updated_at"]
            if old_timestamp and (not timestamp or timestamp<old_timestamp):
                continue
            if (not timestamp or timestamp==old_timestamp) and latest["state"]=="completed" and item["state"]!="completed":
                continue
        conn.execute("""INSERT INTO pm_due_observations(
          shift_id,wo_number,due_iso,asset_num,wo_status,status_code,
          state,source_updated_at,item_json,observed_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(shift_id,wo_number) DO UPDATE SET
          due_iso=excluded.due_iso,asset_num=excluded.asset_num,
          wo_status=excluded.wo_status,status_code=excluded.status_code,
          state=excluded.state,source_updated_at=excluded.source_updated_at,
          item_json=excluded.item_json,observed_at=excluded.observed_at""",
          (shift_id,item["wo_number"],item["due_iso"],item["asset_num"],
           item["wo_status"],item["status_code"],item["state"],timestamp,
           json.dumps(item),seen))
    conn.execute("""INSERT INTO pm_due_wo_details
      (shift_id,filename,result_json,scanned_at) VALUES(?,?,?,?)
      ON CONFLICT(shift_id) DO UPDATE SET filename=excluded.filename,
      result_json=excluded.result_json,scanned_at=excluded.scanned_at""",
      (shift_id,Path(filename).name[:120],json.dumps(result),seen))
    return {"available":result["available"],"due_count":len(result["rows"]),
            "target_dates":result["target_dates"],
            "to_be_done_seen":result["to_be_done_seen"],
            "notes":result["notes"]}

def _completed_wo_numbers(conn,shift_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_pm'").fetchone():
        return set()
    # A later shift must not retroactively mark an earlier shift's due
    # inventory completed. Only accept completed PMs already reported by
    # the time the target shift opened, plus its own PM completions.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shifts'").fetchone():
        target=conn.execute("SELECT created_at FROM shifts WHERE id=?",(shift_id,)).fetchone()
        if target:
            return {r[0] for r in conn.execute(
                """SELECT DISTINCT p.wo_number FROM report_pm p
                   JOIN shifts s ON s.id=p.shift_id
                   WHERE p.selection_hidden=0
                     AND (s.created_at<=? OR p.shift_id=?)""",
                (target["created_at"],shift_id))}
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT wo_number FROM report_pm WHERE selection_hidden=0")}

def get(conn,shift_id,include_data=False):
    """One-file PM inventory, with an auditable persistent overdue backlog.

    Most recent observation on or before this shift wins. Later shifts may
    resolve an old overdue WO, but may not retroactively modify yesterday's
    due/overdue snapshot. Missing WOs are carried forward as last-known
    outstanding, NEVER silently treated as completed.
    """
    ensure(conn)
    code,shift_day=_shift_info(conn,shift_id)
    dates=due_dates_for_shift(code,shift_day)
    start=min(dates)
    final=max(dates)
    auto=conn.execute("SELECT * FROM pm_due_wo_details WHERE shift_id=?",
                      (shift_id,)).fetchone()
    raw=json.loads(auto["result_json"]) if auto else {}
    available=bool(raw.get("available"))
    notes=list(raw.get("notes",[]))
    matches={}
    for obs in conn.execute("""SELECT * FROM pm_due_observations
              WHERE shift_id<=? AND due_iso<=?
              ORDER BY shift_id DESC""",(shift_id,final.isoformat())):
        if obs["wo_number"] in matches:
            continue
        item=json.loads(obs["item_json"])
        item["last_seen_shift_id"]=obs["shift_id"]
        item["last_seen_at"]=obs["observed_at"]
        item["due_bucket"]="past_due" if item["due_iso"]<start.isoformat() else "due_today"
        matches[obs["wo_number"]]=item
    # A verified completed PM is a separate shift-work fact, not a generic
    # consequence of a work order merely appearing in the due inventory.
    completed=_completed_wo_numbers(conn,shift_id)
    for item in matches.values():
        if item["wo_number"] in completed:
            item["state"]="completed"
            item["wo_status"]="Completed (verified in shift PM history)"
    due_rows=sorted((i for i in matches.values() if i["due_bucket"]=="due_today"),
                    key=lambda i:(i["due_iso"],i["wo_number"]))
    overdue=sorted((i for i in matches.values()
                    if i["due_bucket"]=="past_due" and i["state"]!="completed"),
                   key=lambda i:(i["due_iso"],i["wo_number"]))
    outstanding=[i for i in due_rows if i["state"]!="completed"]
    if not available:
        notes.append("No complete WO Details report staged for this shift. Prior PM statuses are last-known values.")
    if not raw.get("to_be_done_seen") and available:
        notes.append("Single-file completeness is unverified: the export contains no To-Be-Done WOs; confirm Cetaris search filters include all unassigned PMs.")
    return {
      "present":False,"filename":auto["filename"] if auto else "",
      "extension":"","parsed":available,"has_source":bool(available or matches),
      "auto_available":available,"auto_filename":auto["filename"] if auto else "",
      "uploaded_daily_pm":False,
      "rows":due_rows,"outstanding_rows":outstanding,"past_due_rows":overdue,
      "due_count":len(due_rows),"completed_count":len(due_rows)-len(outstanding),
      "count":len(outstanding),"past_due_count":len(overdue),
      "outstanding_total":len(outstanding)+len(overdue),
      "notes":list(dict.fromkeys(notes)),
      "target_dates":sorted(d.isoformat() for d in dates),
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
