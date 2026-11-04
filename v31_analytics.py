"""Read-only, source-grounded V3.1 maintenance analytics.

Only database-backed counts; separate V2/V3 archived history is never
silently mixed with newer shifts. Shift ownership limits normal users.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from datetime import date,timedelta
from zoneinfo import ZoneInfo
from datetime import datetime
import json

import v31_reporting
import v31_pm_needing_completion as v31_pm

TZ=ZoneInfo("America/Indiana/Indianapolis")
SHIFT_ORDER=("S1","S2","S3","S4","S5")

def summarize(conn,owner=None,period="30d",today=None):
    v31_reporting._table(conn)
    v31_pm.ensure(conn)
    end=today or datetime.now(TZ).date()
    horizons={"7d":7,"30d":30,"90d":90,"all":None}
    days=horizons.get(period,30)
    clauses=[]
    args=[]
    if days is not None:
        clauses.append("shift_day>=?")
        args.append((end-timedelta(days=days-1)).isoformat())
    if owner is not None:
        clauses.append("owner=?")
        args.append(owner)
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    shifts=[dict(x) for x in conn.execute(
        "SELECT id,code,shift_day,owner,open,report_saved_at,closed_reason,report_count "
        "FROM shifts"+where+" ORDER BY shift_day DESC,id DESC",tuple(args))]
    ids=[s["id"] for s in shifts]
    totals={"shifts":len(shifts),"active":0,"saved_passdowns":0,
            "closed_without_passdown":0,"work_orders":0,"completed_pms":0,
            "pm_needing_completion":0,"breakdowns":0,"oos":0,
            "rts":0,"pending_closure":0,"manual_happenings":0,"pending_import_reviews":0}
    daydata=defaultdict(lambda:{"work_orders":0,"completed_pms":0,"breakdowns":0})
    shiftdata={shift:{"code":shift,"work_orders":0,"completed_pms":0,
                      "breakdowns":0,"oos":0,"shifts":0} for shift in SHIFT_ORDER}
    categories=Counter()
    sidinfo={s["id"]:s for s in shifts}
    for shift in shifts:
        totals["active"]+=int(bool(shift["open"]))
        totals["saved_passdowns"]+=int(bool(shift["report_saved_at"]))
        totals["closed_without_passdown"]+=int(
            not shift["open"] and not shift["report_saved_at"])
        shiftdata[shift["code"]]["shifts"]+=1
        daydata[shift["shift_day"]]
    if ids:
        parameters=",".join("?" for _ in ids)
        for row in conn.execute(
            f"SELECT shift_id,category,status,is_manual FROM report_wo "
            f"WHERE excluded=0 AND selection_hidden=0 AND shift_id IN ({parameters})",ids):
            parent=sidinfo[row["shift_id"]]
            totals["work_orders"]+=1
            shiftdata[parent["code"]]["work_orders"]+=1
            daydata[parent["shift_day"]]["work_orders"]+=1
            categories[row["category"] or "Unclassified"]+=1
            if row["category"]=="Breakdown":
                totals["breakdowns"]+=1
                shiftdata[parent["code"]]["breakdowns"]+=1
                daydata[parent["shift_day"]]["breakdowns"]+=1
            if row["status"]=="RTS":totals["rts"]+=1
            if row["status"]=="OOS":
                totals["oos"]+=1
                shiftdata[parent["code"]]["oos"]+=1
            if row["status"]=="PENDING":totals["pending_closure"]+=1
            if row["is_manual"]:totals["manual_happenings"]+=1
        for row in conn.execute(
            f"SELECT shift_id FROM report_pm WHERE selection_hidden=0 AND shift_id IN ({parameters})",ids):
            parent=sidinfo[row["shift_id"]]
            totals["completed_pms"]+=1
            shiftdata[parent["code"]]["completed_pms"]+=1
            daydata[parent["shift_day"]]["completed_pms"]+=1
        for row in conn.execute(
            f"SELECT shift_id,entries_json FROM pm_needing_completion WHERE shift_id IN ({parameters})",ids):
            try:totals["pm_needing_completion"]+=len(json.loads(row["entries_json"]))
            except (ValueError,TypeError):pass
        for row in conn.execute(
            f"SELECT shift_id,pending_json FROM report_audit WHERE shift_id IN ({parameters})",ids):
            try:totals["pending_import_reviews"]+=len(json.loads(row["pending_json"]))
            except (ValueError,TypeError):pass
    total=totals["shifts"]
    totals["report_coverage_pct"]=round(100*totals["saved_passdowns"]/total,1) if total else None
    totals["wo_per_shift"]=round(totals["work_orders"]/total,1) if total else None
    # Trend is by shift start date; counts are report records, not reliability
    # measurements (no downtime duration, MTTR, or 99.8% uptime invented).
    trend=[{"date":day,**values} for day,values in sorted(daydata.items())]
    return {"scope":"V3.1 database only — legacy V2/V3 archive excluded",
            "period":period,"from":(end-timedelta(days=days-1)).isoformat() if days else None,
            "through":end.isoformat(),"totals":totals,
            "by_shift":[shiftdata[shift] for shift in SHIFT_ORDER],
            "by_category":[{"name":name,"count":count} for name,count in categories.most_common(12)],
            "trend":trend[-30:],
            "notes":["Work-order totals exclude removed and selection-hidden entries.",
                     "RTS/OOS totals are report statuses, not time-based equipment availability.",
                     "PMs Needing Completion comes only from separate uploaded PM reports.",
                     "No calculated downtime or MTTR without timestamped outage intervals."]}
