"""Cetaris dropdown coverage using synthetic exports only.

C/D/I/W/O/P are the six selected WO statuses. All ten Primary RFR codes
are included without treating every WO as physical OOS/RTS.
"""
from datetime import datetime

from v31_cetaris_codes import (
    WO_STATUSES, RFR_CATEGORIES, normalize_wo_status, normalize_primary_rfr
)
from v31_cetaris import select_records
import v31_breakdown
import app as m
from test_reporting import client, clock, enter, upload, import_techs, row


def test_all_dropdown_codes_and_descriptions_are_normalized():
    for code, name in WO_STATUSES.items():
        assert normalize_wo_status(code) == (name, code)
        assert normalize_wo_status(name) == (name, code)
        assert normalize_wo_status(f"{name}; {code}") == (name, code)
    for code, name in RFR_CATEGORIES.items():
        assert normalize_primary_rfr(code) == (name, code)
        assert normalize_primary_rfr(name) == (name, code)
        assert normalize_primary_rfr(f"{code} - {name}") == (name, code)
    assert normalize_wo_status("To be Done")[1] == ""
    assert normalize_wo_status("Unclosed")[1] == ""
    assert normalize_wo_status("Void")[1] == ""


def test_all_ten_primary_rfr_categories_and_six_wo_statuses_import(
    client, monkeypatch
):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    rows = []
    for code, rfr in RFR_CATEGORIES.items():
        for status_code, status in WO_STATUSES.items():
            wo = f"CODE-{code}-{status_code}"
            example = row(wo, category=code, status=status_code,
                          started="2026-10-26 07:45",
                          serviced=("2026-10-26 08:05" if status_code in ("C", "D") else None),
                          remarks=f"Recorded {rfr} work in {status} state")
            rows.append(example)
    staged = upload(client, rows)
    assert staged.status_code == 200, staged.text
    imported = import_techs(client, ["Synthetic A"])
    assert imported.status_code == 200, imported.text
    state = client.get("/api/reporting/state").json()
    # Two true completed Preventive Maintenance WOs go to PMs only;
    # other categories and in-flight PM work are still represented.
    assert state["pm_count"] == 2
    happenings = {r["wo_number"]:r for r in state["rows"]}
    assert len(happenings) == 58
    for code, rfr in RFR_CATEGORIES.items():
        for status_code, status in WO_STATUSES.items():
            wo = f"CODE-{code}-{status_code}"
            if code == "08" and status_code in ("C", "D"):
                assert wo not in happenings
                continue
            assert happenings[wo]["category"] == rfr
            assert status in ("Closed","Completed","In Process",
                              "Manager Review Required","Outside Service",
                              "Parts Pending")
    assert happenings["CODE-11-P"]["status"] == "PARTS_PENDING"
    assert happenings["CODE-22-W"]["status"] == "MANAGER_REVIEW"
    assert happenings["CODE-44-O"]["status"] == "OUTSIDE_SERVICE"
    assert happenings["CODE-11-C"]["status"] == "CLOSED"
    # A routine Closed, Completed, or Parts Pending WO is not physical RTS.
    assert client.get("/api/assets/status").json()["rts"] == []


def test_breakdown_parts_pending_to_in_process_then_closed_requires_rts(
    client, monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    s1=enter(client,"S1")
    waiting=row("P-WO",status="P",category="01",
                started="2026-10-26 09:00",serviced=None,
                remarks="Waiting for replacement contactor")
    assert upload(client,[waiting]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    with m._db() as conn:
        incident=conn.execute("SELECT status FROM breakdown_state WHERE wo_number='P-WO'").fetchone()
        assert incident["status"]=="parts pending"
    assert client.get("/api/assets/status").json()["rts"]==[]

    # New daypart's daily export; same WO, no monthly file and no duplicate.
    clock(monkeypatch,day=26,hour=18)
    response=client.post("/api/shifts/start",json={"code":"S2"})
    assert response.status_code==200,response.text
    s2=response.json()["id"]
    active=row("P-WO",status="I",category="01",
               started="2026-10-26 17:30",serviced=None,
               remarks="Replacement installed; functional testing in progress")
    assert upload(client,[active]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    with m._db() as conn:
        incident=conn.execute("SELECT status FROM breakdown_state WHERE wo_number='P-WO'").fetchone()
        assert incident["status"]=="in process"
        assert v31_breakdown.pending(conn,s2)==[]

    # Cetaris Closed does not establish physical RTS.
    closed=row("P-WO",status="C",category="01",
               started="2026-10-26 17:30",serviced="2026-10-26 18:10",
               remarks="Repairs and tests completed")
    assert upload(client,[closed]).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    with m._db() as conn:
        assert v31_breakdown.pending(conn,s1)==[]
        assert [x["status"] for x in v31_breakdown.pending(conn,s2)]==["closed"]
    assert client.get("/api/assets/status").json()["rts"]==[]
    result=client.post("/api/breakdowns/P-WO/decision",json={"decision":"rts"})
    assert result.status_code==200,result.text
    assert result.json()["resolved"] is True
    assert client.get("/api/assets/status").json()["oos"] == []
    assert [x["asset"] for x in client.get("/api/assets/status").json()["rts"]] == ["ASSETP-WO"]
