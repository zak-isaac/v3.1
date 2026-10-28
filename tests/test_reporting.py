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
         "Start Date","In Service Date / Time","WO Closed Date/Time","WO Date / Time",
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
        json={"cause":"Confirmed defective bearing","remarks":"Inspected and replaced bearing","excluded":True})
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
    assert persisted["remarks"]=="Inspected and replaced bearing"


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
    assert 'id="edit-remarks"' in page.text
    script=client.get("/web/shift-data.js")
    assert script.status_code==200
    assert "/api/reporting/stage" in script.text
    assert "Next-Day Work Orders" in script.text


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


def test_next_day_wo_visible_in_audit_without_stealing_next_shift(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    imported=row("TODAY1",started="2026-10-26 08:00",serviced="2026-10-26 08:10")
    tomorrow=row("TOMORROW1",started="2026-10-27 06:00",
                 serviced="2026-10-27 06:12",issue="Next day asset repair")
    assert upload(client,[imported,tomorrow]).status_code==200
    result=import_techs(client,["Synthetic A"])
    assert result.status_code==200,result.text
    state=client.get("/api/reporting/state").json()
    assert {r["wo_number"] for r in state["rows"]}=={"TODAY1"}
    day=state["audit"]["next_day"]
    assert len(day)==1
    assert day[0]["wo_number"]=="TOMORROW1"
    assert day[0]["date"]=="2026-10-27"
    assert day[0]["timestamp_type"]=="Work/completion recorded"
    assert day[0]["selected_technician"] is True
    assert day[0]["included_in_shift"] is False
    assert day[0]["disposition"]=="Not attributed to selected shift"
    assert state["audit"]["summary"]["next_day_wos"]==1
    assert len(client.get("/api/reporting/state").json()["audit"]["next_day"])==1


def test_s3_next_day_overnight_wo_appears_as_included(client,monkeypatch):
    clock(monkeypatch,day=26,hour=21)
    enter(client,"S3")
    assert upload(client,[row("S3_NIGHT",started="2026-10-27 01:00",
                      serviced="2026-10-27 01:15")]).status_code==200
    result=import_techs(client,["Synthetic A"])
    assert result.status_code==200,result.text
    data=client.get("/api/reporting/state").json()
    assert [r["wo_number"] for r in data["rows"]]==["S3_NIGHT"]
    audit=data["audit"]["next_day"]
    assert len(audit)==1
    assert audit[0]["included_in_shift"] is True
    assert audit[0]["disposition"]=="Included in selected shift"


def test_s5_next_day_overnight_pm_is_counted_and_audited(client,monkeypatch):
    clock(monkeypatch,day=30,hour=21)
    enter(client,"S5")
    r=row("PM_NIGHT",category="Preventive Maintenance",pm="E.001.BALER.WALK",
          started="2026-10-31 00:20",serviced="2026-10-31 00:33")
    assert upload(client,[r]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    data=client.get("/api/reporting/state").json()
    assert data["pm_count"]==1 and data["rows"]==[]
    audit=data["audit"]["next_day"]
    assert len(audit)==1
    assert audit[0]["wo_number"]=="PM_NIGHT"
    assert audit[0]["disposition"]=="PM counted in selected shift"
    assert audit[0]["included_in_shift"] is True


def test_next_day_administrative_closure_is_separate_from_work_date(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    r=row("CLOSE_LATER",started="2026-10-26 07:30",
          serviced="2026-10-26 08:10")
    r["WO Closed Date/Time"]="2026-10-27 09:00"
    assert upload(client,[r]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    data=client.get("/api/reporting/state").json()
    assert len(data["rows"])==1
    audit=data["audit"]["next_day"]
    assert len(audit)==1
    assert audit[0]["timestamp_type"]=="WO opened / administrative update only"
    assert audit[0]["work_timestamps"]==[]
    assert audit[0]["admin_timestamps"]==["2026-10-27T09:00:00"]
    assert audit[0]["included_in_shift"] is True


def test_next_day_wo_unique_across_labor_rows_and_unselected_visible(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    records=[
       row("OTHER_DAY",tech="Synthetic B",started="2026-10-27 06:05",
           serviced="2026-10-27 06:11"),
       row("OTHER_DAY",tech="Synthetic C",started="2026-10-27 06:04",
           serviced="2026-10-27 06:12"),
       row("CURRENT_DAY",tech="Synthetic A",started="2026-10-26 09:00",
           serviced="2026-10-26 09:15")
    ]
    assert upload(client,records).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    data=client.get("/api/reporting/state").json()
    assert {x["wo_number"] for x in data["rows"]}=={"CURRENT_DAY"}
    nextday=data["audit"]["next_day"]
    assert len(nextday)==1
    assert nextday[0]["wo_number"]=="OTHER_DAY"
    assert nextday[0]["technicians"]==["Synthetic B","Synthetic C"]
    assert nextday[0]["selected_technician"] is False
    assert nextday[0]["disposition"]=="Other technician / not selected"


def test_next_day_audit_migrates_previous_v31_schema_without_deleting_old_data(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    with m._db() as db:
        db.execute("""CREATE TABLE report_audit (
            shift_id INTEGER PRIMARY KEY,
            summary_json TEXT NOT NULL DEFAULT '{}',
            pending_json TEXT NOT NULL DEFAULT '[]',
            ignored_json TEXT NOT NULL DEFAULT '[]',updated_at TEXT NOT NULL
        )""")
        db.execute("""INSERT INTO report_audit
          (shift_id,summary_json,pending_json,ignored_json,updated_at)
          VALUES (1,'{"old_record":1}','[]','[]','2026-10-26')""")
    state=client.get("/api/reporting/state")
    assert state.status_code==200,state.text
    data=state.json()
    assert data["audit"]["next_day"]==[]
    assert data["audit"]["summary"]["old_record"]==1
    with m._db() as db:
        assert "next_day_json" in {r["name"] for r in
            db.execute("PRAGMA table_info(report_audit)")}


def test_next_day_opened_wo_is_audited_without_work_or_close(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    r=row("NEXT_OPEN",started=None,serviced=None,status="Open")
    r["WO Date / Time"]="2026-10-27 07:00"
    assert upload(client,[r]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    data=client.get("/api/reporting/state").json()
    assert data["rows"]==[]
    assert len(data["audit"]["next_day"])==1
    item=data["audit"]["next_day"][0]
    assert item["wo_number"]=="NEXT_OPEN"
    assert item["work_timestamps"]==[]
    assert item["admin_timestamps"]==["2026-10-27T07:00:00"]
    assert item["included_in_shift"] is False


def test_drax_preselects_matching_shift_msts_but_not_other_technicians(client,monkeypatch,tmp_path):
    import csv
    import v31_roster
    clock(monkeypatch,day=26,hour=10)
    roster=tmp_path/"mst-roster.csv"
    with roster.open("w",newline="") as output:
        writer=csv.writer(output)
        writer.writerow(["Name","Assoc. #","Shift","Job Title"])
        writer.writerow(["A Synthetic","111111","S1","MST"])
        writer.writerow(["B Synthetic","222222","S2","MST"])
        writer.writerow(["C Synthetic","333333","S1","Associate"])
    v31_roster.sync(tmp_path,tmp_path/"v31.sqlite3",set(m.MANAGEMENT_ASSOCIATES),force=True)
    enter(client,"S1")
    response=upload(client,[
        row("D100",tech="Synthetic, A"),
        row("D200",tech="Synthetic B"),
        row("D300",tech="Synthetic C"),
        row("D400",tech="Unknown Fourth")])
    assert response.status_code==200,response.text
    suggestions={t["name"]:t for t in response.json()["tech_suggestions"]}
    assert suggestions["Synthetic, A"]["group"]=="assigned"
    assert suggestions["Synthetic, A"]["preselected"] is True
    assert suggestions["Synthetic B"]["group"]=="other_shift"
    assert suggestions["Synthetic B"]["preselected"] is False
    assert suggestions["Synthetic C"]["group"]=="unmatched"
    assert suggestions["Unknown Fourth"]["group"]=="unmatched"
    assert all("number" not in t for t in suggestions.values())
    state=client.get("/api/reporting/state").json()
    assert state["tech_suggestions"]==response.json()["tech_suggestions"]
    assert state["stage"]["staged"] is True
    assert state["stage"]["imported"] is False
    assert state["rows"]==[]
    accepted=import_techs(client,["Synthetic, A","Synthetic B"])
    assert accepted.status_code==200,accepted.text
    final=client.get("/api/reporting/state").json()
    assert final["stage"]["imported"] is True
    assert {t["wo_number"] for t in final["rows"]}=={"D100","D200"}
    assert set(final["technicians"])=={"Synthetic, A","Synthetic B"}


def test_cetaris_replacement_requires_import_confirmation_again(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client)
    assert upload(client,[row("REPLACE1")]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    first=client.get("/api/reporting/state").json()
    assert first["stage"]["imported"] is True
    assert len(first["rows"])==1
    assert upload(client,[row("REPLACE2")]).status_code==200
    staged=client.get("/api/reporting/state").json()
    assert staged["stage"]["staged"] is True and staged["stage"]["imported"] is False
    assert {r["wo_number"] for r in staged["rows"]}=={"REPLACE1"}
    assert import_techs(client,["Synthetic A"]).status_code==200
    final=client.get("/api/reporting/state").json()
    assert final["stage"]["imported"] is True
    assert {r["wo_number"] for r in final["rows"]}=={"REPLACE1","REPLACE2"}


def test_shift_report_repositions_cetaris_and_uses_real_walmart_logo(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client)
    response=client.get("/shift-report.html")
    assert response.status_code==200
    page=response.text
    header=page.split('<section class="shift-header"',1)[1].split('<div class="workspace-grid"',1)[0]
    assert 'S3 Shift Report' not in header  # Displayed shift comes from server.
    assert 'id="replace-file"' in header
    assert 'id="change-techs"' in header
    assert 'id="view-audit"' in header
    assert 'id="progress-number"' in header
    assert 'id="v31-import-feedback"' in header
    assert 'id="progress-fill"' in header
    assert 'width:75%' not in page
    assert 'width:0%;height:100%' in page
    assert 'v31-brand-logo' in page
    assert '/web/walmart-2025.svg' in page
    assert 'sidebar-walmart' not in page
    assert '/web/report-header.css' in page
    logo=client.get("/web/walmart-2025.svg")
    assert logo.status_code==200
    assert '<svg' in logo.text and '#ffc220' in logo.text
    stylesheet=client.get("/web/report-header.css")
    assert stylesheet.status_code==200
    assert '#replace-file.v31-cetaris-primary' in stylesheet.text
    js=client.get("/web/shift-data.js").text
    assert "v31-tech-group" in js
    assert "Drax MSTs" in js
    assert "snapshot.stage.imported" in js


def test_file_accepted_but_empty_is_not_treated_as_work_order_import(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client)
    blank=workbook([])
    staged=client.post("/api/reporting/stage",
        files={"file":("empty.xlsx",blank,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert staged.status_code==200,staged.text
    assert staged.json()["stats"]["parsed_rows"]==0
    state=client.get("/api/reporting/state").json()
    assert state["stage"]["staged"] and not state["stage"]["imported"]
    assert state["tech_suggestions"]==[]
    assert state["rows"]==[] and state["pm_count"]==0


def test_command_center_layout_uses_full_width_metrics_and_real_empty_state(client, monkeypatch):
    clock(monkeypatch)
    enter(client)
    response=client.get("/shift-report.html")
    assert response.status_code==200
    markup=response.text
    # Metrics are across the full workspace, not compressed into the work-order column.
    header=markup.index('<section class="shift-header"')
    overview=markup.index('<section class="overview-section"')
    cards=markup.index('<section class="metrics"')
    workspace=markup.index('<div class="workspace-grid">')
    assert header < overview < cards < workspace
    assert markup.count('id="metric-work"')==1
    assert markup.count('id="metric-pms-completed"')==1
    assert '<span>Shift Workspace</span>' in markup
    assert 'Changes remain in this frontend preview' not in markup
    assert 'web/command-center.css' in markup
    script=markup[markup.rfind("<script>")+len("<script>"):markup.rfind("</script>")]
    assert 'v31-work-empty' in script
    assert 'Start with a Cetaris work-order export' in script
    assert 'Your file is ready for technician selection' in script
    assert 'No qualifying work orders for this shift' in script
    assert 'window.V31Reporting?.chooseTechs()' in script
    assert "if(searchBox)populateRows()" in script
    assert "state.stageImported=!!data.stage?.imported" in script


def test_command_center_css_responsive_and_protects_original_ui_actions(client, monkeypatch):
    clock(monkeypatch)
    enter(client)
    stylesheet=client.get("/web/command-center.css")
    assert stylesheet.status_code==200
    css=stylesheet.text
    for rule in (".overview-section", ".metrics", ".v31-work-empty",
                 ".workspace-grid", ".report-actions", ".sidebar",
                 "@media(max-width:1240px)", "@media(max-width:1030px)",
                 "@media(max-width:600px)"):
        assert rule in css
    assert "grid-template-columns:repeat(6,minmax(0,1fr))" in css
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in css
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in css
    assert "overflow-y:auto" in css
    markup=client.get("/shift-report.html").text
    # Design changes must not remove functional import/report/administration controls.
    for element_id in ("replace-file","change-techs","view-audit",
                       "save-draft","preview-report","generate-eml","end-shift",
                       "v31-admin-screen","v31-signout","v31-sidebar-user"):
        assert f'id="{element_id}"' in markup
    assert '/web/walmart-2025.svg' in markup
