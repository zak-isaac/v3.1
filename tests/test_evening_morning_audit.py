"""S3/S5 Import Audit 'evening vs morning' completeness guide.

Confirmed with the product owner (2026-11-03): this is a quick sanity
count -- e.g. for a Friday S5 shift, 'Evening (before midnight): 10 WO,
Morning (after midnight): 9 WO' -- so an MST can eyeball that everything
they expect from the shift actually got pulled into the import, not a
precise attribution mechanism in its own right (that's v31_split.assign()'s
job). Not applicable to S1/S2/S4, which don't cross midnight.
"""
import pytest
from fastapi.testclient import TestClient
import app as m

from test_reporting import clock, enter, upload, import_techs, row


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    with TestClient(m.app) as c:
        yield c


def test_s5_friday_night_splits_work_by_midnight(client, monkeypatch):
    clock(monkeypatch, day=30, hour=21)  # Friday 9pm, inside S5's window
    enter(client, "S5")
    rows = [
        row("EVE1", tech="Synthetic A", started="2026-10-30 18:00", serviced="2026-10-30 18:20"),
        row("EVE2", tech="Synthetic A", started="2026-10-30 22:00", serviced="2026-10-30 22:30"),
        row("MORN1", tech="Synthetic A", started="2026-10-31 01:00", serviced="2026-10-31 01:20"),
        row("MORN2", tech="Synthetic A", started="2026-10-31 05:00", serviced="2026-10-31 05:30"),
    ]
    assert upload(client, rows).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    data = client.get("/api/reporting/state").json()
    assert {r["wo_number"] for r in data["rows"]} == {"EVE1", "EVE2", "MORN1", "MORN2"}
    em = data["audit"]["evening_morning"]
    assert em["applicable"] is True
    assert em["evening"] == 2
    assert em["morning"] == 2


def test_s1_day_shift_is_not_applicable(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    assert upload(client, [row("DAY1")]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    data = client.get("/api/reporting/state").json()
    assert data["audit"]["evening_morning"] == {"applicable": False}


def test_excluded_wo_does_not_count_toward_either_side(client, monkeypatch):
    clock(monkeypatch, day=30, hour=21)
    shift_id = enter(client, "S5")
    rows = [
        row("EVE1", tech="Synthetic A", started="2026-10-30 18:00", serviced="2026-10-30 18:20"),
        row("MORN1", tech="Synthetic A", started="2026-10-31 01:00", serviced="2026-10-31 01:20"),
    ]
    assert upload(client, rows).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    excluded = client.post("/api/reporting/work-orders/EVE1", json={"excluded": True})
    assert excluded.status_code == 200, excluded.text
    data = client.get("/api/reporting/state").json()
    em = data["audit"]["evening_morning"]
    assert em["evening"] == 0
    assert em["morning"] == 1
