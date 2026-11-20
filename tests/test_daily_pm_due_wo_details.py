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


def test_unassigned_to_be_done_only_is_due_in_wo_details(
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
    assert response.json()["pm_due"]["due_count"]==1
    # Only To Be Done is urgent; In Process and Completed are excluded.
    # No Technician is assigned, so no labor must be fabricated.
    state=client.get("/api/reporting/state").json()
    pm=state["pm_needing_completion"]
    assert pm["auto_available"] and pm["has_source"]
    assert not pm["present"]
    assert pm["due_count"]==1 and pm["count"]==1 and pm["completed_count"]==0
    assert {p["wo_number"] for p in pm["rows"]}=={"PM-T"}
    assert next(p for p in pm["rows"] if p["wo_number"]=="PM-T")["wo_status"]=="To be Done"
    assert pm["rows"][0]["pm_due_date"]=="10/26/2026"
    assert pm["target_dates"]==["2026-10-26"]
    assert pm["notes"]  # To-Be-Done rows and missing due-date warning.
    assert state["pm_count"]==0
    assert state["rows"]==[]
    preview=client.get("/api/shifts/report/preview")
    assert preview.status_code==200,preview.text
    html=preview.json()["html"]
    assert "PM-T" in html and "PM-D" not in html
    assert "1 To Be Done PMs due today" in html
    assert "NOT-PM" not in html


def test_one_wo_details_file_classifies_to_be_done_by_pm_due_date(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    # One current Cetaris export: overdue PMs can be weeks old.
    # Do not infer PM status from a WO Date or an earlier upload.
    items=[
        to_be_done("PM-PAST",due="10/25/2026"),
        to_be_done("PM-WEEKS",due="10/03/2026"),
        to_be_done("PM-TODAY",due="10/26/2026"),
        to_be_done("PM-FUTURE",due="10/27/2026"),
        to_be_done("PM-IN-PROCESS",due="10/25/2026",status="In Process"),
        to_be_done("PM-PARTS",due="10/25/2026",status="Parts Pending"),
        to_be_done("PM-REVIEW",due="10/25/2026",status="Manager Review Required"),
        to_be_done("PM-OUTSIDE",due="10/25/2026",status="Outside Service"),
        to_be_done("PM-UNCLOSED",due="10/25/2026",status="Unclosed"),
        to_be_done("PM-COMPLETED",due="10/25/2026",status="Completed"),
        to_be_done("PM-CLOSED",due="10/26/2026",status="Closed"),
        to_be_done("PM-VOID",due="10/25/2026",status="Void"),
        to_be_done("PM-NO-STATUS",due="10/26/2026",status=""),
        to_be_done("PKM-EXCLUDE",due="10/25/2026",asset="PKM12345"),
        to_be_done("OTHER-RFR",due="10/25/2026",rfr="11"),
    ]
    staged=stage_details(client,items)
    assert staged.status_code==200,staged.text
    assert staged.json()["pm_due"]["due_count"]==1
    pm=client.get("/api/pm-needing-completion").json()
    assert pm["due_count"]==pm["count"]==1
    assert [r["wo_number"] for r in pm["rows"]]==["PM-TODAY"]
    assert pm["rows"][0]["urgency"]=="yellow"
    assert pm["past_due_count"]==2
    assert {r["wo_number"] for r in pm["past_due_rows"]}=={
        "PM-PAST","PM-WEEKS"}
    assert all(r["urgency"]=="red" and r["wo_status"]=="To be Done"
               for r in pm["past_due_rows"])
    assert pm["completed_count"]==0
    assert pm["outstanding_total"]==3


def test_outlook_email_uses_yellow_today_and_red_past_due(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    assert stage_details(client,[
        to_be_done("YELLOW-01",due="10/26/2026"),
        to_be_done("RED-01",due="10/25/2026"),
        to_be_done("GREY-DONE",due="10/26/2026",status="Closed"),
        to_be_done("OLD-DONE",due="10/24/2026",status="Completed"),
        to_be_done("FUTURE",due="10/27/2026")
    ]).status_code==200
    html=client.get("/api/shifts/report/preview").json()["html"]
    assert "YELLOW-01" in html and "RED-01" in html
    assert "#FFF2C7" in html and "#FDE8E8" in html
    assert "DUE TODAY — 11:59 PM" in html and "PAST DUE" in html
    assert "GREY-DONE" not in html
    assert "OLD-DONE" not in html and "FUTURE" not in html



def test_missing_date_is_flagged_not_assumed_due_today(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    response=stage_details(client,[to_be_done("PM-NODATE",due="")])
    assert response.status_code==200,response.text
    due=client.get("/api/pm-needing-completion").json()
    assert due["due_count"]==0 and due["count"]==0
    assert any("no valid" in note.lower() or "parseable" in note.lower()
               for note in due["notes"])


def test_overnight_s3_switches_yellow_to_red_after_midnight(
    client,monkeypatch
):
    clock(monkeypatch,day=27,hour=2)
    enter(client,"S3")
    pm_rows=[to_be_done("S3-YESTERDAY",due="10/26/2026"),
             to_be_done("S3-TODAY",due="10/27/2026"),
             to_be_done("S3-TOMORROW",due="10/28/2026")]
    staged=stage_details(client,pm_rows)
    assert staged.status_code==200,staged.text
    due=client.get("/api/pm-needing-completion").json()
    assert due["target_dates"]==["2026-10-27"]
    assert [r["wo_number"] for r in due["rows"]]==["S3-TODAY"]
    assert [r["wo_number"] for r in due["past_due_rows"]]==["S3-YESTERDAY"]
    assert due["count"]==1 and due["past_due_count"]==1


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


def test_ind3_style_s5_daily_date_and_time_are_calendar_based():
    from v31_pm_due_scanner import scan
    source=details([
        to_be_done("S5-DUE",due="10/25/2026 8:24 PM",status="To be Done"),
        to_be_done("S5-FUTURE",due="10/26/2026 4:30 AM",status="To be Done"),
        to_be_done("S5-AFTER",due="10/27/2026 12:00 AM",status="To be Done"),
        to_be_done("S5-OTHER-RFR",due="10/25/2026 3:01 AM",rfr="04"),
    ])
    inventory=scan(source,"S5","2026-10-25")
    assert inventory["available"] is True
    assert inventory["target_dates"]==["2026-10-25"]
    assert [r["wo_number"] for r in inventory["rows"]]==["S5-DUE"]
    assert {r["wo_number"] for r in inventory["catalog_rows"]}=={
        "S5-DUE","S5-FUTURE","S5-AFTER"}


def test_ind3_style_excel_serial_due_date_is_read_safely():
    from v31_pm_due_scanner import scan,due_date
    from openpyxl.utils.datetime import to_excel
    from datetime import datetime
    day=datetime(2026,10,26,4,30)
    serial=to_excel(day)
    assert due_date(serial).isoformat()=="2026-10-26"
    assert due_date(str(serial)).isoformat()=="2026-10-26"
    raw=details([to_be_done("PM-SERIAL",due=serial)])
    result=scan(raw,"S1","2026-10-26")
    assert [r["wo_number"] for r in result["rows"]]==["PM-SERIAL"]


def test_pm_only_wo_details_does_not_block_end_shift_technician_confirmation(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    assert stage_details(client,[to_be_done("DUE-UNASSIGNED")]).status_code==200
    # No technician exists in the file and none should be fabricated.
    state=client.get("/api/reporting/state").json()
    assert state["stage"]["technicians"]==[]
    assert not state["stage"]["imported"]
    assert state["pm_needing_completion"]["count"]==1
    assert client.post("/api/shifts/report",json={}).status_code==200
    ready=client.get("/api/shifts/end/readiness")
    assert ready.status_code==200,ready.text
    assert not any("Confirm its technicians" in reason
                   for reason in ready.json().get("blockers",[]))
