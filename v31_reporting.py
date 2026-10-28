"""Persistent, shift-bound Cetaris import for IND3 V3.1.

Reuses reviewed V3 parsers, grouping and actual-technician shift attribution.
No production V3 database or real roster is changed. No AI rewriting yet.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from v31_cetaris import parse_excel, technician_counts, select_records, MAX_SIZE
from v31_split import assign, _person_key

TZ=ZoneInfo("America/Indiana/Indianapolis")


def _table(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS report_stage (
      shift_id INTEGER PRIMARY KEY, filename TEXT NOT NULL,
      raw_json TEXT NOT NULL, stats_json TEXT NOT NULL, uploaded_by TEXT NOT NULL,
      uploaded_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS report_techs (
      shift_id INTEGER PRIMARY KEY, selected_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS report_wo (
      id INTEGER PRIMARY KEY AUTOINCREMENT, shift_id INTEGER NOT NULL,
      wo_number TEXT NOT NULL, asset TEXT NOT NULL DEFAULT '',
      category TEXT NOT NULL DEFAULT '', cause TEXT NOT NULL DEFAULT '',
      tech TEXT NOT NULL DEFAULT '', remarks TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'RTS',
      excluded INTEGER NOT NULL DEFAULT 0, edited INTEGER NOT NULL DEFAULT 0,
      imported_at TEXT NOT NULL,
      UNIQUE(shift_id,wo_number));
    CREATE TABLE IF NOT EXISTS report_pm (
      shift_id INTEGER NOT NULL, wo_number TEXT NOT NULL,
      asset TEXT NOT NULL DEFAULT '', tech TEXT NOT NULL DEFAULT '',
      completed_at TEXT NOT NULL, PRIMARY KEY(shift_id,wo_number));
    CREATE TABLE IF NOT EXISTS report_audit (
      shift_id INTEGER PRIMARY KEY, summary_json TEXT NOT NULL DEFAULT '{}',
      pending_json TEXT NOT NULL DEFAULT '[]',
      ignored_json TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL);
    """)
    # Migration only adds tables; original V2/V3 archive and new V3.1 shifts stay intact.


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":"))


def _now():
    return datetime.now(TZ).isoformat()


def stage(conn, shift_id, payload: bytes, filename: str, actor: str):
    _table(conn)
    stats={}
    rows=parse_excel(payload,stats)
    filename=Path(filename.replace("\\","/")).name[:120] or "cetaris.xlsx"
    conn.execute("""INSERT INTO report_stage(shift_id,filename,raw_json,stats_json,uploaded_by,uploaded_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(shift_id) DO UPDATE SET
        filename=excluded.filename,raw_json=excluded.raw_json,stats_json=excluded.stats_json,
        uploaded_by=excluded.uploaded_by,uploaded_at=excluded.uploaded_at""",
        (shift_id,filename,_json(rows),_json(stats),actor,_now()))
    return {"filename":filename,"technicians":technician_counts(rows),
            "stats":stats,"staged":True,"requires_selection":True}


def stage_details(conn, shift_id):
    _table(conn)
    stage_row=conn.execute("SELECT * FROM report_stage WHERE shift_id=?",(shift_id,)).fetchone()
    if not stage_row:
        return {"staged":False,"filename":"","technicians":[],"stats":{}}
    rows=json.loads(stage_row["raw_json"])
    return {"staged":True,"filename":stage_row["filename"],
            "technicians":technician_counts(rows),
            "stats":json.loads(stage_row["stats_json"])}


def _existing_elsewhere(conn, shift_id, wo):
    # Prevent another active/historical local shift claiming the same WO.
    row=conn.execute("""SELECT shift_id FROM report_wo
                        WHERE shift_id<>? AND wo_number=? AND excluded=0
                        LIMIT 1""",(shift_id,wo)).fetchone()
    if not row:
        row=conn.execute("""SELECT shift_id FROM report_pm
                            WHERE shift_id<>? AND wo_number=? LIMIT 1""",
                         (shift_id,wo)).fetchone()
    return row["shift_id"] if row else None


def roster_shift_map(tech_names, associates):
    """Match names by exact normalized tokens, never guess from selection.

    Drax commonly uses First Last; Cetaris can use Last, First. Missing or
    conflicting Drax assignments remain unassigned for explicit import audit.
    """
    roster={}
    for associate in associates:
        if not associate.get("active") or not associate.get("shift"):
            continue
        key=_person_key(associate.get("name"))
        if key:
            roster.setdefault(key,set()).add(associate["shift"])
    mapped={}
    for tech in tech_names:
        key=_person_key(tech)
        matches=roster.get(key,set())
        if len(matches)==1:
            mapped[tech]=next(iter(matches))
    return mapped


