"""Persistent, shift-bound Cetaris import for IND3 V3.1.

Reuses reviewed V3 parsers, grouping and actual-technician shift attribution.
No production V3 database or real roster is changed. Phase 2 adds an
optional, human-reviewed local-AI rewrite suggestion on top of the
Phase 1 deterministic spelling correction -- see ai_remark_rewriter.py.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from v31_cetaris import parse_excel, technician_counts, select_records, MAX_SIZE
from v31_split import assign, _person_key, parse_close
import ai_remark_rewriter
import html as _html
import spelling_fix
import v31_oos
import v31_signature
import v31_pm_needing_completion as v31_pm
import v31_breakdown
import v31_returned_to_service

TZ=ZoneInfo("America/Indiana/Indianapolis")


def _table(conn):
    # This module is called by several concurrent browser requests on first
    # page load. Only run SQLite DDL/migrations when an old schema actually
    # needs them; repeated executescript() commits and schema locks can race
    # an in-flight Cetaris upload and occasionally return HTTP 500.
    expected={
        "report_stage":{"applied_at","prior_techs_json"},
        "report_techs":{"selected_json"},
        "report_wo":{"ai_status","is_manual","selection_hidden","source_techs_json","work_at"},
        "report_pm":{"selection_hidden","source_techs_json"},
        "report_audit":{"next_day_json"},
        "carry_over_decisions":{"id"}
    }
    current={r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if all(table in current and columns.issubset({
        r["name"] for r in conn.execute(f"PRAGMA table_info({table})")
    }) for table,columns in expected.items()):
        return
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
    # Keep existing per-shift audits; add a dedicated next-calendar-day listing.
    columns={row["name"] for row in conn.execute("PRAGMA table_info(report_audit)")}
    if "next_day_json" not in columns:
        conn.execute("ALTER TABLE report_audit ADD COLUMN next_day_json TEXT NOT NULL DEFAULT '[]'")
    stage_columns={row["name"] for row in conn.execute("PRAGMA table_info(report_stage)")}
    if "applied_at" not in stage_columns:
        conn.execute("ALTER TABLE report_stage ADD COLUMN applied_at TEXT DEFAULT NULL")
        # Older V3.1 records already imported by technician selection keep their status.
        conn.execute("""UPDATE report_stage SET applied_at=uploaded_at
            WHERE EXISTS(SELECT 1 FROM report_techs t WHERE t.shift_id=report_stage.shift_id)""")
    if "prior_techs_json" not in stage_columns:
        conn.execute("ALTER TABLE report_stage ADD COLUMN prior_techs_json TEXT NOT NULL DEFAULT '[]'")
    # Phase 2: AI rewrite review state. ai_status is one of 'none' | 'suggested'
    # | 'accepted' | 'rejected'. Accepting a suggestion reuses the existing
    # `edited` flag (see accept_ai_rewrite()) so the reimport-preservation
    # guard in import_selection() protects it for free -- no new guard needed.
    wo_columns={row["name"] for row in conn.execute("PRAGMA table_info(report_wo)")}
    if "ai_suggestion" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN ai_suggestion TEXT NOT NULL DEFAULT ''")
    if "ai_status" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN ai_status TEXT NOT NULL DEFAULT 'none'")
    if "is_manual" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN is_manual INTEGER NOT NULL DEFAULT 0")
    if "reference_wo" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN reference_wo TEXT NOT NULL DEFAULT ''")
    if "carry_over" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN carry_over INTEGER NOT NULL DEFAULT 0")
    if "source_shift_id" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN source_shift_id INTEGER")
    if "manual_oos_id" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN manual_oos_id INTEGER")
    conn.execute("""CREATE TABLE IF NOT EXISTS carry_over_decisions(
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       source_row_id INTEGER NOT NULL,
       source_shift_id INTEGER NOT NULL,
       target_shift_id INTEGER NOT NULL,
       action TEXT NOT NULL,
       actor TEXT NOT NULL,
       acted_at TEXT NOT NULL)""")
    if "selection_hidden" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN selection_hidden INTEGER NOT NULL DEFAULT 0")
    if "source_techs_json" not in wo_columns:
        conn.execute("ALTER TABLE report_wo ADD COLUMN source_techs_json TEXT NOT NULL DEFAULT '[]'")
    pm_columns={r["name"] for r in conn.execute("PRAGMA table_info(report_pm)")}
    if "selection_hidden" not in pm_columns:
        conn.execute("ALTER TABLE report_pm ADD COLUMN selection_hidden INTEGER NOT NULL DEFAULT 0")
    if "source_techs_json" not in pm_columns:
        conn.execute("ALTER TABLE report_pm ADD COLUMN source_techs_json TEXT NOT NULL DEFAULT '[]'")
    if "work_at" not in wo_columns:
        # The actual work-evidence timestamp (latest of last_complete_at /
        # in_service_at / start_at) this WO was attributed on -- powers the
        # S3/S5 evening-vs-morning completeness guide in the Import Audit.
        # Not the same as imported_at (when the MST ran the import).
        conn.execute("ALTER TABLE report_wo ADD COLUMN work_at TEXT NOT NULL DEFAULT ''")
    # Migration only adds tables; original V2/V3 archive and new V3.1 shifts stay intact.


def _json(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":"))


def _now():
    return datetime.now(TZ).isoformat()


def stage(conn, shift_id, payload: bytes, filename: str, actor: str):
    _table(conn)
    stats={}
    rows=parse_excel(payload,stats)
    technicians=technician_counts(rows)
    # Keep a tiny technician index alongside the full imported rows.
    # Loading a month's raw JSON on every report refresh was unnecessary.
    stats["technicians"]=technicians
    filename=Path(filename.replace("\\","/")).name[:120] or "cetaris.xlsx"
    previous=conn.execute("SELECT stats_json,applied_at FROM report_stage WHERE shift_id=?",
                          (shift_id,)).fetchone()
    prior_names=[]
    if previous and previous["applied_at"]:
        old_stats=json.loads(previous["stats_json"])
        cached=old_stats.get("technicians")
        if isinstance(cached,list):
            prior_names=[p["name"] for p in cached]
        else:
            # Older staged reports have no technician index; migrate lazily.
            prior_blob=conn.execute(
                "SELECT raw_json FROM report_stage WHERE shift_id=?",(shift_id,)).fetchone()
            if prior_blob:
                prior_names=[p["name"] for p in technician_counts(json.loads(prior_blob["raw_json"]))]
    conn.execute("""INSERT INTO report_stage
        (shift_id,filename,raw_json,stats_json,uploaded_by,uploaded_at,applied_at,prior_techs_json)
        VALUES(?,?,?,?,?,?,NULL,?) ON CONFLICT(shift_id) DO UPDATE SET
        filename=excluded.filename,raw_json=excluded.raw_json,stats_json=excluded.stats_json,
        uploaded_by=excluded.uploaded_by,uploaded_at=excluded.uploaded_at,applied_at=NULL,
        prior_techs_json=excluded.prior_techs_json""",
        (shift_id,filename,_json(rows),_json(stats),actor,_now(),_json(prior_names)))
    return {"filename":filename,"technicians":technicians,
            "stats":stats,"staged":True,"requires_selection":True}


def stage_details(conn, shift_id):
    _table(conn)
    # Fetch metadata only: a full-month raw_json blob is often much larger
    # than the entire current shift's report and must not be loaded on refresh.
    stage_row=conn.execute("""SELECT filename,stats_json,applied_at,prior_techs_json
                              FROM report_stage WHERE shift_id=?""",(shift_id,)).fetchone()
    if not stage_row:
        return {"staged":False,"imported":False,"filename":"","technicians":[],"stats":{},
                "prior_stage_technicians":[]}
    stats=json.loads(stage_row["stats_json"])
    cached=stats.get("technicians")
    if isinstance(cached,list):
        technicians=cached
    else:
        # Backward compatibility with files staged before this optimization.
        old=conn.execute("SELECT raw_json FROM report_stage WHERE shift_id=?",
                         (shift_id,)).fetchone()
        technicians=technician_counts(json.loads(old["raw_json"])) if old else []
    return {"staged":True,"imported":bool(stage_row["applied_at"]),
            "filename":stage_row["filename"],
            "technicians":technicians,
            "prior_stage_technicians":json.loads(stage_row["prior_techs_json"] or "[]"),
            "stats":stats}


def _existing_elsewhere(conn,shift_id,wo,new_status):
    """Prevent double-credit, but allow a later RTS for a prior OOS WO."""
    existing=conn.execute("""SELECT shift_id,status FROM report_wo
                             WHERE shift_id<>? AND wo_number=? AND excluded=0
                             ORDER BY id DESC LIMIT 1""",(shift_id,wo)).fetchone()
    if existing:
        if existing["status"]=="OOS" and new_status=="RTS":
            return None  # Valid cross-shift OOS -> RTS progression.
        return existing["shift_id"]
    pm=conn.execute("""SELECT shift_id FROM report_pm
                       WHERE shift_id<>? AND wo_number=? LIMIT 1""",
                    (shift_id,wo)).fetchone()
    return pm["shift_id"] if pm else None


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


def _next_day_work_orders(rows, shift_day, selected, imported, pms, pending, ignored):
    """Surface next-calendar-day work-order evidence WITHOUT reassigning ownership.

    Work timestamps (labor Start / In Service / WO Last Complete) are
    separate from administrative Closed / Update timestamps; neither
    constitutes shift ownership by itself. Dedup by WO# across labor rows.
    """
    next_date=(date.fromisoformat(shift_day)+timedelta(days=1)).isoformat()
    work_fields=("start_at","in_service_at","last_complete_at")
    admin_fields=("opened_at","closed_at","updated_at")
    grouped={}
    for row in rows:
        activity=[(field,str(row.get(field) or "")) for field in work_fields
                  if str(row.get(field) or "")[:10]==next_date]
        administration=[(field,str(row.get(field) or "")) for field in admin_fields
                        if str(row.get(field) or "")[:10]==next_date]
        if not activity and not administration:
            continue
        wo=str(row.get("wo_number") or "").strip()
        if not wo:
            continue
        item=grouped.setdefault(wo,{
            "wo_number":wo,"asset_id":row.get("asset_id") or "",
            "date":next_date,"technicians":set(),"statuses":set(),
            "work_timestamps":set(),"admin_timestamps":set(),
            "selected_technician":False})
        if not item["asset_id"]:
            item["asset_id"]=row.get("asset_id") or ""
        tech=row.get("tech_name") or ""
        if tech:item["technicians"].add(tech)
        if tech in selected:item["selected_technician"]=True
        if row.get("status"):item["statuses"].add(str(row["status"]))
        item["work_timestamps"].update(value for _,value in activity)
        item["admin_timestamps"].update(value for _,value in administration)
    pending_numbers={str(x.get("wo_number") or "") for x in pending}
    ignored_numbers={str(x.get("wo_number") or "") for x in ignored}
    result=[]
    for wo,item in sorted(grouped.items()):
        is_imported=wo in imported
        is_pm=wo in pms
        if is_pm:disposition="PM counted in selected shift"
        elif is_imported:disposition="Included in selected shift"
        elif not item["selected_technician"]:disposition="Other technician / not selected"
        elif wo in pending_numbers:disposition="Needs shift/closure review"
        elif wo in ignored_numbers:disposition="Excluded from selected shift"
        else:disposition="Not attributed to selected shift"
        result.append({
            "wo_number":wo,"asset_id":item["asset_id"],
            "date":next_date,"technicians":sorted(item["technicians"]),
            "status":", ".join(sorted(item["statuses"])),
            "work_timestamps":sorted(item["work_timestamps"]),
            "admin_timestamps":sorted(item["admin_timestamps"]),
            "timestamp_type":"Work/completion recorded" if item["work_timestamps"]
                             else "WO opened / administrative update only",
            "selected_technician":item["selected_technician"],
            "disposition":disposition,
            "included_in_shift":is_imported or is_pm,
        })
    return result


def technician_suggestions(source_techs, roster_rows, shift_code):
    """Preselect every uniquely matched active Drax associate on this shift.

    Import preselection is based on the Drax-assigned shift, NEVER the
    associate who opened the report or a literal MST Job Title.
    Exact normalized name matching protects against accidental ownership;
    ambiguous, unmatched and inactive people remain unchecked.
    """
    by_name={}
    by_name_any={}
    for person in roster_rows:
        key=_person_key(person.get('name'))
        if not key:
            continue
        by_name_any.setdefault(key,[]).append(person)
        if person.get('active'):
            by_name.setdefault(key,[]).append(person)
    result=[]
    for tech in source_techs:
        name=tech['name']
        key=_person_key(name)
        matches=by_name.get(key,[])
        match=matches[0] if len(matches)==1 else None
        assigned=match.get('shift') if match else None
        group=('assigned' if assigned==shift_code else
               'other_shift' if assigned else 'unmatched')
        reason=None
        if group=='unmatched':
            any_matches=by_name_any.get(key,[])
            if not any_matches:
                reason='No Drax associate with this name was found.'
            elif len(matches)>1:
                reason='Multiple active Drax records share this name; confirm manually.'
            elif not any(p.get('active') for p in any_matches):
                reason='Found in Drax, but the record is inactive.'
            elif match and not assigned:
                reason='Active Drax record has no assigned shift.'
            else:
                reason='Drax record found but could not be confirmed automatically.'
        result.append({'name':name,'count':tech['count'],
                       'group':group,'drax_shift':assigned,
                       'preselected':group=='assigned',
                       'match_reason':reason})
    return result

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
    # Latest available work-evidence timestamp per WO -- not the attribution
    # decision itself (that's assign()'s job), just a representative moment
    # for the evening/morning completeness guide in the Import Audit.
    wo_work_at={}
    for r in accepted:
        wo=str(r.get("wo_number") or "")
        candidates=[parse_close(r.get(f)) for f in
                    ("last_complete_at","in_service_at","start_at")]
        moment=max((c for c in candidates if c), default=None)
        if moment and (wo not in wo_work_at or moment>wo_work_at[wo]):
            wo_work_at[wo]=moment
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
            "techs":[],"remarks":[],"status":"OOS" if kind=="oos" else (
                "IN_PROCESS" if kind=="in_process" else
                "PENDING" if r.get("closure_reminder") else "RTS")})
        if r.get("tech_name") and r["tech_name"] not in item["techs"]:
            item["techs"].append(r["tech_name"])
        for remark in r.get("remarks_list") or ([r["remarks"]] if r.get("remarks") else []):
            if remark and remark not in item["remarks"]:
                item["remarks"].append(remark)
        if kind=="oos":item["status"]="OOS"
        elif kind=="in_process" and item["status"]!="OOS":item["status"]="IN_PROCESS"
    # PM takes precedence over any duplicate labor row from the same WO.
    for wo in pm_by_wo:by_wo.pop(wo,None)
    ignored=[]
    now=_now()
    added=updated=preserved=pm_count=0
    for wo,row in by_wo.items():
        owned=_existing_elsewhere(conn,shift_id,wo,row['status'])
        if owned is not None:
            ignored.append({"wo_number":wo,"reason":"Already recorded in V3.1 shift #"+str(owned)})
            continue
        existing=conn.execute("SELECT id,edited,status FROM report_wo WHERE shift_id=? AND wo_number=?",
                              (shift_id,wo)).fetchone()
        if existing and existing["edited"]:
            # Keep deliberate manual wording, but refresh the source
            # contributors so removing a technician can still hide the WO.
            # A later verified Cetaris Closed event must release a prior
            # PENDING closure even if remarks were edited manually. Preserve
            # human wording, category, exclusion and deliberate OOS holds.
            new_status=("RTS" if existing["status"]=="PENDING" and row["status"]=="RTS"
                        else existing["status"])
            conn.execute("UPDATE report_wo SET source_techs_json=?,status=? WHERE id=?",
                         (_json(row["techs"]),new_status,existing["id"]))
            preserved+=1
            continue
        fields=(row["asset"],row["category"],row["cause"],
                "; ".join(row["techs"]),"; ".join(row["remarks"]),row["status"])
        work_at=wo_work_at[wo].isoformat() if wo in wo_work_at else ""
        if existing:
            conn.execute("""UPDATE report_wo SET asset=?,category=?,cause=?,tech=?,
                remarks=?,status=?,imported_at=?,work_at=?,source_techs_json=?
                WHERE id=?""",
                (*fields,now,work_at,_json(row["techs"]),existing["id"]))
            updated+=1
        else:
            conn.execute("""INSERT INTO report_wo
               (shift_id,wo_number,asset,category,cause,tech,remarks,status,imported_at,work_at,source_techs_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(shift_id,wo,*fields,now,work_at,_json(row["techs"])))
            added+=1
    for wo,row in pm_by_wo.items():
        owned=_existing_elsewhere(conn,shift_id,wo,'RTS')
        if owned is not None:
            ignored.append({"wo_number":wo,"reason":"PM already recorded in shift #"+str(owned)})
            continue
        conn.execute("""INSERT INTO report_pm(shift_id,wo_number,asset,tech,completed_at,source_techs_json)
             VALUES(?,?,?,?,?,?) ON CONFLICT(shift_id,wo_number) DO UPDATE SET
             asset=excluded.asset,tech=excluded.tech,
             source_techs_json=excluded.source_techs_json""",
             (shift_id,wo,row["asset"],"; ".join(row["techs"]),now,_json(row["techs"])))
        pm_count+=1
    # Reconcile the CURRENT technician selection against cumulative records,
    # including older WOs missing from this replacement export. Never erase
    # original work history or override an MST's deliberate Exclude choice.
    # source_techs_json is provenance, not the report owner's name.
    selection_hidden=selection_restored=0
    for table in ("report_wo","report_pm"):
        for saved in conn.execute(
            f"SELECT rowid AS row_key,tech,selection_hidden,source_techs_json FROM {table} WHERE shift_id=? "
            f"{'AND is_manual=0' if table=='report_wo' else ''}",
            (shift_id,)).fetchall():
            try:
                contributors=set(json.loads(saved["source_techs_json"] or "[]"))
            except (ValueError,TypeError):
                contributors=set()
            if not contributors:
                contributors={x.strip() for x in saved["tech"].split(";") if x.strip()}
            hidden=int(bool(contributors) and not bool(contributors & selected))
            if hidden != saved["selection_hidden"]:
                conn.execute(f"UPDATE {table} SET selection_hidden=? WHERE rowid=?",
                             (hidden,saved["row_key"]))
                if hidden: selection_hidden+=1
                else: selection_restored+=1
    # V3-style breakdown lifecycle is separate from ordinary WO attribution.
    # It can update already-tracked WOs from later Cetaris exports without
    # crediting an unrelated shift for the original repair.
    breakdown_changes=v31_breakdown.sync(conn,all_rows,selected,accepted,shift_id)
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
    # Show next-day WOs even when the shift-attribution rules correctly
    # reject them. Overnight S3/S5 WOs may already be included.
    persisted_numbers={r["wo_number"] for r in conn.execute(
        "SELECT wo_number FROM report_wo WHERE shift_id=?",(shift_id,))}
    saved_pm_numbers={r["wo_number"] for r in conn.execute(
        "SELECT wo_number FROM report_pm WHERE shift_id=?",(shift_id,))}
    next_day=_next_day_work_orders(
        all_rows,shift_day,selected,persisted_numbers,saved_pm_numbers,pending,ignored)
    summary.update({"added":added,"updated":updated,"preserved_edits":preserved,
                    "breakdown_status_changes":breakdown_changes,
                    "selection_hidden":selection_hidden,"selection_restored":selection_restored,
                    "pm_wos_this_import":pm_count,"selected_technicians":len(selected),
                    "pending_review":len(pending),"ignored":len(ignored),
                    "staged_rows":len(all_rows),
                    "roster_matched":len(roster_shifts or {}),
                    "next_day_wos":len(next_day)})
    conn.execute("""INSERT INTO report_techs(shift_id,selected_json) VALUES(?,?)
          ON CONFLICT(shift_id) DO UPDATE SET selected_json=excluded.selected_json""",
          (shift_id,_json(sorted(selected))))
    conn.execute("""INSERT INTO report_audit
         (shift_id,summary_json,pending_json,ignored_json,next_day_json,updated_at)
         VALUES(?,?,?,?,?,?) ON CONFLICT(shift_id) DO UPDATE SET
         summary_json=excluded.summary_json,pending_json=excluded.pending_json,
         ignored_json=excluded.ignored_json,next_day_json=excluded.next_day_json,
         updated_at=excluded.updated_at""",
         (shift_id,_json(summary),_json(pending),_json(ignored),_json(next_day),now))
    conn.execute("UPDATE report_stage SET applied_at=? WHERE shift_id=?",(_now(),shift_id))
    return summary


