"""Proactive Repair (and other non-Breakdown) WOs that are still 'in process'
in Cetaris used to be silently dropped from the shift report entirely until
Closed -- an asset could sit in the shop for days with zero visibility.
They now surface as their own IN_PROCESS status, distinct from the Breakdown
OOS board, until management explicitly approves one onto the live OOS board.
"""
import pytest
from fastapi.testclient import TestClient
import app as m
import v31_roster
from test_reporting import clock, enter, row, upload, import_techs


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    with TestClient(m.app) as c:
        yield c


def test_proactive_repair_in_process_surfaces_instead_of_vanishing(client, monkeypatch):
    clock(monkeypatch)
    enter(client)
    records = [row("PR500", category="Proactive Repair", status="In Process",
                    remarks="Pulled for belt replacement")]
    assert upload(client, records).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200

    data = client.get("/api/reporting/state").json()
    found = next((x for x in data["rows"] if x["wo_number"] == "PR500"), None)
    assert found is not None, "In-process Proactive Repair WO must not vanish"
    assert found["status"] == "IN_PROCESS"

    # Must not be auto-added to the live OOS board -- that still requires
    # an explicit management approval.
    oos = client.get("/api/oos").json()
    assert not any(o["asset"] == "ASSETPR500" for o in oos["open"])


def test_breakdown_in_process_still_goes_straight_to_oos_unaffected(client, monkeypatch):
    """Guard against regressing the existing, unrelated Breakdown auto-OOS path."""
    clock(monkeypatch)
    enter(client)
    records = [row("BRK1", category="Breakdown", status="In Process",
                    remarks="Asset offline")]
    assert upload(client, records).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    data = client.get("/api/reporting/state").json()
    found = next(x for x in data["rows"] if x["wo_number"] == "BRK1")
    assert found["status"] == "OOS"


def test_management_can_approve_in_process_onto_oos_board(client, monkeypatch):
    clock(monkeypatch)
    enter(client)
    records = [row("PR501", category="Proactive Repair", status="In Process",
                    remarks="In the shop for inspection")]
    upload(client, records)
    import_techs(client, ["Synthetic A"])

    approved = client.post("/api/reporting/work-orders/PR501/approve-oos")
    assert approved.status_code == 200, approved.text

    data = client.get("/api/reporting/state").json()
    found = next(x for x in data["rows"] if x["wo_number"] == "PR501")
    assert found["status"] == "OOS"

    oos = client.get("/api/oos").json()
    assert any(o["asset"] == "ASSETPR501" for o in oos["open"])


def test_approve_oos_rejects_missing_or_already_resolved_wo(client, monkeypatch):
    clock(monkeypatch)
    enter(client)
    missing = client.post("/api/reporting/work-orders/NOPE/approve-oos")
    assert missing.status_code == 404

    records = [row("PR502", category="Proactive Repair", status="Closed")]
    upload(client, records)
    import_techs(client, ["Synthetic A"])
    not_in_process = client.post("/api/reporting/work-orders/PR502/approve-oos")
    assert not_in_process.status_code == 409


def test_regular_mst_cannot_approve_in_process_onto_oos_board(client, tmp_path, monkeypatch):
    import csv
    clock(monkeypatch)
    with (tmp_path / "mst-roster.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Assoc. #", "Shift", "Job Title"])
        w.writerow(["Regular Tech", "555003", "S1", "MST"])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", force=True)

    assert client.post("/api/login", json={"associate": "555003"}).status_code == 200
    assert client.post("/api/shifts/start", json={"code": "S1"}).status_code == 200
    records = [row("PR503", category="Proactive Repair", status="In Process")]
    upload(client, records)
    import_techs(client, ["Regular Tech"])

    denied = client.post("/api/reporting/work-orders/PR503/approve-oos")
    assert denied.status_code == 403
