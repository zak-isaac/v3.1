"""Proactive Repair (Primary RFR 11) must never vanish into PMs or filtering.

These represent real IND3 daily Cetaris exports; all asset/WO data is fake.
"""
import app as m
from test_reporting import client, clock, enter, upload, import_techs, row
from v31_cetaris_codes import WO_STATUSES
from v31_cetaris import select_records


def test_proactive_repair_each_six_wo_statuses_is_shift_happening(
    client, monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    records=[]
    for code in WO_STATUSES:
        records.append(row(
            f"PRO-11-{code}", category="11", status=code,
            started="2026-10-26 08:10",
            serviced="2026-10-26 08:45" if code in ("C","D") else None,
            remarks=f"Documented proactive repair status {code}",
            # Even with a PM-looking optional job code, Primary RFR=11
            # establishes that this is Proactive Repair, not RFR 08 PM.
            pm="RNPK.001.PKM",
        ))
    staged=upload(client,records)
    assert staged.status_code==200,staged.text
    imported=import_techs(client,["Synthetic A"])
    assert imported.status_code==200,imported.text
    report=client.get("/api/reporting/state").json()
    assert report["pm_count"]==0
    assert report["pms"]==[]
    by_wo={r["wo_number"]:r for r in report["rows"]}
    assert len(by_wo)==6,by_wo
    expected={"C":"CLOSED","D":"COMPLETED","I":"IN_PROGRESS",
              "W":"MANAGER_REVIEW","O":"OUTSIDE_SERVICE",
              "P":"PARTS_PENDING"}
    for code,status in expected.items():
        rec=by_wo[f"PRO-11-{code}"]
        assert rec["category"]=="Proactive Repair"
        assert rec["status"]==status
        assert f"Documented proactive repair status {code}" in rec["remarks"]
    assets=client.get("/api/assets/status").json()
    assert assets["rts"]==[]
    assert assets["oos"]==[]
    assert assets["active_breakdowns"]==[]
    preview=client.get("/api/shifts/report/preview")
    assert preview.status_code==200,preview.text
    html=preview.json()["html"]
    for code in expected:
        assert f"PRO-11-{code}" in html
    assert import_techs(client,["Synthetic A"]).status_code==200
    assert len(client.get("/api/reporting/state").json()["rows"])==6


def test_proactive_repair_completed_or_closed_without_in_service_is_worked_only(
    client, monkeypatch
):
    # Proactive repairs rarely take a machine out of production. Cetaris
    # may have NO physical "In Service" event; that must not erase technician
    # work, nor silently award someone credit for a separate WO completion.
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    records=[row(f"PRO-NORETURN-{code}",category="Proactive Repair",
                 status=code, started="2026-10-26 08:30",
                 serviced=None,remarks="Repaired noncritical sensor",
                 pm="E.001.BALER.WALK") for code in ("C","D")]
    assert upload(client,records).status_code==200
    assert import_techs(client,["Synthetic A"]).status_code==200
    report=client.get("/api/reporting/state").json()
    by_wo={r["wo_number"]:r for r in report["rows"]}
    assert set(by_wo)=={"PRO-NORETURN-C","PRO-NORETURN-D"}
    assert all(r["category"]=="Proactive Repair" and
               r["status"]=="WORKED" for r in by_wo.values())
    assert report["pm_count"]==0
    assert client.get("/api/assets/status").json()["rts"]==[]


def test_proactive_repair_closed_with_pm_code_is_not_counted_as_pm():
    base={"wo_number":"PROCODE","tech_name":"Tech One","asset_id":"RC0011",
          "rfr":"Proactive Repair","pm_code":"E.001.BALER.WALK",
          "remarks":"Adjusted photoeye","status":"Closed",
          "start_at":"2026-10-26T08:10:00","in_service_at":""}
    accepted,summary=select_records([base],{"Tech One"},None,None)
    assert len(accepted)==1
    assert accepted[0]["kind"]=="happening"
    assert summary["unique_pm_wos"]==0
