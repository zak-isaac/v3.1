"""Existing WO + newly entered work time must appear in today's shift.

These synthetic daily Cetaris imports use one persistent V3.1 database.
The same WO retains its historical work without giving the importing
shift false completion, PM, or physical RTS credit.
"""
import test_reporting as helpers
from test_reporting import client, clock, enter, upload, import_techs, row


def _include_update_timestamp(monkeypatch):
    # The general test fixture intentionally uses a short Cetaris header
    # list. Expose the actual optional Update Date / Time export column.
    monkeypatch.setattr(
        helpers, "HEADERS", [*helpers.HEADERS, "Update Date / Time"]
    )


def test_old_in_process_wo_with_new_update_today_is_in_todays_happenings(
    client, monkeypatch
):
    _include_update_timestamp(monkeypatch)
    clock(monkeypatch, day=26, hour=10)
    s1=enter(client,"S1")
    original=row("REPEAT-I", category="11", status="I",
                 started="2026-10-26 08:05", serviced=None,
                 remarks="Initial proactive inspection, repair continuing")
    assert upload(client,[original]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    s1_note=client.get("/api/reporting/state").json()["rows"][0]
    assert s1_note["status"]=="IN_PROGRESS"

    clock(monkeypatch, day=26, hour=19)
    shift=client.post("/api/shifts/start",json={"code":"S2"})
    assert shift.status_code==200,shift.text
    newer={**original,
           "Update Date / Time":"2026-10-26 18:15",
           "Technician Remarks":"New troubleshooting recorded during S2"}
    assert upload(client,[newer]).status_code==200
    result=import_techs(client,["Synthetic A"])
    assert result.status_code==200,result.text
    current=client.get("/api/reporting/state").json()
    assert len(current["rows"])==1,current["rows"]
    assert current["rows"][0]["wo_number"]=="REPEAT-I"
    assert current["rows"][0]["status"]=="WORKED"
    assert "New troubleshooting recorded during S2" in current["rows"][0]["remarks"]
    with helpers.m._db() as conn:
        earlier=conn.execute(
            "SELECT remarks,status FROM report_wo WHERE shift_id=? AND wo_number=?",
            (s1,"REPEAT-I")).fetchone()
        assert earlier["status"]=="IN_PROGRESS"
        assert earlier["remarks"]==s1_note["remarks"]
    assets=client.get("/api/assets/status").json()
    assert assets["oos"]==[] and assets["rts"]==[]


def test_closed_wo_new_s2_labor_start_is_reported_without_recrediting_closure(
    client, monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    s1=enter(client,"S1")
    first=row("REPEAT-C",category="Proactive Repair",status="C",
              started="2026-10-26 08:10",serviced="2026-10-26 08:50",
              remarks="Earlier adjustment")
    assert upload(client,[first]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200

    clock(monkeypatch,day=26,hour=19)
    s2=client.post("/api/shifts/start",json={"code":"S2"})
    assert s2.status_code==200,s2.text
    today=row("REPEAT-C",category="Proactive Repair",status="C",
              started="2026-10-26 18:20",serviced="2026-10-26 08:50",
              remarks="Additional proactive repair performed on existing WO")
    assert upload(client,[first,today]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    current=client.get("/api/reporting/state").json()
    assert len(current["rows"])==1,current["rows"]
    assert current["rows"][0]["wo_number"]=="REPEAT-C"
    assert current["rows"][0]["status"]=="WORKED"
    assert "Additional proactive repair performed" in current["rows"][0]["remarks"]
    assert "Earlier adjustment" not in current["rows"][0]["remarks"]
    assert current["pm_count"]==0
    assets=client.get("/api/assets/status").json()
    assert assets["oos"]==[] and assets["rts"]==[]


def test_older_closed_wo_with_new_technician_update_is_not_new_rts(
    client,monkeypatch
):
    _include_update_timestamp(monkeypatch)
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    original=row("REPEAT-D",category="11",status="D",
                 started="2026-10-26 08:20",serviced="2026-10-26 09:10",
                 remarks="Original work completed")
    assert upload(client,[original]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    clock(monkeypatch,day=26,hour=19)
    s2=client.post("/api/shifts/start",json={"code":"S2"})
    assert s2.status_code==200,s2.text
    documented={**original,"WO Status":"C",
                "Update Date / Time":"2026-10-26 18:50",
                "Technician Remarks":"Follow-up work documented this evening"}
    assert upload(client,[documented]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    current=client.get("/api/reporting/state").json()
    assert len(current["rows"])==1,current["rows"]
    assert current["rows"][0]["status"]=="WORKED"
    assert "Follow-up work documented" in current["rows"][0]["remarks"]
    assert current["pm_count"]==0
    assert client.get("/api/assets/status").json()["rts"]==[]


def test_admin_only_update_does_not_create_fake_technician_activity(
    client,monkeypatch
):
    _include_update_timestamp(monkeypatch)
    clock(monkeypatch,day=26,hour=19)
    enter(client,"S2")
    metadata=row("ADMIN-ONLY",category="11",status="C",
                 started="2026-10-26 08:10",serviced="2026-10-26 08:50",
                 remarks="")
    metadata["Update Date / Time"]="2026-10-26 18:40"
    assert upload(client,[metadata]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    assert client.get("/api/reporting/state").json()["rows"]==[]
