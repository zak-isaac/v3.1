"""Field-style V3.1 simulation: FIVE shifts, daily exports, one shared DB.

All assets/technicians/WO numbers are synthetic. No production workspace used.
This module deliberately tests OUTCOMES across application APIs rather than
assuming that a green parser/unit test proves the passdown is correct.
"""
import app as m
import v31_breakdown
from test_reporting import client, clock, enter, upload, import_techs, row


def _ingest(c, items):
    loaded=upload(c,items)
    assert loaded.status_code==200,loaded.text
    imported=import_techs(c,["Synthetic A"])
    assert imported.status_code==200,imported.text
    return c.get("/api/reporting/state").json()


def _active(c, wo):
    items=c.get("/api/assets/status").json()["active_breakdowns"]
    return next((r for r in items if r["wo_number"]==wo),None)


def test_sim_weekday_s1_s2_s3_single_wo_preserves_ownership_and_email(
    client, monkeypatch
):
    # Monday: S1 discovers motor fault; asset is actually taken OOS.
    clock(monkeypatch,day=26,hour=10)
    s1=enter(client,"S1")
    a=row("SIM-MOTOR",category="01",status="P",
          started="2026-10-26 08:25",serviced=None,
          remarks="Motor damaged; awaiting replacement")
    _ingest(client,[a])
    down=client.post("/api/oos",json={"asset":"ASSETSIM-MOTOR","cause":"Motor locked out"})
    assert down.status_code==200,down.text
    assert _active(client,"SIM-MOTOR")["status"]=="parts pending"

    # Monday evening: S2 works on same WO without claiming repair completion.
    clock(monkeypatch,day=26,hour=19)
    s2=client.post("/api/shifts/start",json={"code":"S2"})
    assert s2.status_code==200,s2.text
    s2_id=s2.json()["id"]
    b=row("SIM-MOTOR",category="Breakdown",status="I",
          started="2026-10-26 18:10",serviced=None,
          remarks="Replacement installed; additional checks required")
    state=_ingest(client,[b])
    assert state["rows"][0]["status"]=="IN_PROGRESS"
    assert _active(client,"SIM-MOTOR")["status"]=="in process"
    assert client.get("/api/assets/status").json()["rts"]==[]

    # S3 starts Monday overnight and completes the work after midnight Tue.
    clock(monkeypatch,day=27,hour=2)
    s3=client.post("/api/shifts/start",json={"code":"S3"})
    assert s3.status_code==200,s3.text
    s3_id=s3.json()["id"]
    c=row("SIM-MOTOR",category="01",status="D",
          started="2026-10-27 00:30",serviced="2026-10-27 01:15",
          remarks="Tested motor; production operation restored")
    state=_ingest(client,[c])
    assert state["rows"][0]["status"]=="COMPLETED"
    with m._db() as conn:
        assert v31_breakdown.pending(conn,s1)==[]
        assert v31_breakdown.pending(conn,s2_id)==[]
        assert [x["wo_number"] for x in v31_breakdown.pending(conn,s3_id)]==[
            "SIM-MOTOR"
        ]
    assert client.get("/api/assets/status").json()["rts"]==[]
    result=client.post("/api/breakdowns/SIM-MOTOR/decision",
                       json={"decision":"rts"})
    assert result.status_code==200,result.text
    assets=client.get("/api/assets/status").json()
    assert assets["active_breakdowns"]==[]
    assert assets["oos"]==[]
    assert [x["wo_number"] for x in assets["breakdowns"]]==["SIM-MOTOR"]
    eml=client.get("/api/shifts/report/preview")
    assert eml.status_code==200,eml.text
    html=eml.json()["html"]
    section=html.split(">Returned to Service</strong>",1)[1].split(
        ">Current Out of Service</strong>",1)[0]
    assert "ASSETSIM-MOTOR" in section
    with m._db() as conn:
        shifts={r["shift_id"]:r["status"] for r in conn.execute(
            "SELECT shift_id,status FROM report_wo WHERE wo_number='SIM-MOTOR'")}
        assert shifts=={s1:"PARTS_PENDING",s2_id:"IN_PROGRESS",s3_id:"COMPLETED"}
        events=list(conn.execute("SELECT shift_id FROM breakdown_rts_events"))
        assert len(events)==1 and events[0]["shift_id"]==s3_id

    # Later monthly-style upload with an old S1 row cannot re-open the WO.
    _ingest(client,[a,c])
    with m._db() as conn:
        r=conn.execute(
            "SELECT status,rts_logged FROM breakdown_state WHERE wo_number='SIM-MOTOR'"
        ).fetchone()
        assert r["rts_logged"]==1
        assert conn.execute(
            "SELECT COUNT(*) FROM breakdown_rts_events WHERE wo_number='SIM-MOTOR'"
        ).fetchone()[0]==1
    assert client.get("/api/assets/status").json()["pending"]==[]


