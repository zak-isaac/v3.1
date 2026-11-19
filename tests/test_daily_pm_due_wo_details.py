"""Due-date PM inventory from daily WO Details and Today's DAILY PMs.

All data is synthetic. Test both unassigned To-Be-Done PMs and real
completion, without granting nonexistent technician or shift labor.
"""
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from test_reporting import client,clock,enter,upload,import_techs,row


def details(rows):
    headers=["WO Number","Asset #","WO Status","Primary RFR",
             "Service/Repair Description","Technician","Technician Remarks",
             "Start Date","PM Due Date","Promise Date / Time",
             "WO Date / Time","SJ / PM Code","Update Date / Time"]
    wb=Workbook()
    ws=wb.active
    ws.append(headers)
    for item in rows:
        ws.append([item.get(h) for h in headers])
    b=BytesIO()
    wb.save(b);wb.close()
    return b.getvalue()


def to_be_done(wo,due="10/26/2026",rfr="08",status="To be Done",
               asset=None,tech=None):
    return {"WO Number":wo,"Asset #":asset or "ASSET-"+wo,
            "WO Status":status,"Primary RFR":rfr,
            "Service/Repair Description":"Daily inspection",
            "Technician":tech,"Technician Remarks":"",
            "PM Due Date":due,"WO Date / Time":"10/23/2026 10:00 AM",
            "Promise Date / Time":"10/26/2026 06:00 PM"}


def stage_details(c,items):
    return c.post("/api/reporting/stage",files={"file":(
        "daily-wo-details.xlsx",details(items),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_unassigned_to_be_done_and_other_due_pms_are_found_in_wo_details(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    items=[
        to_be_done("PM-T"),
        to_be_done("PM-I",status="In Process"),
        to_be_done("PM-D",status="Completed"),
        to_be_done("PM-TOMORROW",due="10/27/2026"),
        to_be_done("PM-OLD",due="10/25/2026"),
        to_be_done("NOT-PM",rfr="11"),
        {**to_be_done("PM-MISSING"),"PM Due Date":None},
        row("SHIFT-WO",category="Proactive Repair",status="C"),
    ]
    response=stage_details(client,items)
    assert response.status_code==200,response.text
    assert response.json()["pm_due"]["due_count"]==3
    # The three outstanding/finished PMs have NO Technician and therefore
    # are not eligible for imported labor, but still belong on the due list.
    state=client.get("/api/reporting/state").json()
    pm=state["pm_needing_completion"]
    assert pm["auto_available"] and pm["has_source"]
    assert not pm["present"]
    assert pm["due_count"]==3 and pm["count"]==2 and pm["completed_count"]==1
    assert {p["wo_number"] for p in pm["rows"]}=={"PM-T","PM-I","PM-D"}
    assert next(p for p in pm["rows"] if p["wo_number"]=="PM-T")["wo_status"]=="To be Done"
    assert pm["rows"][0]["pm_due_date"]=="10/26/2026"
    assert pm["target_dates"]==["2026-10-26"]
    assert pm["notes"]  # To-Be-Done rows and missing due-date warning.
    assert state["pm_count"]==0
    assert state["rows"]==[]
    preview=client.get("/api/shifts/report/preview")
    assert preview.status_code==200,preview.text
    html=preview.json()["html"]
    assert "PM-T" in html and "PM-D" in html
    assert "2 still needing completion" in html
    assert "NOT-PM" not in html


def test_manual_today_daily_pms_fills_gap_in_filtered_wo_details(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    # The normal WO Details export was filtered: no unassigned To-Be-Done
    # records. Its Completed PM should stay completed after list merge.
    report=stage_details(client,[to_be_done("PM-D",status="Completed")])
    assert report.status_code==200,report.text
    pm_csv=(b"WO Number,PM Due Date,Asset #\n"
            b"PM-D,10/26/2026,ASSET-PM-D\n"
            b"PM-T1,10/26/2026,ASSET-PM-T1\n"
            b"PM-T2,10/26/2026,ASSET-PM-T2\n"
            b"PM-TOMORROW,10/27/2026,ASSET-PM-LATER\n")
    uploaded=client.post("/api/pm-needing-completion/upload",
          files={"file":("todays-daily-pms.csv",pm_csv,"text/csv")})
    assert uploaded.status_code==200,uploaded.text
    pm=uploaded.json()
    assert pm["present"] and pm["auto_available"]
    assert pm["due_count"]==3 and pm["count"]==2 and pm["completed_count"]==1
    assert {p["wo_number"] for p in pm["rows"]}=={"PM-D","PM-T1","PM-T2"}
    assert next(p for p in pm["rows"] if p["wo_number"]=="PM-D")["state"]=="completed"
    assert client.get("/api/pm-needing-completion/download").content==pm_csv
    deleted=client.delete("/api/pm-needing-completion")
    assert deleted.status_code==200
    after=client.get("/api/pm-needing-completion").json()
    assert not after["present"] and after["auto_available"]
    assert after["due_count"]==1 and after["count"]==0


def test_missing_date_is_flagged_not_assumed_due_today(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    response=stage_details(client,[to_be_done("PM-NODATE",due="")])
    assert response.status_code==200,response.text
    due=client.get("/api/pm-needing-completion").json()
    assert due["due_count"]==0 and due["count"]==0
    assert any("no valid" in note.lower() or "parseable" in note.lower()
               for note in due["notes"])


def test_overnight_s3_includes_both_actual_due_dates_not_other_dates(
    client,monkeypatch
):
    clock(monkeypatch,day=27,hour=2)
    enter(client,"S3")
    pm_rows=[to_be_done("S3-EVENING",due="10/26/2026"),
             to_be_done("S3-MORNING",due="10/27/2026"),
             to_be_done("S3-AFTER",due="10/28/2026")]
    staged=stage_details(client,pm_rows)
    assert staged.status_code==200,staged.text
    due=client.get("/api/pm-needing-completion").json()
    assert due["target_dates"]==["2026-10-26","2026-10-27"]
    assert {r["wo_number"] for r in due["rows"]}=={"S3-EVENING","S3-MORNING"}
    assert due["count"]==2


def test_only_due_pms_no_tech_does_not_fabricate_shift_work(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    staged=stage_details(client,[to_be_done("UNASSIGNED-PM")])
    assert staged.status_code==200,staged.text
    due=client.get("/api/pm-needing-completion").json()
    assert due["count"]==1
    assert client.get("/api/reporting/state").json()["rows"]==[]
    assert client.get("/api/reporting/state").json()["pm_count"]==0
    assert client.get("/api/assets/status").json()["rts"]==[]
