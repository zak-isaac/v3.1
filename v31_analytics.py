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
    clauses=["shift_day<=?"]
    args=[end.isoformat()]
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
                      "breakdowns":0,"oos":0,"shifts":0,
                      "proactive_repairs":0,"pending_closure":0,
                      "saved_passdowns":0,"closed_without_passdown":0}
               for shift in SHIFT_ORDER}
    totals["proactive_repairs"]=0
    categories=Counter()
    assets={}
    cause_groups={}
    dedup_breakdown_keys=set()
    dedup_all_wo_keys=set()
    missing_asset_entries=0
    sidinfo={s["id"]:s for s in shifts}
    for shift in shifts:
        totals["active"]+=int(bool(shift["open"]))
        totals["saved_passdowns"]+=int(bool(shift["report_saved_at"]))
        totals["closed_without_passdown"]+=int(
            not shift["open"] and not shift["report_saved_at"])
        shiftdata[shift["code"]]["shifts"]+=1
        shiftdata[shift["code"]]["saved_passdowns"]+=int(bool(shift["report_saved_at"]))
        shiftdata[shift["code"]]["closed_without_passdown"]+=int(
            not shift["open"] and not shift["report_saved_at"])
        daydata[shift["shift_day"]]
    if ids:
        parameters=",".join("?" for _ in ids)
        for row in conn.execute(
            f"SELECT shift_id,wo_number,asset,category,cause,status,is_manual,carry_over FROM report_wo "
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
            if row["category"]=="Proactive Repair":
                totals["proactive_repairs"]+=1
                shiftdata[parent["code"]]["proactive_repairs"]+=1
            if row["status"]=="PENDING":
                shiftdata[parent["code"]]["pending_closure"]+=1

            # A WO can legitimately appear on different shift reports (OOS
            # at handover, then RTS). Count it once per asset for reliability
            # rankings. Manual entries are unique shift-scoped observations.
            wo=str(row["wo_number"] or "").strip().casefold()
            wo_key=("manual",parent["id"],wo) if row["is_manual"] else ("cetaris",wo)
            dedup_all_wo_keys.add(wo_key)
            asset_raw=" ".join(str(row["asset"] or "").split())
            if not asset_raw:
                missing_asset_entries+=1
                continue
            asset_key=asset_raw.casefold()
            item=assets.setdefault(asset_key,{
                "asset":asset_raw,"wo_keys":set(),"breakdown_keys":set(),
                "proactive_keys":set(),"shift_codes":set(),
                "last_shift_day":parent["shift_day"]
            })
            item["wo_keys"].add(wo_key)
            item["shift_codes"].add(parent["code"])
            item["last_shift_day"]=max(item["last_shift_day"],parent["shift_day"])
            if row["category"]=="Proactive Repair":
                item["proactive_keys"].add(wo_key)
            if row["category"]=="Breakdown":
                item["breakdown_keys"].add(wo_key)
                if wo_key not in dedup_breakdown_keys:
                    dedup_breakdown_keys.add(wo_key)
                    description=" ".join(str(row["cause"] or "").split())
                    if description:
                        normalized=description.casefold()
                        cause=cause_groups.setdefault(normalized,{
                            "description":description,"wo_keys":set()
                        })
                        cause["wo_keys"].add(wo_key)
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
    top_assets=[{
        "asset":r["asset"],"work_orders":len(r["wo_keys"]),
        "breakdowns":len(r["breakdown_keys"]),
        "proactive_repairs":len(r["proactive_keys"]),
        "shifts":sorted(r["shift_codes"]),
        "last_shift_day":r["last_shift_day"]
    } for r in assets.values()]
    top_assets.sort(key=lambda r:(-r["work_orders"],-r["breakdowns"],r["asset"].casefold()))
    repeat_assets=[{
        **r,"repeat_breakdown_wos":r["breakdowns"]-1
    } for r in top_assets if r["breakdowns"]>=2]
    repeat_assets.sort(key=lambda r:(-r["breakdowns"],-r["work_orders"],r["asset"].casefold()))
    common_breakdowns=[{"description":x["description"],"work_orders":len(x["wo_keys"])}
                       for x in cause_groups.values()]
    common_breakdowns.sort(key=lambda x:(-x["work_orders"],x["description"].casefold()))
    totals["distinct_wo_ids"]=len(dedup_all_wo_keys)
    totals["assets_reported"]=len(assets)
    totals["repeat_breakdown_assets"]=len(repeat_assets)
    totals["missing_asset_entries"]=missing_asset_entries

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
            "top_assets":top_assets[:10],
            "repeat_breakdown_assets":repeat_assets[:10],
            "common_breakdown_descriptions":common_breakdowns[:8],
            "trend":trend[-90:],
            "notes":["Included WO totals are per shift report; one WO may appear in multiple shift reports.",
                     "Asset rankings count each distinct WO once per asset, including shift-scoped manual work.",
                     "Repeat breakdowns mean two or more distinct breakdown WOs for one asset, not two sightings of one WO.",
                     "Breakdown descriptions are technician text, not independently verified root causes.",
                     "RTS/OOS totals are recorded report statuses, not current equipment availability.",
                     "PMs Needing Completion comes only from separate uploaded PM reports.",
                     "No calculated downtime or MTTR without timestamped outage intervals."]}
