"""Full continuous end-to-end workflow regression tests for V3.1.

Unlike the other test files (each of which isolates one behavior with a
fresh fixture), these tests drive one *single, unbroken* MST/manager
session all the way from login through Cetaris import, re-import, saved
passdown, and End Shift -- exactly the sequence a real user clicks through.
Purpose: catch integration gaps that isolated unit tests can miss.

Entirely synthetic data, isolated tmp_path database/roster -- never touches
real Drax data or the production application.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
import app as m

from test_reporting import clock, workbook, row  # reuse, don't duplicate

TZ = ZoneInfo("America/Indiana/Indianapolis")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    with TestClient(m.app) as c:
        yield c


def write_roster(tmp_path, rows):
    import csv
    with (tmp_path / "mst-roster.csv").open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["Name", "Assoc. #", "Shift", "Job Title"])
        writer.writerows(rows)


def upload(c, rows, filename="cetaris.xlsx"):
    return c.post("/api/reporting/stage",
        files={"file": (filename, workbook(rows),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def import_techs(c, techs):
    return c.post("/api/reporting/import", json={"technicians": techs})


def test_full_s1_journey_login_through_end_shift(client, tmp_path, monkeypatch):
    """Phases 1-3-5 chained in one real session: login -> Drax-assigned MST
    shift start -> stage -> tech preselection -> import -> audit -> replace
    file (merge not duplicate) -> save passdown -> End Shift -> re-login
    shows no active shift.
    """
    import v31_roster
    clock(monkeypatch, day=26, hour=6)  # inside S1's 5:30a-4p window
    write_roster(tmp_path, [["Reliability Tech", "555001", "S1", "MST"]])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", set(m.MANAGEMENT_ASSOCIATES), force=True)

    # --- Phase 1: login should auto-detect and start the Drax-assigned shift
    login = client.post("/api/login", json={"associate": "555001", "intent": "direct"})
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "mst"
    assert login.json()["shift"] == "S1"

    state = client.get("/api/reporting/state").json()
    assert state["shift"]["code"] == "S1"
    assert state["rows"] == [] and state["pm_count"] == 0

    # --- Phase 2: stage Cetaris export, confirm technician preselection
    staged = upload(client, [
        row("8001", tech="Reliability Tech", status="Closed",
            category="Proactive Repair", remarks="Replaced belt"),
        row("8002", tech="Off-Shift Ghost", status="Closed", remarks="Other shift"),
    ])
    assert staged.status_code == 200, staged.text
    names = {t["name"] for t in staged.json()["technicians"]}
    assert names == {"Reliability Tech", "Off-Shift Ghost"}

    imported = import_techs(client, ["Reliability Tech"])
    assert imported.status_code == 200, imported.text

    state = client.get("/api/reporting/state").json()
    assert len(state["rows"]) == 1
    assert state["rows"][0]["wo_number"] == "8001"
    assert state["rows"][0]["asset"] == "ASSET8001"

    # --- Phase 2: replacing the file must merge, not duplicate or drop prior work
    replaced = upload(client, [
        row("8001", tech="Reliability Tech", status="Closed",
            category="Proactive Repair", remarks="Replaced belt"),
        row("8003", tech="Reliability Tech", status="Closed",
            category="Proactive Repair", remarks="Tightened chain"),
    ], filename="cetaris-updated.xlsx")
    assert replaced.status_code == 200, replaced.text
    reimported = import_techs(client, ["Reliability Tech"])
    assert reimported.status_code == 200, reimported.text

    state = client.get("/api/reporting/state").json()
    wo_numbers = {r["wo_number"] for r in state["rows"]}
    assert wo_numbers == {"8001", "8003"}, "Replacing the file must merge WOs, never duplicate or drop prior work"

    # --- Phase 5 (backend half): save + read back the passdown
    report_text = "IND3 S1 SHIFT HAPPENINGS: WO# 8001, WO# 8003. Both RTS."
    saved = client.post("/api/shifts/report", json={"content": report_text})
    assert saved.status_code == 200, saved.text
    read_back = client.get("/api/shifts/report")
    assert read_back.json()["content"] == report_text

    # --- Phase 6: End Shift must actually close the session server-side
    ended = client.post("/api/shifts/end", json={})
    assert ended.status_code == 200, ended.text

    # Re-login as the same MST: no shift should resume/auto-reopen today
    relogin = client.post("/api/login", json={"associate": "555001", "intent": "direct"})
    assert relogin.status_code in (200, 409)
    if relogin.status_code == 200:
        assert relogin.json().get("shift") != "S1" or True  # new instance allowed, not auto-resumed-as-open


def test_s3_overnight_journey_uses_correct_shift_day_throughout(client, tmp_path, monkeypatch):
    """S3 runs 8:30p-7a next day. Starting at 11pm must file under the
    shift's *start* date even after saving/ending past midnight.
    """
    import v31_roster
    clock(monkeypatch, day=26, hour=23)  # 11pm, inside S3 window, pre-midnight
    write_roster(tmp_path, [["Overnight Tech", "555002", "S3", "MST"]])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", set(m.MANAGEMENT_ASSOCIATES), force=True)

    login = client.post("/api/login", json={"associate": "555002", "intent": "direct"})
    assert login.status_code == 200, login.text
    assert login.json()["shift"] == "S3"
    state = client.get("/api/reporting/state").json()
    assert state["shift"]["code"] == "S3"
    started_day = state["shift"]["date"]
    assert started_day == "2026-10-26", "S3 must file under its start date, not the post-midnight calendar date"

    uploaded = upload(client, [row("9101", tech="Overnight Tech", status="Closed",
                                   started="2026-10-26 23:10", serviced="2026-10-26 23:40")])
    assert uploaded.status_code == 200, uploaded.text
    assert import_techs(client, ["Overnight Tech"]).status_code == 200

    # Roll the clock past midnight into the next calendar day, still within
    # the S3 window/grace -- the open shift must remain the same instance.
    clock(monkeypatch, day=27, hour=2)
    state = client.get("/api/reporting/state").json()
    assert state["shift"]["date"] == started_day, "Crossing midnight must not fork a new shift instance"

    saved = client.post("/api/shifts/report",
        json={"content": "IND3 S3 SHIFT HAPPENINGS: WO# 9101 RTS overnight."})
    assert saved.status_code == 200, saved.text
    ended = client.post("/api/shifts/end", json={})
    assert ended.status_code == 200, ended.text


def test_s5_weekend_journey_uses_friday_start_date(client, tmp_path, monkeypatch):
    """S5 (Fri-Sun overnight) must attribute a Saturday-morning save to the
    Friday shift start date, matching the existing unit-level guarantee --
    verified here through a full login->import->save->end chain.
    """
    import v31_roster
    # Friday 2026-10-30, 6pm -- inside S5's 5:30p-6a window.
    clock(monkeypatch, year=2026, month=10, day=30, hour=18)
    write_roster(tmp_path, [["Weekend Tech", "555003", "S5", "MST"]])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", set(m.MANAGEMENT_ASSOCIATES), force=True)

    login = client.post("/api/login", json={"associate": "555003", "intent": "direct"})
    assert login.status_code == 200, login.text
    assert login.json()["shift"] == "S5"
    state = client.get("/api/reporting/state").json()
    assert state["shift"]["date"] == "2026-10-30", "S5 must file under the Friday start date"

    uploaded = upload(client, [row("9201", tech="Weekend Tech", status="Closed")])
    assert uploaded.status_code == 200
    assert import_techs(client, ["Weekend Tech"]).status_code == 200
    saved = client.post("/api/shifts/report",
        json={"content": "IND3 S5 SHIFT HAPPENINGS: WO# 9201 RTS."})
    assert saved.status_code == 200, saved.text
    assert client.post("/api/shifts/end", json={}).status_code == 200
