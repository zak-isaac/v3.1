"""Synthetic end-to-end Cetaris tests, entirely isolated from live Workshop V3."""
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
import app as m

TZ=ZoneInfo("America/Indiana/Indianapolis")
HEADERS=["WO Number","Asset #","WO Status","Primary RFR",
         "Service/Repair Description","Technician","Technician Remarks",
         "Start Date","In Service Date / Time","WO Closed Date/Time",
         "WO Last Complete Date / Time","SJ / PM Code","Completed By"]


def clock(monkeypatch, year=2026, month=10, day=26, hour=10):
    original=datetime
    fixed=original(year,month,day,hour,tzinfo=TZ)
    class FakeTime:
        combine=original.combine
        @staticmethod
        def now(tz):
            return fixed
    monkeypatch.setattr(m,"datetime",FakeTime)


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(m,"PRIVATE",tmp_path)
    monkeypatch.setattr(m,"DB_FILE",tmp_path/"v31.sqlite3")
    monkeypatch.setattr(m,"SESSION_SECRET_FILE",tmp_path/"key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH",raising=False)
    with TestClient(m.app) as c:
        yield c


def enter(c,code="S1"):
    login=c.post("/api/login",json={"associate":"8972"})
    assert login.status_code==200
    r=c.post("/api/shifts/start",json={"code":code})
    assert r.status_code==200,r.text
    return r.json()["id"]


def workbook(rows):
    w=Workbook();sheet=w.active;sheet.append(HEADERS)
    for row in rows:
        sheet.append([row.get(column) for column in HEADERS])
    output=BytesIO();w.save(output);w.close()
    return output.getvalue()


def row(wo,tech="Synthetic A",category="Proactive Repair",status="Closed",
        started="2026-10-26 07:45",serviced="2026-10-26 08:05",remarks="Repaired wiring",
        pm="",issue="Repair completed"):
    return {"WO Number":wo,"Asset #":"ASSET"+wo,"WO Status":status,
        "Primary RFR":category,"Service/Repair Description":issue,
        "Technician":tech,"Technician Remarks":remarks,"Start Date":started,
        "In Service Date / Time":serviced,"SJ / PM Code":pm}


