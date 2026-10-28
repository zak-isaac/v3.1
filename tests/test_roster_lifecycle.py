"""V3.1 audit: Drax sync, management edits, OT, capacity, passdown/expiry.

Tests use isolated temporary files and synthetic records, never production data.
"""
import csv
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
import app as m
import v31_roster as roster_module

TZ = ZoneInfo("America/Indiana/Indianapolis")


def at(day=27, hour=13, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def freeze(monkeypatch, instant):
    original = datetime

    class Clock:
        combine = original.combine

        @staticmethod
        def now(tz):
            return instant

    monkeypatch.setattr(m, "datetime", Clock)


def write_roster(file, extra=None):
    rows = [
        ["Synthetic MST A", "111111", "a11", "S1", "MST"],
        ["Synthetic MST B", "222222", "b22", "S2", "MST"],
        ["Synthetic Associate C", "333333", "c33", "S1", "Associate"],
    ] + (extra or [])
    with file.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["Name", "Assoc. #", "Default ID", "Shift", "Job Title"])
        writer.writerows(rows)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "secret")
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    write_roster(tmp_path / "mst-roster.csv")
    with TestClient(m.app) as client:
        yield client


def sign(client, who="8972", intent="direct"):
    return client.post("/api/login", json={"associate":who, "intent":intent})


def test_drax_roster_sync_and_manager_visibility(client, monkeypatch):
    freeze(monkeypatch, at())
    assert sign(client).status_code == 200
    status = client.get("/api/admin/roster")
    assert status.status_code == 200
    data = status.json()
    assert data["entries"] == 3, data
    assert data["source"] == "mst-roster.csv"
    assert data["last_synced"]
    records = {r["number"]:r for r in data["associates"]}
    assert records["111111"]["is_mst"]
    assert not records["333333"]["is_mst"]
    assert sign(client,"333333").status_code == 401


def test_drax_reload_inserts_updates_and_disables_removed_rows(client, tmp_path, monkeypatch):
    freeze(monkeypatch, at())
    sign(client)
    write_roster(tmp_path/"mst-roster.csv",
                 extra=[["New MST", "444444", "d44", "S3", "MST"]])
    status = client.post("/api/admin/roster/reload", json={})
    assert status.status_code == 200, status.text
    assert any(row["number"]=="444444" for row in status.json()["associates"])
    # Remove a Drax user: their cached row becomes inactive, not login-authorized.
    path=tmp_path/"mst-roster.csv"
    with path.open("w",newline="") as out:
        writer=csv.writer(out)
        writer.writerow(["Name","Assoc. #","Default ID","Shift","Job Title"])
        writer.writerow(["New MST","444444","d44","S3","MST"])
    result=client.post("/api/admin/roster/reload",json={})
    assert result.status_code==200
    assert not next(x for x in result.json()["associates"] if x["number"]=="111111")["active"]
    assert sign(client,"111111").status_code == 401


def test_manager_edit_survives_drax_reload(client, monkeypatch):
    freeze(monkeypatch, at())
    sign(client)
    result = client.post("/api/admin/roster/override",
        json={"number":"333333","is_mst":True,"shift":"S1"})
    assert result.status_code == 200, result.text
    person = next(p for p in result.json()["associates"] if p["number"]=="333333")
    assert person["is_mst"] and person["overridden"]
    client.post("/api/admin/roster/reload",json={})
    assert "333333" in m.roster()
    assert m.roster()["333333"]["shift"] == "S1"
    reset=client.post("/api/admin/roster/override",
                      json={"number":"333333","reset":True})
    assert reset.status_code == 200
    assert "333333" not in m.roster()


def test_management_can_add_manual_mst_not_in_drax(client,monkeypatch):
    freeze(monkeypatch, at())
    sign(client)
    added=client.post("/api/admin/roster/add",
        json={"number":"555555","name":"Synthetic manual","shift":"S1","is_mst":True})
    assert added.status_code==200, added.text
    assert "555555" in m.roster()
    client.post("/api/admin/roster/reload",json={})
    assert "555555" in m.roster()
    assert client.post("/api/admin/roster/add",
        json={"number":"8972","name":"Should be denied","shift":"S1","is_mst":True}).status_code == 403


def test_regular_mst_cannot_use_management_endpoints(client,monkeypatch):
    freeze(monkeypatch, at())
    sign(client, "111111")
    assert client.get("/api/admin/roster").status_code == 403
    assert client.post("/api/admin/roster/reload",json={}).status_code == 403
    assert client.post("/api/admin/overtime/grant",
      json={"associate":"222222","code":"S1","shift_day":"2026-10-27"}).status_code == 403


def test_overtime_grant_and_revocation(client,monkeypatch):
    freeze(monkeypatch, at())
    sign(client)
    grant=client.post("/api/admin/overtime/grant",
       json={"associate":"222222","code":"S1","shift_day":"2026-10-27"})
    assert grant.status_code == 200, grant.text
    assert grant.json()["is_ot"] is True
    client.post("/api/logout")
    assert sign(client,"222222").status_code==200
    assert client.get("/api/me").json()["selected"]["code"]=="S2"
    result=client.post("/api/shifts/start",json={"code":"S1"})
    assert result.status_code==200, result.text
    assert result.json()["is_ot"] is True
    client.post("/api/logout")
    sign(client)
    revoke=client.post("/api/admin/overtime/revoke",
       json={"associate":"222222","code":"S1","shift_day":"2026-10-27"})
    assert revoke.status_code==200
    client.post("/api/logout")
    sign(client,"222222")
    assert client.post("/api/shifts/resume",json={"shift_id":result.json()["id"]}).status_code==403


