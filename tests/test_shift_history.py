"""Shift History (V3.1's own shift records, not the V2/V3 archive):
GET /api/history/shifts and GET /api/history/shifts/{id}/eml.

Full real-session flow like test_e2e_shift_workflow.py -- login, import,
save a draft (vendors/ro/notes/loto/missingKeys), save+resave a report,
end the shift, then confirm the history list and the regenerated .eml both
reflect what actually happened, scoped correctly per associate.
"""
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
import app as m

from test_reporting import clock, workbook, row

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


def run_full_shift(client, tmp_path, monkeypatch, associate, name, shift_code="S1", hour=6):
    import v31_roster
    clock(monkeypatch, day=26, hour=hour)
    write_roster(tmp_path, [[name, associate, shift_code, "MST"]])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", force=True)
    assert client.post("/api/login", json={"associate": associate, "intent": "direct"}).status_code == 200
    staged = client.post("/api/reporting/stage",
        files={"file": ("cetaris.xlsx", workbook([
            row("9001", tech=name, status="Closed", category="Proactive Repair", remarks="Replaced belt"),
        ]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert staged.status_code == 200, staged.text
    assert client.post("/api/reporting/import", json={"technicians": [name]}).status_code == 200
    draft = client.post("/api/reporting/draft", json={
        "vendors": ["ACME Conveyor Service"], "ro": ["Checked robot arm calibration"],
        "notes": "Quiet shift overall", "loto": True, "missingKeys": [],
    })
    assert draft.status_code == 200, draft.text
    saved = client.post("/api/shifts/report",
        json={"content": "IND3 S1 SHIFT HAPPENINGS: WO# 9001. RTS."})
    assert saved.status_code == 200, saved.text
    return saved


def test_closed_shift_appears_with_real_counts_and_eml_regenerates_faithfully(client, tmp_path, monkeypatch):
    run_full_shift(client, tmp_path, monkeypatch, "555001", "Reliability Tech")
    ended = client.post("/api/shifts/end",json={"acknowledge_warnings":True})
    assert ended.status_code == 200, ended.text
    shift_id = ended.json()["shift_id"]

    listing = client.get("/api/history/shifts").json()
    assert listing["total"] == 1
    entry = listing["rows"][0]
    assert entry["id"] == shift_id
    assert entry["code"] == "S1"
    assert entry["owner"] == "Reliability Tech"
    assert entry["happenings"] == 1
    assert entry["reports"] == 1
    assert entry["status"] == "Closed"
    assert entry["has_report"] is True

    eml = client.get(f"/api/history/shifts/{shift_id}/eml")
    assert eml.status_code == 200
    assert eml.headers["content-type"].startswith("message/rfc822")
    assert "attachment" in eml.headers["content-disposition"]
    body = eml.text
    assert "Subject: IND3 - S1 Shift Passdown" in body
    assert "ACME Conveyor Service" in body
    assert "Checked robot arm calibration" in body
    assert "Quiet shift overall" in body
    assert "WO# 9001" in body
    assert "LOTO Verified" in body


def test_resaving_report_increments_reports_count(client, tmp_path, monkeypatch):
    run_full_shift(client, tmp_path, monkeypatch, "555002", "Second Tech")
    again = client.post("/api/shifts/report",
        json={"content": "IND3 S1 SHIFT HAPPENINGS: WO# 9001. RTS. Updated."})
    assert again.status_code == 200, again.text
    entry = client.get("/api/history/shifts").json()["rows"][0]
    assert entry["reports"] == 2


def test_missing_loto_keys_show_in_regenerated_eml(client, tmp_path, monkeypatch):
    import v31_roster
    clock(monkeypatch, day=26, hour=6)
    write_roster(tmp_path, [["Third Tech", "555003", "S1", "MST"]])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", force=True)
    client.post("/api/login", json={"associate": "555003", "intent": "direct"})
    # Facility key inventory is now independent of shift LOTO verification.
    # A completed check with missing keys must display VERIFIED and a warning.
    for key in ("Key 2", "Key 4"):
        marked=client.post("/api/reporting/loto/missing-key",
                           json={"key":key,"missing":True})
        assert marked.status_code==200,marked.text
    draft=client.post("/api/reporting/draft", json={
        "vendors": [], "ro": [], "notes": "", "loto": True, "missingKeys": [],
    })
    assert draft.status_code==200,draft.text
    saved = client.post("/api/shifts/report", json={"content": "IND3 S1 SHIFT HAPPENINGS: None."})
    assert saved.status_code == 200, saved.text
    shift_id = saved.json()["shift_id"]
    eml = client.get(f"/api/history/shifts/{shift_id}/eml")
    assert "LOTO Verified" in eml.text
    assert "LOTO Verification Pending" not in eml.text
    assert "Missing keys:" in eml.text
    assert "Key 2" in eml.text and "Key 4" in eml.text


def test_ordinary_mst_sees_only_own_shifts_and_cannot_download_others(client, tmp_path, monkeypatch):
    run_full_shift(client, tmp_path, monkeypatch, "555001", "Reliability Tech")
    first_shift = client.post("/api/shifts/end",json={"acknowledge_warnings":True}).json()["shift_id"]

    # A different ordinary MST, a day later -- no need to start a shift of
    # their own to exercise the access-control boundary below.
    import v31_roster
    write_roster(tmp_path, [["Reliability Tech", "555001", "S1", "MST"],
                            ["Other Tech", "555004", "S1", "MST"]])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", force=True)
    client.post("/api/logout", json={})
    clock(monkeypatch, day=27, hour=6)
    assert client.post("/api/login", json={"associate": "555004", "intent": "direct"}).status_code == 200

    listing = client.get("/api/history/shifts").json()
    assert all(r["id"] != first_shift for r in listing["rows"])
    forbidden = client.get(f"/api/history/shifts/{first_shift}/eml")
    assert forbidden.status_code == 403


def test_management_sees_every_associates_shifts(client, tmp_path, monkeypatch):
    run_full_shift(client, tmp_path, monkeypatch, "555001", "Reliability Tech")
    client.post("/api/shifts/end",json={"acknowledge_warnings":True})
    client.post("/api/logout", json={})
    assert client.post("/api/login", json={"associate": "8972", "intent": "direct"}).status_code == 200
    listing = client.get("/api/history/shifts").json()
    assert listing["total"] == 1
    assert listing["rows"][0]["owner"] == "Reliability Tech"


def test_history_screen_and_script_wired_on_both_report_and_standalone_admin(client, tmp_path, monkeypatch):
    """The routed #v31-history-screen (not the old cramped dialog) and its
    script must be reachable both from a signed-in shift session and from
    the standalone /administration page management uses with no shift
    selected -- the earlier dialog version was only wired on one of them."""
    run_full_shift(client, tmp_path, monkeypatch, "555001", "Reliability Tech")
    report_page = client.get("/shift-report.html").text
    assert 'id="v31-history-screen"' in report_page
    assert 'id="v31-history-rows"' in report_page
    assert '/web/shift-history.js' in report_page

    client.post("/api/shifts/end",json={"acknowledge_warnings":True})
    client.post("/api/logout", json={})
    assert client.post("/api/login", json={"associate": "8972", "intent": "direct"}).status_code == 200
    admin_page = client.get("/administration").text
    assert 'id="v31-history-screen"' in admin_page
    assert '/web/shift-history.js' in admin_page