def test_sim_weekend_s4_s5_overnight_parts_and_verified_rts(client,monkeypatch):
    clock(monkeypatch,day=24,hour=10)
    s4=enter(client,"S4")
    work=row("SIM-CONVEYOR",category="01",status="P",
             started="2026-10-24 09:00",serviced=None,
             remarks="Drive damaged; replacement ordered")
    _ingest(client,[work])
    assert _active(client,"SIM-CONVEYOR")["status"]=="parts pending"
    # S5 shift starts Saturday evening; completion happens Sunday morning.
    clock(monkeypatch,day=25,hour=2)
    res=client.post("/api/shifts/start",json={"code":"S5"})
    assert res.status_code==200,res.text
    s5=res.json()["id"]
    repaired=row("SIM-CONVEYOR",category="01",status="D",
                 started="2026-10-25 00:20",serviced="2026-10-25 01:05",
                 remarks="New drive installed and tested")
    _ingest(client,[repaired])
    with m._db() as conn:
        assert v31_breakdown.pending(conn,s4)==[]
        assert [r["wo_number"] for r in v31_breakdown.pending(conn,s5)]==[
            "SIM-CONVEYOR"
        ]
    resolved=client.post("/api/breakdowns/SIM-CONVEYOR/decision",
                         json={"decision":"rts"})
    assert resolved.status_code==200,resolved.text
    with m._db() as conn:
        assert conn.execute(
            "SELECT shift_id FROM breakdown_rts_events WHERE wo_number='SIM-CONVEYOR'"
        ).fetchone()["shift_id"]==s5