def update_wo(conn,shift_id,wo_number,data:dict):
    _table(conn)
    row=conn.execute("SELECT * FROM report_wo WHERE shift_id=? AND wo_number=?",
                     (shift_id,wo_number)).fetchone()
    if not row:raise KeyError("Work order not found in this shift")
    permitted={"asset","category","cause","tech","remarks","status","excluded"}
    fields={k:v for k,v in data.items() if k in permitted}
    if not fields:raise ValueError("No supported work-order changes")
    if row["status"]=="PENDING" and not row["is_manual"] and "status" in fields and fields["status"]!="PENDING":
        raise ValueError("Pending Cetaris closure must be verified by a new Closed WO import; editing other fields is allowed")
    for k,v in fields.items():
        if k=="excluded":
            if not isinstance(v,bool):raise ValueError("Excluded must be true or false")
            fields[k]=int(v)
        elif not isinstance(v,str) or len(v)>5000 or (k=="status" and v not in ("RTS","OOS","PENDING")):
            raise ValueError("Invalid value for "+k)
        else:fields[k]=v.strip()
    setters=", ".join(k+"=?" for k in fields)
    conn.execute(f"UPDATE report_wo SET {setters},edited=1 WHERE id=?",
                 (*fields.values(),row["id"]))
    return True


