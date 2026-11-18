"""Full V3.1 workflow integration with synthetic data only.

Covers real connections between import/shift attribution, repair tracking,
manual OOS/RTS, asset dashboard and generated passdown. Never uses live DB.
"""
import sqlite3

import app as m
import v31_breakdown
from test_reporting import client, clock, enter, upload, import_techs, row


def test_daily_import_to_oos_to_completed_rts_to_closed_is_consistent(
    client, monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    s1=enter(client,"S1")
    waiting=row("FULL-WO",category="01",status="P",
                started="2026-10-26 08:00",serviced=None,
                remarks="Replacement bearing on order")
    assert upload(client,[waiting]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    state=client.get("/api/reporting/state").json()
    assert state["rows"][0]["status"]=="PARTS_PENDING"
    assets=client.get("/api/assets/status").json()
    assert len(assets["active_breakdowns"])==1
    assert assets["active_breakdowns"][0]["wo_number"]=="FULL-WO"
    assert assets["oos"]==[]  # Parts Pending alone is not a physical OOS claim.

    # Explicit equipment OOS is distinct from the Cetaris work status.
    down=client.post("/api/oos",json={"asset":"ASSETFULL-WO",
                                      "cause":"Equipment isolated for repair"})
    assert down.status_code==200,down.text
    manual_id=down.json()["id"]
    assert [r["asset"] for r in client.get("/api/assets/status").json()["oos"]]==[
        "ASSETFULL-WO"
    ]

    # A direct manual RTS cannot bypass an unresolved Breakdown WO.
    bypass=client.post(f"/api/oos/{manual_id}/rts",json={})
    assert bypass.status_code==409
    assert client.get("/api/oos").json()["open"]

    clock(monkeypatch,day=26,hour=18)
    created=client.post("/api/shifts/start",json={"code":"S2"})
    assert created.status_code==200,created.text
    s2=created.json()["id"]

    # Today's export does not have to repeat yesterday's WO. The live
    # breakdown and OOS record are still available to the incoming shift.
    before=client.get("/api/assets/status").json()
    assert before["active_breakdowns"][0]["wo_number"]=="FULL-WO"
    assert before["active_breakdowns"][0]["reason"].startswith("Parts Pending")
    assert before["oos"][0]["asset"]=="ASSETFULL-WO"

    resumed=row("FULL-WO",category="Breakdown",status="I",
                started="2026-10-26 17:30",serviced=None,
                remarks="Parts installed; repair in progress")
    assert upload(client,[resumed]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    with m._db() as conn:
        st=conn.execute("SELECT status FROM breakdown_state WHERE wo_number='FULL-WO'").fetchone()
        assert st["status"]=="in process"
    report=client.get("/api/reporting/state").json()
    assert report["rows"][0]["status"]=="IN_PROGRESS"
    assert client.get("/api/assets/status").json()["oos"]

    completed=row("FULL-WO",category="Breakdown",status="D",
                  started="2026-10-26 17:30",serviced="2026-10-26 17:50",
                  remarks="Bearing replaced; function tested")
    assert upload(client,[completed]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    assert client.get("/api/reporting/state").json()["rows"][0]["status"]=="COMPLETED"
    with m._db() as conn:
        assert v31_breakdown.pending(conn,s1)==[]
        assert [x["wo_number"] for x in v31_breakdown.pending(conn,s2)]==["FULL-WO"]
    assert client.get("/api/assets/status").json()["rts"]==[]

    result=client.post("/api/breakdowns/FULL-WO/decision",json={"decision":"rts"})
    assert result.status_code==200,result.text
    assert result.json()["resolved"] is True
    after=client.get("/api/assets/status").json()
    assert after["active_breakdowns"]==[]
    assert after["oos"]==[]
    assert [x["asset"] for x in after["rts"]]==["ASSETFULL-WO"]
    assert [x["wo_number"] for x in after["breakdowns"]]==["FULL-WO"]
    assert client.get("/api/oos").json()["open"]==[]

    # Email/dashboard must share the same confirmed RTS source.
    preview=client.get("/api/shifts/report/preview")
    assert preview.status_code==200,preview.text
    html=preview.json()["html"]
    rts_section=html.split(">Returned to Service</strong>",1)[1].split(
        ">Current Out of Service</strong>",1)[0]
    assert "<strong>ASSETFULL-WO</strong>" in rts_section
    oos_section=html.split(">Current Out of Service</strong>",1)[1].split(
        "LOTO",1)[0]
    assert "ASSETFULL-WO" not in oos_section
    with m._db() as conn:
        old=conn.execute("SELECT status FROM report_wo WHERE shift_id=?",(s1,)).fetchone()
        new=conn.execute("SELECT status FROM report_wo WHERE shift_id=?",(s2,)).fetchone()
        assert old["status"]=="PARTS_PENDING"
        assert new["status"]=="COMPLETED"

    # Later administrative closure does not fabricate another RTS event.
    closed=row("FULL-WO",category="Breakdown",status="C",
               started="2026-10-26 17:30",serviced="2026-10-26 17:50",
               remarks="Work order administratively closed")
    assert upload(client,[closed]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    assert client.get("/api/reporting/state").json()["rows"][0]["status"]=="CLOSED"
    with m._db() as conn:
        events=conn.execute(
            "SELECT COUNT(*) FROM breakdown_rts_events WHERE wo_number='FULL-WO'"
        ).fetchone()[0]
        assert events==1


def test_primary_rfr_prevents_found_during_pm_from_becoming_a_completed_pm(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    finding=row("FIND-01",category="Found During PM",status="Completed",
                started="2026-10-26 08:30",serviced="2026-10-26 08:50",
                remarks="Discovered and repaired sensor issue",pm="RNPK.001.PKM")
    assert upload(client,[finding]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    state=client.get("/api/reporting/state").json()
    assert state["pm_count"]==0
    assert len(state["rows"])==1
    assert state["rows"][0]["category"]=="Found During PM"
    assert state["rows"][0]["status"]=="COMPLETED"


def test_breakdown_sync_only_updates_exact_attributed_labor_row():
    db=sqlite3.connect(":memory:")
    db.row_factory=sqlite3.Row
    try:
        wrong={"wo_number":"EXACT-1","asset_id":"ASSET-OLD",
               "status":"P","rfr":"01","tech_name":"Tech A",
               "start_at":"2026-10-25T08:00:00"}
        correct={**wrong,"asset_id":"ASSET-REAL",
                 "start_at":"2026-10-26T08:00:00"}
        changes=v31_breakdown.sync(db,[wrong,correct],{"Tech A"},[correct],1)
        assert changes==1
        recorded=db.execute("SELECT asset,status FROM breakdown_state WHERE wo_number='EXACT-1'").fetchone()
        assert recorded["asset"]=="ASSET-REAL"
        assert recorded["status"]=="parts pending"
    finally:
        db.close()