def test_sim_vendor_outside_service_completed_unclosed_and_later_closed(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    sent=row("SIM-VENDOR",category="Breakdown",status="O",
             started="2026-10-26 09:10",serviced=None,
             remarks="Vendor dispatched for equipment repair")
    _ingest(client,[sent])
    assert _active(client,"SIM-VENDOR")["status"]=="outside service"
    assert client.get("/api/assets/status").json()["rts"]==[]

    clock(monkeypatch,day=26,hour=19)
    s2=client.post("/api/shifts/start",json={"code":"S2"})
    assert s2.status_code==200,s2.text
    s2_id=s2.json()["id"]
    finished=row("SIM-VENDOR",category="Breakdown",status="D",
                 started="2026-10-26 17:20",serviced="2026-10-26 17:40",
                 remarks="Vendor repaired equipment; invoice outstanding")
    state=_ingest(client,[finished])
    assert state["rows"][0]["status"]=="COMPLETED"
    assert _active(client,"SIM-VENDOR")["status"]=="completed"
    assert client.get("/api/assets/status").json()["rts"]==[]
    marked=client.post("/api/breakdowns/SIM-VENDOR/decision",json={"decision":"rts"})
    assert marked.status_code==200,marked.text
    assert client.get("/api/assets/status").json()["active_breakdowns"]==[]
    assert len(client.get("/api/assets/status").json()["rts"])==1
    with m._db() as conn:
        assert conn.execute("SELECT status FROM breakdown_state WHERE wo_number='SIM-VENDOR'").fetchone()["status"]=="completed"
    # Event remains unique if Cetaris closes later (invoice payment).
    closed=row("SIM-VENDOR",category="Breakdown",status="C",
               started="2026-10-26 17:20",serviced="2026-10-26 17:40",
               remarks="Administrative closure after vendor invoice")
    state=_ingest(client,[closed])
    assert state["rows"][0]["status"]=="CLOSED"
    with m._db() as conn:
        ev=conn.execute("SELECT shift_id FROM breakdown_rts_events WHERE wo_number='SIM-VENDOR'").fetchall()
        assert len(ev)==1 and ev[0]["shift_id"]==s2_id
    assert client.get("/api/assets/status").json()["active_breakdowns"]==[]


def test_sim_keep_oos_blocks_direct_rts_and_reopens_after_new_completion(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    item=row("SIM-HOLD",category="01",status="D",
             started="2026-10-26 09:00",serviced="2026-10-26 09:35",
             remarks="Testing failed; equipment still unavailable")
    _ingest(client,[item])
    keep=client.post("/api/breakdowns/SIM-HOLD/decision",json={"decision":"keep_oos"})
    assert keep.status_code==200,keep.text
    assert any(x["wo_number"]=="SIM-HOLD" for x in client.get(
        "/api/assets/status").json()["active_breakdowns"])
    assert any(x["asset"]=="ASSETSIM-HOLD" for x in client.get(
        "/api/assets/status").json()["oos"])
    shortcut=client.post("/api/oos/rts",json={"asset":"ASSETSIM-HOLD"})
    assert shortcut.status_code==409
    # Identical upload must not re-open the review dialog.
    _ingest(client,[item])
    assert client.get("/api/assets/status").json()["pending"]==[]
    # Later shift does genuine new work: new review allowed.
    clock(monkeypatch,day=26,hour=18)
    s2=client.post("/api/shifts/start",json={"code":"S2"})
    assert s2.status_code==200,s2.text
    fixed=row("SIM-HOLD",category="01",status="D",
              started="2026-10-26 17:25",serviced="2026-10-26 17:50",
              remarks="Replaced sensor and verified operation")
    _ingest(client,[fixed])
    assert [x["wo_number"] for x in client.get(
        "/api/assets/status").json()["pending"]]==["SIM-HOLD"]
    rts=client.post("/api/breakdowns/SIM-HOLD/decision",json={"decision":"rts"})
    assert rts.status_code==200,rts.text
    assert client.get("/api/assets/status").json()["oos"]==[]


def test_sim_all_rfr_types_do_not_create_false_breakdowns(client,monkeypatch):
    from v31_cetaris_codes import RFR_CATEGORIES
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    rows=[row("SIM-RFR-"+code,category=code,status="I",
              started="2026-10-26 08:05",serviced=None,
              remarks="Recorded service work")
          for code in RFR_CATEGORIES]
    report=_ingest(client,rows)
    assert {x["category"] for x in report["rows"]}==set(RFR_CATEGORIES.values())
    assets=client.get("/api/assets/status").json()
    assert len(assets["active_breakdowns"])==1
    assert assets["active_breakdowns"][0]["wo_number"]=="SIM-RFR-01"
    assert assets["oos"]==[] and assets["rts"]==[]
    assert report["pm_count"]==0


def test_sim_no_missing_today_wo_cannot_auto_close_prior_breakdown(
    client,monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    old=row("SIM-OLD",category="01",status="P",
            started="2026-10-26 08:00",serviced=None)
    _ingest(client,[old])
    clock(monkeypatch,day=26,hour=19)
    s2=client.post("/api/shifts/start",json={"code":"S2"})
    assert s2.status_code==200,s2.text
    unrelated=row("SIM-OTHER",category="11",status="C",
                  started="2026-10-26 17:15",serviced="2026-10-26 17:40")
    _ingest(client,[unrelated])
    live=client.get("/api/assets/status").json()
    assert any(x["wo_number"]=="SIM-OLD" and x["status"]=="parts pending"
               for x in live["active_breakdowns"])
    assert live["pending"]==[]
