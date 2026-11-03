"""Manually-reported Breakdown/OOS tracking: POST /api/oos, GET /api/oos,
POST /api/oos/{id}/rts.

Independent of Cetaris-imported work orders -- an MST can log an asset as
Down/Degraded with no WO behind it at all, it stays open across shifts
(global, not scoped to the logging shift), and any signed-in associate
(not just whoever logged it) can mark it RTS later, possibly from a
completely different shift.
"""
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
import app as m

from test_reporting import clock, enter

TZ = ZoneInfo("America/Indiana/Indianapolis")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    with TestClient(m.app) as c:
        yield c


def test_create_requires_an_active_shift(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    client.post("/api/login", json={"associate": "8972"})
    resp = client.post("/api/oos", json={"asset": "CONV-12", "cause": "Jammed belt"})
    assert resp.status_code == 409


def test_create_list_and_mark_rts_round_trip(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    created = client.post("/api/oos", json={
        "asset": "CONV-12", "cause": "Jammed belt", "notes": "Smoking slightly",
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["asset"] == "CONV-12"
    assert body["open"] == 1
    assert body["logged_by_name"]
    oos_id = body["id"]

    listed = client.get("/api/oos").json()
    assert any(r["id"] == oos_id for r in listed["open"])
    assert listed["resolved_this_shift"] == []

    resolved = client.post(f"/api/oos/{oos_id}/rts", json={"notes": "Belt replaced"})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["open"] == 0
    assert resolved.json()["rts_by_name"]

    listed_after = client.get("/api/oos").json()
    assert all(r["id"] != oos_id for r in listed_after["open"])
    assert any(r["id"] == oos_id for r in listed_after["resolved_this_shift"])


def test_cannot_mark_already_resolved_record_rts_again(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    oos_id = client.post("/api/oos", json={"asset": "CONV-12"}).json()["id"]
    assert client.post(f"/api/oos/{oos_id}/rts", json={}).status_code == 200
    again = client.post(f"/api/oos/{oos_id}/rts", json={})
    assert again.status_code == 409


def test_marking_nonexistent_record_rts_404s(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    assert client.post("/api/oos/999999/rts", json={}).status_code == 404


def test_different_associate_can_see_and_resolve_a_globally_open_record(client, tmp_path, monkeypatch):
    """The whole point: equipment logged down by one shift stays visible
    and resolvable by a completely different shift/day/associate."""
    import v31_roster
    clock(monkeypatch, day=26, hour=10)
    import csv
    with (tmp_path / "mst-roster.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Assoc. #", "Shift", "Job Title"])
        w.writerow(["First Tech", "555001", "S1", "MST"])
        w.writerow(["Second Tech", "555002", "S2", "MST"])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", set(m.MANAGEMENT_ASSOCIATES), force=True)

    assert client.post("/api/login", json={"associate": "555001", "intent": "direct"}).status_code == 200
    oos_id = client.post("/api/oos", json={"asset": "BALER-3", "cause": "Hydraulic leak"}).json()["id"]
    client.post("/api/logout", json={})

    clock(monkeypatch, day=26, hour=13)  # inside S2's window
    assert client.post("/api/login", json={"associate": "555002", "intent": "direct"}).status_code == 200
    listed = client.get("/api/oos").json()
    assert any(r["id"] == oos_id for r in listed["open"])
    resolved = client.post(f"/api/oos/{oos_id}/rts", json={})
    assert resolved.status_code == 200
    assert resolved.json()["rts_by_name"] == "Second Tech"
    assert resolved.json()["logged_by_name"] == "First Tech"


def test_resolved_manual_oos_appears_in_regenerated_historical_eml(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    shift_id = enter(client, "S1")
    oos_id = client.post("/api/oos", json={"asset": "CONV-12", "cause": "Jammed belt"}).json()["id"]
    assert client.post(f"/api/oos/{oos_id}/rts", json={}).status_code == 200
    saved = client.post("/api/shifts/report",
        json={"content": "IND3 S1 SHIFT HAPPENINGS: None."})
    assert saved.status_code == 200, saved.text
    ended = client.post("/api/shifts/end", json={})
    assert ended.status_code == 200, ended.text

    eml = client.get(f"/api/history/shifts/{shift_id}/eml")
    assert eml.status_code == 200
    assert "CONV-12" in eml.text
    assert "manually reported" in eml.text