def import_selection(conn, shift_id: int, shift_code: str, shift_day: str,
                     technicians: list[str], closure_reviews: dict[str,str] | None = None,
                     roster_shifts: dict[str,str] | None = None):
    _table(conn)
    staged=conn.execute("SELECT * FROM report_stage WHERE shift_id=?",(shift_id,)).fetchone()
    if not staged:
        raise ValueError("Upload a Cetaris file before selecting technicians")
    all_rows=json.loads(staged["raw_json"])
    actual={r["tech_name"] for r in all_rows}
    selected=set(technicians)
    if not selected or not selected.issubset(actual):
        raise ValueError("Select at least one technician from this Cetaris export")
    if len(selected)>100:
        raise ValueError("Too many technicians selected")
    # Exact reviewed V3 evidence, not administrative WO close timestamps.
    accepted,pending=assign(all_rows, selected, shift_code, shift_day,
                            roster_shifts=roster_shifts or {})
    # Do not silently include Completed non-PMs; MST must choose reminder.
    records,summary=select_records(accepted,selected,datetime.min,datetime.max,
                                    closure_reviews=closure_reviews or {})
    by_wo={}
    pm_by_wo={}
    for r in records:
        wo=r["wo_number"]
        kind=r["kind"]
        target=pm_by_wo if kind=="pm" else by_wo
        item=target.setdefault(wo,{"wo":wo,"asset":r.get("asset_id") or "",
            "category":r.get("rfr") or "Other",
            "cause":r.get("reported_issue") or "",
            "techs":[],"remarks":[],"status":"OOS" if kind=="oos" else ("PENDING" if r.get("closure_reminder") else "RTS")})
        if r.get("tech_name") and r["tech_name"] not in item["techs"]:
            item["techs"].append(r["tech_name"])
        for remark in r.get("remarks_list") or ([r["remarks"]] if r.get("remarks") else []):
            if remark and remark not in item["remarks"]:
                item["remarks"].append(remark)
        if kind=="oos":item["status"]="OOS"
    # PM takes precedence over any duplicate labor row from the same WO.
    for wo in pm_by_wo:by_wo.pop(wo,None)
    ignored=[]
    now=_now()
    added=updated=preserved=pm_count=0
    for wo,row in by_wo.items():
        owned=_existing_elsewhere(conn,shift_id,wo)
        if owned is not None:
            ignored.append({"wo_number":wo,"reason":"Already recorded in V3.1 shift #"+str(owned)})
            continue
        existing=conn.execute("SELECT id,edited FROM report_wo WHERE shift_id=? AND wo_number=?",
                              (shift_id,wo)).fetchone()
        if existing and existing["edited"]:
            preserved+=1
            continue
        fields=(row["asset"],row["category"],row["cause"],
                "; ".join(row["techs"]),"; ".join(row["remarks"]),row["status"])
        if existing:
            conn.execute("""UPDATE report_wo SET asset=?,category=?,cause=?,tech=?,
                remarks=?,status=?,imported_at=? WHERE id=?""",(*fields,now,existing["id"]))
            updated+=1
        else:
            conn.execute("""INSERT INTO report_wo
               (shift_id,wo_number,asset,category,cause,tech,remarks,status,imported_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",(shift_id,wo,*fields,now))
            added+=1
    for wo,row in pm_by_wo.items():
        owned=_existing_elsewhere(conn,shift_id,wo)
        if owned is not None:
            ignored.append({"wo_number":wo,"reason":"PM already recorded in shift #"+str(owned)})
            continue
        conn.execute("""INSERT INTO report_pm(shift_id,wo_number,asset,tech,completed_at)
             VALUES(?,?,?,?,?) ON CONFLICT(shift_id,wo_number) DO UPDATE SET
             asset=excluded.asset,tech=excluded.tech""",
             (shift_id,wo,row["asset"],"; ".join(row["techs"]),now))
        pm_count+=1
    # Completed non-PM records are pending a specific MST confirmation.
    completed=set()
    for r in accepted:
        if r["tech_name"] not in selected:
            continue
        if (r["status"].strip().casefold()=="completed" and
            r["wo_number"] not in pm_by_wo and r["wo_number"] not in by_wo):
            completed.add(r["wo_number"])
    for wo in sorted(completed):
        pending.append({"wo_number":wo,"reason":
                        "Completed but not Closed: add closure reminder or ignore"})
    summary.update({"added":added,"updated":updated,"preserved_edits":preserved,
                    "pm_wos_this_import":pm_count,"selected_technicians":len(selected),
                    "pending_review":len(pending),"ignored":len(ignored),
                    "staged_rows":len(all_rows),
                    "roster_matched":len(roster_shifts or {})})
    conn.execute("""INSERT INTO report_techs(shift_id,selected_json) VALUES(?,?)
          ON CONFLICT(shift_id) DO UPDATE SET selected_json=excluded.selected_json""",
          (shift_id,_json(sorted(selected))))
    conn.execute("""INSERT INTO report_audit(shift_id,summary_json,pending_json,ignored_json,updated_at)
         VALUES(?,?,?,?,?) ON CONFLICT(shift_id) DO UPDATE SET
         summary_json=excluded.summary_json,pending_json=excluded.pending_json,
         ignored_json=excluded.ignored_json,updated_at=excluded.updated_at""",
         (shift_id,_json(summary),_json(pending),_json(ignored),now))
    return summary


def update_wo(conn,shift_id,wo_number,data:dict):
    _table(conn)
    row=conn.execute("SELECT * FROM report_wo WHERE shift_id=? AND wo_number=?",
                     (shift_id,wo_number)).fetchone()
    if not row:raise KeyError("Work order not found in this shift")
    permitted={"asset","category","cause","tech","remarks","status","excluded"}
    fields={k:v for k,v in data.items() if k in permitted}
    if not fields:raise ValueError("No supported work-order changes")
    for k,v in fields.items():
        if k=="excluded":
            if not isinstance(v,bool):raise ValueError("Excluded must be true or false")
            fields[k]=int(v)
        elif not isinstance(v,str) or len(v)>5000 or (k=="status" and v not in ("RTS","OOS")):
            raise ValueError("Invalid value for "+k)
        else:fields[k]=v.strip()
    setters=", ".join(k+"=?" for k in fields)
    conn.execute(f"UPDATE report_wo SET {setters},edited=1 WHERE id=?",
                 (*fields.values(),row["id"]))
    return True


def get_state(conn,shift_id):
    _table(conn)
    rows=[dict(x) for x in conn.execute(
        "SELECT * FROM report_wo WHERE shift_id=? ORDER BY id",(shift_id,))]
    for r in rows:r["excluded"]=bool(r["excluded"])
    pm=[dict(x) for x in conn.execute(
        "SELECT * FROM report_pm WHERE shift_id=? ORDER BY wo_number",(shift_id,))]
    tech=conn.execute("SELECT selected_json FROM report_techs WHERE shift_id=?",(shift_id,)).fetchone()
    audit=conn.execute("SELECT * FROM report_audit WHERE shift_id=?",(shift_id,)).fetchone()
    stage_data=stage_details(conn,shift_id)
    return {"rows":rows,"pms":pm,"pm_count":len(pm),
            "technicians":json.loads(tech["selected_json"]) if tech else [],
            "stage":stage_data,
            "audit":{"summary":json.loads(audit["summary_json"]) if audit else {},
                     "pending":json.loads(audit["pending_json"]) if audit else [],
                     "ignored":json.loads(audit["ignored_json"]) if audit else []}}


def save_draft(conn,shift_id,data):
    _table(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS report_drafts(
       shift_id INTEGER PRIMARY KEY, body_json TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    payload={k:data.get(k) for k in ("vendors","ro","notes","loto","missingKey")}
    conn.execute("""INSERT INTO report_drafts(shift_id,body_json,updated_at)
      VALUES(?,?,?) ON CONFLICT(shift_id) DO UPDATE SET
      body_json=excluded.body_json,updated_at=excluded.updated_at""",
      (shift_id,_json(payload),_now()))


def get_draft(conn,shift_id):
    conn.execute("""CREATE TABLE IF NOT EXISTS report_drafts(
       shift_id INTEGER PRIMARY KEY,body_json TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    row=conn.execute("SELECT body_json FROM report_drafts WHERE shift_id=?",(shift_id,)).fetchone()
    return json.loads(row["body_json"]) if row else {}
