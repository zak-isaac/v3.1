"""Extract calendar-due PM work orders from a Cetaris WO Details export.

Independent of labor-based Shift Happenings: To be Done/unscheduled PMs
often have NO Technician, so they must not be discarded by labor parsing.
Only explicit PM Due Date, Primary RFR=08, and WO Status=To Be Done
qualify for urgency. All other statuses are excluded from due/overdue lists.
"""
from __future__ import annotations
from datetime import datetime, date, timedelta
from io import BytesIO, StringIO
from collections import Counter
import csv

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from v31_cetaris_codes import normalize_primary_rfr, normalize_wo_status
from v31_cetaris import _parse_dt

PENDING_CODES=frozenset(("T",))
DONE_CODES=frozenset(("D","C"))

def due_dates_for_shift(code, shift_day):
    # A PM is due on its civil calendar date, through 11:59 PM.
    # S3/S5 do NOT make two dates simultaneously "due today".
    day=date.fromisoformat(shift_day) if isinstance(shift_day,str) else shift_day
    return frozenset((day,))

def due_date(value):
    if isinstance(value,(int,float)) and 20000<=value<=90000:
        parsed=from_excel(value)
        return parsed.date() if isinstance(parsed,datetime) else parsed
    if isinstance(value,datetime):
        return value.date()
    if isinstance(value,date):
        return value
    value=str(value or "").strip()
    if not value:
        return None
    if value.replace(".", "", 1).isdigit():
        try:
            number=float(value)
            if 20000<=number<=90000:
                parsed=from_excel(number)
                return parsed.date() if isinstance(parsed,datetime) else parsed
        except (TypeError,ValueError):
            pass
    try:
        return datetime.fromisoformat(value.replace("Z","+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y","%m/%d/%y","%m-%d-%Y",
                "%m/%d/%Y %I:%M %p","%m/%d/%Y %I:%M:%S %p",
                "%m/%d/%Y %H:%M",
                "%Y-%m-%d %I:%M:%S %p"):
        try:
            return datetime.strptime(value,fmt).date()
        except ValueError:
            pass
    return None

def _cell(value):
    if value is None:
        return ""
    if isinstance(value,datetime):
        return value.strftime("%m/%d/%Y %I:%M %p")
    if isinstance(value,date):
        return value.strftime("%m/%d/%Y")
    return str(int(value)) if isinstance(value,float) and value.is_integer() else str(value).strip()

def _grid(content):
    wb=None
    try:
        wb=load_workbook(BytesIO(content),read_only=True,data_only=True)
        grid=wb.active.iter_rows(values_only=True)
        headers=next(grid,None)
        yield headers,grid
    except Exception as exc:
        if wb is not None:
            raise ValueError("Unable to read the Excel WO Details sheet") from exc
        try:
            grid=iter(csv.reader(StringIO(content.decode("utf-8-sig"))))
            yield next(grid,None),grid
        except (UnicodeError,csv.Error) as error:
            raise ValueError("Invalid CSV or Excel WO Details report") from error
    finally:
        if wb is not None:
            wb.close()

def scan(content,code,shift_day):
    dates=due_dates_for_shift(code,shift_day)
    result={"rows":[],"available":False,"target_dates":sorted(x.isoformat() for x in dates),
            "matching":0,"missing_due":0,"to_be_done_seen":0,
            "scanned_rows":0,"notes":[],"catalog_rows":[],"skipped_pkm_assets":0}
    for headers,grid in _grid(content):
        if not headers:
            result["notes"].append("WO Details export contains no headings")
            return result
        mapping={_cell(h).strip().casefold():i for i,h in enumerate(headers) if _cell(h).strip()}
        if "wo number" not in mapping or "pm due date" not in mapping:
            result["notes"].append("WO Details export lacks WO Number or PM Due Date; re-export unfiltered WO Details with PM Due Date")
            return result
        if "primary rfr" not in mapping and "sj / pm code" not in mapping:
            result["notes"].append("WO Details export lacks Primary RFR and SJ / PM Code; PM classification unavailable")
            return result
        result["available"]=True
        def get(row,name):
            pos=mapping.get(name)
            return row[pos] if pos is not None and pos<len(row) else None
        keyed={}
        for row in grid:
            if not row or not any(v is not None and str(v).strip() for v in row):
                continue
            result["scanned_rows"]+=1
            wo=_cell(get(row,"wo number"))
            if not wo:
                continue
            # The equipment's Asset # is authoritative for PM listings.
            # Asset ID may be a conflicting alias; fall back only if blank.
            asset_id=_cell(get(row,"asset #")) or _cell(get(row,"asset id"))
            if asset_id.casefold().startswith("pkm"):
                result["skipped_pkm_assets"]+=1
                continue
            rfr,code_rfr=normalize_primary_rfr(_cell(get(row,"primary rfr")))
            if code_rfr!="08":
                continue
            result["matching"]+=1
            raw_due=get(row,"pm due date")
            parsed=due_date(raw_due)
            if not parsed:
                result["missing_due"]+=1
                continue
            raw_status=_cell(get(row,"wo status"))
            status,status_code=normalize_wo_status(raw_status)
            if raw_status.casefold() in ("to be done","to-be-done","t"):
                status,status_code="To be Done","T"
            if status_code=="T":
                result["to_be_done_seen"]+=1
            if status_code=="V":
                # Void work orders are retained as historical WO records,
                # but must never become an outstanding scheduled PM.
                continue
            state=("completed" if status_code in DONE_CODES else
                   "outstanding" if status_code in PENDING_CODES else "review")
            updated=_parse_dt(get(row,"update date / time"))
            item={"wo_number":wo,"wo_date":_cell(get(row,"wo date / time")),
                  "pm_due_date":parsed.strftime("%m/%d/%Y"),
                  "due_iso":parsed.isoformat(),
                  "promise_date":_cell(get(row,"promise date / time")),
                  "asset_num":asset_id,
                  "wo_status":status or raw_status or "Status not provided",
                  "status_code":status_code,"state":state,
                  "source":"WO Details",
                  "_updated":updated.isoformat() if updated else ""}
            previous=keyed.get(wo)
            if previous is None:
                keyed[wo]=item
            else:
                prior_time=previous["_updated"]
                new_time=item["_updated"]
                # The most recently updated row is the newest status; when
                # timestamps are absent, avoid declaring a finished PM open
                # merely because an older To-Be-Done labor row was repeated.
                if ((new_time and (not prior_time or new_time>prior_time)) or
                    ((new_time==prior_time or (not prior_time and not new_time)) and
                     previous["state"]!="completed" and state=="completed")):
                    keyed[wo]=item
        # Entire current file, including older due dates. No records from
        # prior uploads or prior shifts are consulted for this list.
        result["catalog_rows"]=sorted(
            ({k:v for k,v in item.items() if not k.startswith("_")}
             for item in keyed.values()),
            key=lambda x:(x["due_iso"],x["wo_number"]))
        result["rows"]=sorted(
            ({k:v for k,v in item.items() if not k.startswith("_")}
             for item in keyed.values() if item["due_iso"] in result["target_dates"]
             and item["status_code"]=="T"),
            key=lambda x:(x["due_iso"],x["wo_number"]))
        if result["missing_due"]:
            result["notes"].append(
                f'{result["missing_due"]} PM rows lacked a parseable PM Due Date and could not be assigned to a day')
        if not result["to_be_done_seen"]:
            result["notes"].append(
                "No To-Be-Done PM rows found. The single WO Details export may be filtered; re-export with all RFR 08 PMs including unassigned To-Be-Done WOs.")
        return result
    return result