def add_manual_happening(conn,shift_id,*,asset,cause,remarks,category,status,technician,
                         reference_wo='',source_shift_id=None,manual_oos_id=None):
    """Record work discovered outside Cetaris, including proactive/PM findings.

    Generated MANUAL identifiers are local record keys, not claimed Cetaris
    work-order numbers. A real WO is only referenced when supplied.
    """
    import secrets
    _table(conn)
    if not asset.strip() or not cause.strip():
        raise ValueError('Asset and description are required')
    if category not in ('Breakdown','Process Interruption','Proactive Repair','Work Request'):
        raise ValueError('Invalid manual work category')
    if status not in ('RTS','OOS','PENDING'):
        raise ValueError('Invalid manual equipment status')
    wo='MANUAL-'+secrets.token_hex(6).upper()
    now=_now()
    conn.execute("""INSERT INTO report_wo
      (shift_id,wo_number,asset,category,cause,tech,remarks,status,imported_at,
       work_at,source_techs_json,is_manual,reference_wo,source_shift_id,manual_oos_id)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (shift_id,wo,asset.strip()[:120],category,cause.strip()[:4000],
       technician.strip()[:180],remarks.strip()[:5000],status,now,'','[]',
       1,reference_wo.strip()[:100],source_shift_id,manual_oos_id))
    return {'wo_number':wo,'reference_wo':reference_wo,'asset':asset,
            'status':status,'category':category,'is_manual':True}


def flag_carry_over(conn,shift_id,wo_number,enabled):
    _table(conn)
    row=conn.execute("SELECT id FROM report_wo WHERE shift_id=? AND wo_number=?",
                     (shift_id,wo_number)).fetchone()
    if not row:raise KeyError('Work order not found in selected shift')
    conn.execute('UPDATE report_wo SET carry_over=? WHERE id=?',(int(bool(enabled)),row['id']))
    return {'wo_number':wo_number,'carry_over':bool(enabled)}


def available_carry_overs(conn,target_shift_id,target_day):
    _table(conn)
    rows=conn.execute("""SELECT r.id,r.shift_id,r.wo_number,r.asset,r.cause,r.remarks,r.status,
        r.reference_wo,s.code,s.shift_day
        FROM report_wo r JOIN shifts s ON s.id=r.shift_id
        WHERE r.carry_over=1 AND r.excluded=0 AND r.selection_hidden=0
        AND r.shift_id<>? AND s.open=0 AND s.shift_day<=?
        ORDER BY s.shift_day DESC,r.id DESC LIMIT 75""",
        (target_shift_id,target_day)).fetchall()
    return [dict(row) for row in rows]


def resolve_carry_over(conn,target_shift_id,target_day,source_row_id,action,actor):
    _table(conn)
    if action not in ('adopt','dismiss'):
        raise ValueError('Invalid carry-over decision')
    found={r['id']:r for r in available_carry_overs(conn,target_shift_id,target_day)}
    row=found.get(source_row_id)
    if not row:raise KeyError('Carry-over no longer available')
    updated=conn.execute("UPDATE report_wo SET carry_over=0 WHERE id=? AND carry_over=1",
                         (source_row_id,))
    if updated.rowcount!=1:raise KeyError('Carry-over was already handled')
    adopted=None
    if action=='adopt':
        # Adoption makes an explicit follow-up, NOT another completed WO
        # and never automatically counts an old RTS as a new repair.
        notes=('Carry-over from '+row['code']+' '+row['shift_day']+
               ' · source WO '+(row['reference_wo'] or row['wo_number']))
        if row['remarks']: notes+=' · '+row['remarks']
        adopted=add_manual_happening(conn,target_shift_id,asset=row['asset'],
               cause='Carry-over follow-up: '+row['cause'],remarks=notes,
               category='Work Request',status='PENDING',technician=actor,
               reference_wo=row['reference_wo'] or row['wo_number'],
               source_shift_id=row['shift_id'])
    conn.execute("""INSERT INTO carry_over_decisions(
       source_row_id,source_shift_id,target_shift_id,action,actor,acted_at)
       VALUES(?,?,?,?,?,?)""",
       (source_row_id,row['shift_id'],target_shift_id,action,actor,_now()))
    return {'action':action,'source_row_id':source_row_id,'adopted':adopted}


def request_ai_rewrite(conn,shift_id,wo_number):
    """Ask the local AI engine for a professional rewrite of a WO's
    CURRENT, already spelling-corrected remark. Never mutates `remarks` --
    only stores the suggestion + review status for the MST to inspect via
    the WO edit modal (see accept_ai_rewrite/reject_ai_rewrite). A failed
    or unavailable AI engine is not an error here: the WO's ai_status is
    set to 'none' again and the caller gets a clear reason, so ordinary
    spelling correction and manual editing continue uninterrupted.
    """
    _table(conn)
    row=conn.execute("SELECT * FROM report_wo WHERE shift_id=? AND wo_number=?",
                     (shift_id,wo_number)).fetchone()
    if not row:raise KeyError("Work order not found in this shift")
    corrected_remark=spelling_fix.correct_spelling(row["remarks"])
    result=ai_remark_rewriter.suggest_rewrite(corrected_remark,asset_id=row["asset"],wo_number=wo_number)
    if result["ok"]:
        conn.execute("UPDATE report_wo SET ai_suggestion=?,ai_status='suggested' WHERE id=?",
                     (result["text"],row["id"]))
    else:
        conn.execute("UPDATE report_wo SET ai_status='none' WHERE id=?",(row["id"],))
    return result


def reject_ai_rewrite(conn,shift_id,wo_number):
    """MST declined the suggestion. The current `remarks` value (spelling-
    corrected or previously manually-edited text) is left completely
    untouched -- only the review status changes."""
    _table(conn)
    row=conn.execute("SELECT id FROM report_wo WHERE shift_id=? AND wo_number=?",
                     (shift_id,wo_number)).fetchone()
    if not row:raise KeyError("Work order not found in this shift")
    conn.execute("UPDATE report_wo SET ai_status='rejected' WHERE id=?",(row["id"],))
    return True


def accept_ai_rewrite(conn,shift_id,wo_number,final_text):
    """MST accepted the suggestion, verbatim or after further edits --
    `final_text` is whatever the MST actually approved, which the modal
    always lets them inspect and modify before calling this. Reuses the
    existing update_wo()/`edited` flag so this final text gets the exact
    same reimport-survives-edits protection manual edits already have, and
    flows through get_state()/createReport()/the EML identically.
    """
    text=(final_text or "").strip()
    if not text:raise ValueError("Accepted rewrite text cannot be empty")
    update_wo(conn,shift_id,wo_number,{"remarks":text})
    row=conn.execute("SELECT id FROM report_wo WHERE shift_id=? AND wo_number=?",
                     (shift_id,wo_number)).fetchone()
    conn.execute("UPDATE report_wo SET ai_status='accepted' WHERE id=?",(row["id"],))
    return True


def evening_morning_counts(rows, shift_code, shift_day):
    """S3/S5 completeness guide, confirmed with Isaac 2026-11-03: a quick
    sanity count so an MST can eyeball 'yes, all my WOs got pulled in' --
    e.g. '10 WO before midnight, 9 WO after' for a Friday S5. Not applicable
    to S1/S2/S4, which don't cross midnight.
    """
    if shift_code not in ("S3", "S5"):
        return {"applicable": False}
    if isinstance(shift_day, str):
        shift_day = date.fromisoformat(shift_day)
    evening = morning = 0
    for r in rows:
        if r.get("excluded") or not r.get("work_at"):
            continue
        moment = parse_close(r["work_at"])
        if not moment:
            continue
        if moment.date() == shift_day:
            evening += 1
        else:
            morning += 1
    return {"applicable": True, "evening": evening, "morning": morning}


def get_state(conn,shift_id,shift_code=None,shift_day=None):
    _table(conn)
    rows=[dict(x) for x in conn.execute(
        "SELECT * FROM report_wo WHERE shift_id=? ORDER BY id",(shift_id,))]
    for r in rows:
        r["manual_excluded"]=bool(r["excluded"])
        r["selection_hidden"]=bool(r["selection_hidden"])
        r["excluded"]=r["manual_excluded"] or r["selection_hidden"]
        # Display/report-only spelling cleanup (V3 spelling_fix, ported
        # unchanged). The DB row above is never modified by this -- the
        # Cetaris-original/MST-edited text stays the row of record, and
        # reimport/edit-preservation logic in import_selection()/update_wo()
        # is untouched. *_original lets a future UI offer a revert/compare
        # view without any schema change.
        r["cause_original"]=r["cause"]
        r["remarks_original"]=r["remarks"]
        r["cause"]=spelling_fix.correct_spelling(r["cause"])
        r["remarks"]=spelling_fix.correct_spelling(r["remarks"])
    pm=[dict(x) for x in conn.execute(
        "SELECT * FROM report_pm WHERE shift_id=? AND selection_hidden=0 ORDER BY wo_number",
        (shift_id,))]
    tech=conn.execute("SELECT selected_json FROM report_techs WHERE shift_id=?",(shift_id,)).fetchone()
    audit=conn.execute("SELECT * FROM report_audit WHERE shift_id=?",(shift_id,)).fetchone()
    stage_data=stage_details(conn,shift_id)
    evening_morning=(evening_morning_counts(rows,shift_code,shift_day)
                     if shift_code and shift_day else {"applicable":False})
    return {"rows":rows,"pms":pm,"pm_count":len(pm),
            "pm_needing_completion":v31_pm.get(conn,shift_id),
            "technicians":json.loads(tech["selected_json"]) if tech else [],
            "stage":stage_data,
            "audit":{"summary":json.loads(audit["summary_json"]) if audit else {},
                     "evening_morning":evening_morning,
                     "pending":json.loads(audit["pending_json"]) if audit else [],
                     "ignored":json.loads(audit["ignored_json"]) if audit else [],
                     "next_day":json.loads(audit["next_day_json"]) if audit else []}}


def _ensure_missing_keys(conn):
    """One persistent key inventory shared by S1-S5, not tied to report drafts.

    On first upgrade, preserve missing keys already recorded in old shift
    drafts rather than silently dropping a safety-related warning.
    """
    exists=conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='facility_missing_keys'"
    ).fetchone()
    if exists:
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS facility_missing_keys(
        key_name TEXT PRIMARY KEY,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS facility_missing_key_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_name TEXT NOT NULL,
        missing INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    drafts=conn.execute(
        "SELECT body_json FROM report_drafts"
    ).fetchall() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_drafts'"
    ).fetchone() else []
    found=set()
    for row in drafts:
        try:
            body=json.loads(row["body_json"])
        except (ValueError,TypeError):
            continue
        found.update(k for k in (body.get("missingKeys") or [])
                     if isinstance(k,str) and k in {f"Key {i}" for i in range(1,21)})
    for key in sorted(found,key=lambda k:int(k.split()[1])):
        conn.execute("""INSERT OR IGNORE INTO facility_missing_keys
            (key_name,updated_at,updated_by) VALUES (?,?,?)""",
            (key,_now(),"previous-shift-migration"))


def get_missing_keys(conn):
    _ensure_missing_keys(conn)
    rows=conn.execute("SELECT key_name FROM facility_missing_keys").fetchall()
    return sorted((r["key_name"] for r in rows), key=lambda x:int(x.split()[1]))


def set_missing_key(conn,shift_id,key,missing,actor):
    """Update exactly one facility key; other shifts cannot erase it by autosave."""
    if key not in {f"Key {n}" for n in range(1,21)}:
        raise ValueError("Select one of the 20 LOTO keys")
    _ensure_missing_keys(conn)
    old=conn.execute(
        "SELECT 1 FROM facility_missing_keys WHERE key_name=?",(key,)
    ).fetchone() is not None
    changed=old != missing
    if changed:
        if missing:
            conn.execute("""INSERT INTO facility_missing_keys
                (key_name,updated_at,updated_by) VALUES (?,?,?)""",
                (key,_now(),actor))
        else:
            conn.execute("DELETE FROM facility_missing_keys WHERE key_name=?",(key,))
        conn.execute("""INSERT INTO facility_missing_key_events
            (key_name,missing,shift_id,updated_by,updated_at) VALUES (?,?,?,?,?)""",
            (key,int(missing),shift_id,actor,_now()))
        # A changed safety-critical facility inventory invalidates stale
        # EML snapshots in every *open* shift; archived reports stay frozen.
        conn.execute("""UPDATE shifts SET report_saved_at='',report_content='',
            report_html='',report_hash='',report_eml=X'' WHERE open=1""")
    return {"missingKeys":get_missing_keys(conn),"changed":changed}


def save_draft(conn,shift_id,data):
    _table(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS report_drafts(
       shift_id INTEGER PRIMARY KEY, body_json TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    payload={k:data.get(k) for k in ("vendors","ro","notes","loto")}
    # The deprecated client missingKeys array is intentionally ignored:
    # stale drafts must never clear facility-wide missing keys.
    payload["missingKeys"]=get_missing_keys(conn)
    existing=conn.execute("SELECT body_json FROM report_drafts WHERE shift_id=?",
                          (shift_id,)).fetchone()
    if existing and json.loads(existing["body_json"])==payload:
        return False
    conn.execute("""INSERT INTO report_drafts(shift_id,body_json,updated_at)
      VALUES(?,?,?) ON CONFLICT(shift_id) DO UPDATE SET
      body_json=excluded.body_json,updated_at=excluded.updated_at""",
      (shift_id,_json(payload),_now()))
    return True


def get_draft(conn,shift_id):
    conn.execute("""CREATE TABLE IF NOT EXISTS report_drafts(
       shift_id INTEGER PRIMARY KEY,body_json TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    row=conn.execute("SELECT body_json FROM report_drafts WHERE shift_id=?",
                     (shift_id,)).fetchone()
    data=json.loads(row["body_json"]) if row else {}
    # Open shifts always use the live facility inventory. Closed reports use
    # the saved per-shift snapshot to avoid rewriting historical key status.
    shift=conn.execute("SELECT open FROM shifts WHERE id=?",(shift_id,)).fetchone()
    if shift and shift["open"]:
        data["missingKeys"]=get_missing_keys(conn)
    return data

def happenings_counts(conn, shift_ids):
    """Non-excluded Shift Happenings (work order) count per shift_id, for
    Shift History's summary table. {} for an empty shift_ids list."""
    _table(conn)
    if not shift_ids:
        return {}
    placeholders = ",".join("?" * len(shift_ids))
    rows = conn.execute(
        f"SELECT shift_id, COUNT(*) AS n FROM report_wo "
        f"WHERE excluded=0 AND selection_hidden=0 AND shift_id IN ({placeholders}) GROUP BY shift_id",
        shift_ids)
    return {r["shift_id"]: r["n"] for r in rows}


def build_passdown_html(conn, shift_id, shift_code, lead_name=''):
    """Python mirror of the client-side buildEmailHtml() in shift-report.html,
    used only so Shift History can regenerate a historical shift's EML after
    the browser tab holding its live in-memory `state` is long gone. Rebuilt
    from what actually persists: report_wo/report_pm (via get_state, same
    spelling-corrected text the MST saw) plus report_drafts (vendors/ro/
    notes/loto/missingKeys, via the existing autosave debounce). Section
    wording/order must stay in lockstep with buildEmailHtml() -- update both
    together if either changes.

    lead_name appends the real IND3 Next Gen Outlook signature (matching
    the live V3 workshop app's exact format) -- blank renders a generic
    'IND3 Maintenance Team' signature rather than omitting one."""
    state = get_state(conn, shift_id)
    draft = get_draft(conn, shift_id)
    esc = _html.escape
    inc = [r for r in state["rows"] if not r["excluded"]]

    def sec(title, body_html):
        return (f'<p style="margin:16px 0 4px;font-family:Calibri,Arial,sans-serif;">'
                f'<strong style="font-size:16px;">{title}</strong></p>{body_html}')

    def bullet_list(items):
        s = "font-size:14px;font-family:Calibri,Arial,sans-serif;margin:4px 0;"
        if not items:
            return (f'<ul style="margin:0 0 4px;padding-left:20px;line-height:1.8;">'
                     f'<li style="{s}color:#555;">None</li></ul>')
        lis = "".join(f'<li style="{s}">{i}</li>' for i in items)
        return f'<ul style="margin:0 0 4px;padding-left:20px;line-height:1.8;">{lis}</ul>'

    happenings = []
    notes = (draft.get("notes") or "").strip()
    if notes:
        happenings.append(f'<p style="margin:6px 0;font-size:14px;">{esc(notes)}</p>')
    pm_count = state["pm_count"]
    if pm_count:
        happenings.append(
            f'<p style="margin:6px 0;font-size:14px;"><strong>{pm_count} PM{"s" if pm_count!=1 else ""} '
            f'completed this shift</strong></p>')
    for r in inc:
        heading = (f'{esc(r["asset"] or "Unspecified asset")} : {esc(r["cause"] or "No service description")} '
                   f'(WO# {esc(r["wo_number"])})')
        remark = esc(r["remarks"]) if r["remarks"] else "No technician remarks"
        happenings.append(
            f'<div style="margin:8px 0 10px;"><strong style="font-size:14px;">{heading}</strong>'
            f'<ul style="margin:2px 0 0;padding-left:22px;">'
            f'<li style="font-size:13px;white-space:pre-wrap;">{remark}</li></ul></div>')

    # Returned to Service means confirmed breakdown recovery OR explicit
    # manual RTS. Routine completed/closed Shift Happenings never qualify.
    rts=[f'<strong>{esc(item["asset"])}</strong>' for item in
         v31_returned_to_service.confirmed_returns(conn,shift_id)]
    # Use the same facility-wide active OOS inventory as the dashboard.
    # Historical unresolved breakdowns may not appear in this shift's
    # report_wo rows, and previously resolved equipment must not reappear.
    # Local import avoids a module-level cycle (asset_status imports reporting).
    import v31_asset_status
    oos = [f'<strong>{esc(item["asset"])}</strong>'
           for item in v31_asset_status.summary(conn, shift_id)["oos"]]
    loto_html = (
        '<p style="font-size:14px;font-family:Calibri,Arial,sans-serif;margin-top:18px;">'
        '<strong style="color:#2a8703;">&#10003; LOTO Verified</strong></p>'
        if draft.get("loto") else
        '<p style="font-size:14px;font-family:Calibri,Arial,sans-serif;margin-top:18px;">'
        '<strong style="color:#b45309;">&#9888; LOTO Verification Pending</strong></p>')
    missing_keys = draft.get("missingKeys") or []
    missing_html = ""
    if missing_keys:
        label = "Missing key" + ("s" if len(missing_keys) > 1 else "")
        missing_html = (f'<p style="color:#c00;margin:2px 0;font-size:14px;font-family:Calibri,Arial,sans-serif;">'
                        f'<strong>{label}:</strong> {esc(", ".join(missing_keys))}</p>')
    # The separate V2-style PM report is NOT the regular Completed PM
    # work-order count and must not be inferred from In Process Cetaris WOs.
    # Explicitly hand unfinished repairs and flagged carry-overs to the next
    # shift. Do not imply an unresolved WO or in-process breakdown is RTS.
    followups=[]
    for record in inc:
        status=record["status"]
        carry=bool(record.get("carry_over"))
        if status!="PENDING" and not carry:
            continue
        context=[]
        if status=="PENDING":context.append("Pending completion / closure")
        if carry:context.append("Flagged for shift handover")
        ref=record.get("reference_wo") or record["wo_number"]
        followups.append(f'<strong>{esc(record["asset"] or "Asset unspecified")}</strong> '
                         f'(WO# {esc(ref)}) — {esc(record["cause"] or "Follow-up required")}: '
                         f'{esc("; ".join(context))}')
    pm_html = v31_pm.report_section(conn,shift_id)
    vendors = [esc(v) for v in (draft.get("vendors") or [])]
    ro = [esc(v) for v in (draft.get("ro") or [])]
    html_body = (
        '<html><body style="font-family:Calibri,Arial,sans-serif;font-size:14px;color:#111;'
        'max-width:700px;line-height:1.6;">'
        f'<p style="margin:0 0 18px;font-size:18px;"><strong>{esc(shift_code)} End of Shift Passdown</strong></p>'
        + sec("Vendors", bullet_list(vendors))
        + sec("Shift Happenings", "".join(happenings) if happenings else "<p>None</p>")
        + sec("Open Follow-ups / Shift Carry-overs", bullet_list(followups))
        + sec("Reliable Operations", bullet_list(ro))
        + pm_html
        + sec("Returned to Service", bullet_list(rts))
        + sec("Current Out of Service", bullet_list(oos))
        + loto_html + missing_html + '</body></html>')
    return v31_signature.append_signature(html_body, lead_name)