def upload(c,rows):
    return c.post("/api/reporting/stage",
        files={"file":("cetaris.xlsx",workbook(rows),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def import_techs(c,techs):
    return c.post("/api/reporting/import",json={"technicians":techs})


def test_selected_shift_required_for_reporting(client,monkeypatch):
    clock(monkeypatch)
    assert client.get("/api/reporting/state").status_code==401
    client.post("/api/login",json={"associate":"8972"})
    assert client.get("/api/reporting/state").status_code==409
    enter_id=client.post("/api/shifts/start",json={"code":"S1"}).json()["id"]
    result=client.get("/api/reporting/state").json()
    assert result["shift"]["id"]==enter_id
    assert result["rows"]==[] and result["pm_count"]==0


def test_two_tech_one_closed_wo_grouped_with_remarks(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    uploaded=upload(client,[
      row("9001",tech="Synthetic A",remarks="Located sensor fault",issue="Photoeye not sensing"),
      row("9001",tech="Synthetic B",remarks="Replaced sensor",issue="Photoeye not sensing"),
      row("9002",tech="Unselected C",remarks="Different job"),
    ])
    assert uploaded.status_code==200,uploaded.text
    options=uploaded.json()["technicians"]
    assert {t["name"] for t in options}=={"Synthetic A","Synthetic B","Unselected C"}
    result=import_techs(client,["Synthetic A","Synthetic B"])
    assert result.status_code==200,result.text
    data=client.get("/api/reporting/state").json()
    assert len(data["rows"])==1
    wo=data["rows"][0]
    assert wo["wo_number"]=="9001"
    assert wo["cause"]=="Photoeye not sensing"
    assert wo["asset"]=="ASSET9001"
    assert wo["status"]=="RTS"
    assert "Synthetic A" in wo["tech"] and "Synthetic B" in wo["tech"]
    assert "Located sensor fault" in wo["remarks"] and "Replaced sensor" in wo["remarks"]
    assert wo["excluded"] is False
    assert data["pm_count"]==0


def test_pm_counts_once_and_does_not_enter_shift_happenings(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    records=[
      row("PM900",category="Preventive Maintenance",status="Closed",
          remarks="Inspected equipment",pm="RNPK.001.PKM"),
      row("PM900",tech="Synthetic B",category="Preventive Maintenance",
          status="Closed",remarks="Lubricated bearings",pm="RNPK.001.PKM"),
      row("9003",category="Breakdown",status="In Process",
          started="2026-10-26 09:00",serviced=None,remarks="Asset offline"),
    ]
    assert upload(client,records).status_code==200
    r=import_techs(client,["Synthetic A","Synthetic B"])
    assert r.status_code==200,r.text
    data=client.get("/api/reporting/state").json()
    assert data["pm_count"]==1
    assert data["pms"][0]["wo_number"]=="PM900"
    assert "PM900" not in {v["wo_number"] for v in data["rows"]}
    assert any(x["wo_number"]=="9003" and x["status"]=="OOS" for x in data["rows"])


def test_edit_and_exclude_survive_reimport_and_relogin(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    records=[row("9010",remarks="Changed bearing")]
    upload(client,records)
    assert import_techs(client,["Synthetic A"]).status_code==200
    edited=client.post("/api/reporting/work-orders/9010",
        json={"cause":"Confirmed defective bearing","excluded":True})
    assert edited.status_code==200,edited.text
    upload(client,records)
    summary=import_techs(client,["Synthetic A"]).json()["summary"]
    assert summary["preserved_edits"]==1
    client.post("/api/logout")
    assert client.post("/api/login",json={"associate":"8972"}).status_code==200
    sid=client.get("/api/me").json()["open"][0]["id"]
    assert client.post("/api/shifts/resume",json={"shift_id":sid}).status_code==200
    persisted=client.get("/api/reporting/state").json()["rows"][0]
    assert persisted["cause"]=="Confirmed defective bearing" and persisted["excluded"]


def test_multiple_uploads_add_not_erase_prior_workorders(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    upload(client,[row("9101")])
    import_techs(client,["Synthetic A"])
    upload(client,[row("9102")])
    import_techs(client,["Synthetic A"])
    data=client.get("/api/reporting/state").json()
    assert {x["wo_number"] for x in data["rows"]}=={"9101","9102"}


def test_overnight_s3_uses_shift_start_date(client,monkeypatch):
    clock(monkeypatch,day=26,hour=21)
    enter(client,"S3")
    record=row("S3WO",started="2026-10-27 02:00",
               serviced="2026-10-27 02:15")
    upload(client,[record])
    result=import_techs(client,["Synthetic A"])
    assert result.status_code==200,result.text
    data=client.get("/api/reporting/state").json()
    assert data["shift"]["date"]=="2026-10-26"
    assert [r["wo_number"] for r in data["rows"]]==["S3WO"]


def test_weekend_s5_uses_friday_start_date(client,monkeypatch):
    clock(monkeypatch,day=30,hour=21)
    enter(client,"S5")
    record=row("S5WO",started="2026-10-31 01:00",
               serviced="2026-10-31 01:10")
    upload(client,[record])
    result=import_techs(client,["Synthetic A"])
    assert result.status_code==200,result.text
    data=client.get("/api/reporting/state").json()
    assert data["shift"]["date"]=="2026-10-30"
    assert [r["wo_number"] for r in data["rows"]]==["S5WO"]


def test_completed_non_pm_pending_review_not_silently_closed(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    upload(client,[row("9301",status="Completed")])
    r=import_techs(client,["Synthetic A"])
    assert r.status_code==200,r.text
    result=client.get("/api/reporting/state").json()
    assert result["rows"]==[]
    assert any(x["wo_number"]=="9301" for x in result["audit"]["pending"])


def test_invalid_workbook_does_not_replace_prior_stage(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    upload(client,[row("9501")])
    invalid=client.post("/api/reporting/stage",
       files={"file":("bad.xlsx",b"not a workbook","application/octet-stream")})
    assert invalid.status_code==422
    assert client.get("/api/reporting/state").json()["stage"]["filename"]=="cetaris.xlsx"


def test_draft_is_shift_scoped_and_wo_edit_invalidates_saved_passdown(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    upload(client,[row("9901")])
    import_techs(client,["Synthetic A"])
    saved=client.post("/api/shifts/report",
       json={"content":"IND3 S1 SHIFT HAPPENINGS: WO# 9901. Repaired sensor and confirmed RTS."})
    assert saved.status_code==200,saved.text
    assert client.get("/api/me").json()["report_saved"] is True
    assert client.post("/api/reporting/draft",
       json={"notes":"Waiting for a spare part","vendors":[],"ro":[],
             "loto":False,"missingKey":""}).status_code==200
    assert client.get("/api/me").json()["report_saved"] is False
    assert client.post("/api/shifts/end").status_code==409
    assert client.get("/api/reporting/state").json()["draft"]["notes"]=="Waiting for a spare part"


def test_frontend_shift_data_controller_is_connected(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    page=client.get("/shift-report.html")
    assert page.status_code==200
    assert '/web/shift-data.js' in page.text
    assert 'window.v31ReportView' in page.text
    script=client.get("/web/shift-data.js")
    assert script.status_code==200
    assert "/api/reporting/stage" in script.text


def test_drax_assignments_resolve_s1_s2_overlap(client,monkeypatch,tmp_path):
    import csv
    import v31_roster
    clock(monkeypatch,day=26,hour=13)
    with (tmp_path/"mst-roster.csv").open("w",newline="") as output:
        writer=csv.writer(output)
        writer.writerow(["Name","Assoc. #","Shift","Job Title"])
        writer.writerow(["Synthetic A","111111","S1","MST"])
    v31_roster.sync(tmp_path,tmp_path/"v31.sqlite3",set(m.MANAGEMENT_ASSOCIATES),force=True)
    enter(client,"S1")
    upload(client,[row("OVERLAP1",started="2026-10-26 12:45",
                       serviced="2026-10-26 13:05")])
    r=import_techs(client,["Synthetic A"])
    assert r.status_code==200,r.text
    assert r.json()["summary"]["roster_matched"]==1
    assert [x["wo_number"] for x in client.get("/api/reporting/state").json()["rows"]]==["OVERLAP1"]


def test_missing_drax_shift_evidence_flags_overlap_in_audit(client,monkeypatch):
    clock(monkeypatch,day=26,hour=13)
    enter(client,"S1")
    upload(client,[row("UNCERTAIN1",started="2026-10-26 12:45",
                       serviced="2026-10-26 13:05")])
    assert import_techs(client,["Synthetic A"]).status_code==200
    data=client.get("/api/reporting/state").json()
    assert data["rows"]==[]
    assert any(x["wo_number"]=="UNCERTAIN1" for x in data["audit"]["pending"])


def test_completed_work_order_reminder_is_not_falsely_rts(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    upload(client,[row("REMINDER1",status="Completed")])
    result=client.post("/api/reporting/import",
       json={"technicians":["Synthetic A"],"closure_reviews":{"REMINDER1":"add"}})
    assert result.status_code==200,result.text
    data=client.get("/api/reporting/state").json()
    assert len(data["rows"])==1
    assert data["rows"][0]["status"]=="PENDING"
    assert "Please close this WO" in data["rows"][0]["remarks"]


def test_cross_shift_breakdown_can_transition_from_oos_to_rts(client,monkeypatch,tmp_path):
    import csv
    import v31_roster
    clock(monkeypatch,day=26,hour=10)
    with (tmp_path/"mst-roster.csv").open("w",newline="") as output:
        writer=csv.writer(output)
        writer.writerow(["Name","Assoc. #","Shift","Job Title"])
        writer.writerow(["Synthetic A","111111","S2","MST"])
    v31_roster.sync(tmp_path,tmp_path/"v31.sqlite3",set(m.MANAGEMENT_ASSOCIATES),force=True)
    s1=enter(client,"S1")
    upload(client,[row("BREAK1",status="In Process",category="Breakdown",
                       started="2026-10-26 09:00",serviced=None,
                       remarks="Asset stopped; awaiting replacement")])
    first=import_techs(client,["Synthetic A"])
    assert first.status_code==200,first.text
    first_record=client.get("/api/reporting/state").json()["rows"][0]
    assert first_record["status"]=="OOS"
    clock(monkeypatch,day=26,hour=14)
    second=client.post("/api/shifts/start",json={"code":"S2"})
    assert second.status_code==200,second.text
    s2=second.json()["id"]
    upload(client,[row("BREAK1",status="Closed",category="Breakdown",
                       started="2026-10-26 13:55",serviced="2026-10-26 14:10",
                       remarks="Replacement installed and tested")])
    final=import_techs(client,["Synthetic A"])
    assert final.status_code==200,final.text
    current=client.get("/api/reporting/state").json()["rows"]
    assert len(current)==1 and current[0]["status"]=="RTS"
    with m._db() as db:
        old=db.execute("SELECT status FROM report_wo WHERE shift_id=?",(s1,)).fetchone()
        new=db.execute("SELECT status FROM report_wo WHERE shift_id=?",(s2,)).fetchone()
    assert old["status"]=="OOS" and new["status"]=="RTS"