def test_overtime_invalid_day_and_unknown_associate(client,monkeypatch):
    freeze(monkeypatch, at())
    sign(client)
    bad=client.post("/api/admin/overtime/grant",
       json={"associate":"222222","code":"S4","shift_day":"2026-10-27"})
    assert bad.status_code==422
    missing=client.post("/api/admin/overtime/grant",
       json={"associate":"999999","code":"S1","shift_day":"2026-10-27"})
    assert missing.status_code==404


def test_two_active_shift_limit_even_if_three_windows_overlap(client,monkeypatch):
    freeze(monkeypatch, at())
    # Test-only third eligible window, to exercise capacity independently
    # of normal S1/S2/S3 schedules (which never overlap in groups of three).
    before=m.SHIFT_RULES["S3"]
    monkeypatch.setitem(m.SHIFT_RULES,"S3",
       ("Test third concurrent", (12,0), (20,0), (0,1,2,3)))
    sign(client)
    assert client.post("/api/shifts/start",json={"code":"S1"}).status_code==200
    assert client.post("/api/shifts/start",json={"code":"S2"}).status_code==200
    denied=client.post("/api/shifts/start",json={"code":"S3"})
    assert denied.status_code==409
    state=client.get("/api/me").json()
    assert "S3" not in {r["code"] for r in state["ready"]}


def test_report_required_before_ending_and_saved_in_correct_shift(client,monkeypatch):
    freeze(monkeypatch, at())
    sign(client)
    s1=client.post("/api/shifts/start",json={"code":"S1"}).json()["id"]
    s2=client.post("/api/shifts/start",json={"code":"S2"}).json()["id"]
    assert client.post("/api/shifts/end",json={}).status_code==409
    report="IND3 S2 SHIFT HAPPENINGS: no work orders on this shift. Verified for passdown."
    saved=client.post("/api/shifts/report",json={"content":report})
    assert saved.status_code==200, saved.text
    assert client.get("/api/shifts/report").json()["content"]==report
    ended=client.post("/api/shifts/end",json={})
    assert ended.status_code==200, ended.text
    assert ended.json()["shift_id"]==s2
    with m._db() as db:
        rows={r["id"]:r for r in db.execute("SELECT * FROM shifts")}
        assert rows[s1]["open"]==1
        assert rows[s2]["open"]==0
        assert rows[s2]["closed_reason"]=="completed"
        assert rows[s2]["report_saved_at"]


def test_late_closure_auto_expires_and_preserves_unsubmitted_history(client,monkeypatch):
    freeze(monkeypatch, at(26, 21))
    sign(client)
    started=client.post("/api/shifts/start",json={"code":"S3"})
    assert started.status_code==200
    sid=started.json()["id"]
    assert client.post("/api/logout").status_code==200
    freeze(monkeypatch, at(27,8,1))
    assert client.get("/api/entry").status_code==200
    with m._db() as db:
        row=db.execute("SELECT * FROM shifts WHERE id=?",(sid,)).fetchone()
        assert row["open"]==0
        assert row["closed_reason"]=="window_expired"
        assert row["report_saved_at"]==""
    sign(client)
    assert client.post("/api/shifts/resume",json={"shift_id":sid}).status_code==404


def test_workbook_excel_source_format(client,tmp_path,monkeypatch):
    from openpyxl import Workbook
    workbook=Workbook(); ws=workbook.active
    ws.append(["Name","Assoc. #","Default ID","Shift","Job Title"])
    ws.append(["Spreadsheet MST",888888,"id8","S3","MST"])
    file=tmp_path/"Drax  Associates.xlsx"
    workbook.save(file)
    (tmp_path/"mst-roster.csv").unlink()
    roster_module.sync(tmp_path,tmp_path/"v31.sqlite3",set(m.MANAGEMENT_ASSOCIATES),force=True)
    assert m.roster()["888888"]["shift"]=="S3"


def test_service_and_ui_integration(client, monkeypatch):
    freeze(monkeypatch, at())
    sign(client)
    r=client.post("/api/shifts/start",json={"code":"S1"})
    assert r.status_code==200
    page=client.get("/shift-report.html")
    assert page.status_code==200
    assert 'roster-admin.js' in page.text
    assert 'workspace-auth.js' in page.text
    assert 'const sampleRows = []' in page.text
    assert client.get("/web/roster-admin.js").status_code==200
    assert client.get("/web/login-layout.css").status_code==200


def test_admin_name_edit_survives_drax_refresh_and_reset(client, monkeypatch):
    freeze(monkeypatch, at())
    sign(client)
    changed=client.post("/api/admin/roster/edit",
        json={"number":"111111","full_name":"Approved Display Name"})
    assert changed.status_code==200, changed.text
    assert m.roster()["111111"]["name"]=="Approved Display Name"
    assert client.post("/api/admin/roster/reload",json={}).status_code==200
    assert m.roster()["111111"]["name"]=="Approved Display Name"
    result=client.post("/api/admin/roster/override",
       json={"number":"111111","reset":True})
    assert result.status_code==200
    assert m.roster()["111111"]["name"]=="Synthetic MST A"
    assert client.post("/api/admin/roster/edit",
       json={"number":"111111","full_name":"New name","new_number":"123123"}).status_code==409


def test_roster_error_does_not_erase_cached_data(client,tmp_path,monkeypatch):
    freeze(monkeypatch, at())
    assert sign(client).status_code==200
    (tmp_path/"mst-roster.csv").unlink()
    status=client.post("/api/admin/roster/reload",json={})
    assert status.status_code==200
    assert "No local Drax roster" in status.json()["error"]
    # Cached data remains available for review, but is explicitly marked stale.
    assert status.json()["entries"]==3
