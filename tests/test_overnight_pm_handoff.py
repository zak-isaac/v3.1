"""Verify S3/S5 PM deadlines follow next day's incoming crew.

All rows are synthetic. The PM list always comes only from this shift's
single WO Details upload, not from prior files or a retained PM backlog.
"""
from test_reporting import client, clock, enter
from test_daily_pm_due_wo_details import stage_details, to_be_done


def test_s3_handoff_date_is_tuesday_even_when_staged_monday_night(
    client, monkeypatch
):
    clock(monkeypatch, day=26, hour=21)
    enter(client, "S3")
    response = stage_details(client, [
        to_be_done("S3-MONDAY-OLD", due="10/26/2026"),
        to_be_done("S3-TUESDAY-DUE", due="10/27/2026"),
        to_be_done("S3-WEDNESDAY", due="10/28/2026"),
    ])
    assert response.status_code == 200, response.text
    before = client.get("/api/pm-needing-completion").json()
    assert before["as_of_date"] == "2026-10-27"
    assert before["target_dates"] == ["2026-10-27"]
    assert [r["wo_number"] for r in before["rows"]] == ["S3-TUESDAY-DUE"]
    assert [r["wo_number"] for r in before["past_due_rows"]] == ["S3-MONDAY-OLD"]
    assert before["rows"][0]["urgency"] == "yellow"
    assert before["past_due_rows"][0]["urgency"] == "red"

    clock(monkeypatch, day=27, hour=2)
    after = client.get("/api/pm-needing-completion").json()
    assert after["as_of_date"] == "2026-10-27"
    assert after["rows"] == before["rows"]
    assert after["past_due_rows"] == before["past_due_rows"]
    html = client.get("/api/shifts/report/preview").json()["html"]
    assert "S3-TUESDAY-DUE" in html and "S3-MONDAY-OLD" in html
    assert "S3-WEDNESDAY" not in html
    assert "#FFF2C7" in html and "#FDE8E8" in html


def test_s5_handoff_date_is_saturday_even_when_staged_friday_night(
    client, monkeypatch
):
    clock(monkeypatch, day=23, hour=19)
    enter(client, "S5")
    response = stage_details(client, [
        to_be_done("S5-FRIDAY-OLD", due="10/23/2026"),
        to_be_done("S5-SATURDAY-DUE", due="10/24/2026"),
        to_be_done("S5-SUNDAY", due="10/25/2026"),
    ])
    assert response.status_code == 200, response.text
    before = client.get("/api/pm-needing-completion").json()
    assert before["as_of_date"] == "2026-10-24"
    assert [r["wo_number"] for r in before["rows"]] == ["S5-SATURDAY-DUE"]
    assert [r["wo_number"] for r in before["past_due_rows"]] == ["S5-FRIDAY-OLD"]

    clock(monkeypatch, day=24, hour=2)
    after = client.get("/api/pm-needing-completion").json()
    assert after["target_dates"] == ["2026-10-24"]
    assert after["count"] == 1 and after["past_due_count"] == 1
    html = client.get("/api/shifts/report/preview").json()["html"]
    assert "S5-SATURDAY-DUE" in html and "S5-FRIDAY-OLD" in html
    assert "S5-SUNDAY" not in html
    assert "DUE TODAY" in html and "PAST DUE" in html


def test_day_shift_pm_due_date_stays_on_same_calendar_day(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    response = stage_details(client, [
        to_be_done("S1-MONDAY", due="10/26/2026"),
        to_be_done("S1-TUESDAY", due="10/27/2026"),
    ])
    assert response.status_code == 200, response.text
    pm = client.get("/api/pm-needing-completion").json()
    assert pm["as_of_date"] == "2026-10-26"
    assert [r["wo_number"] for r in pm["rows"]] == ["S1-MONDAY"]
    assert pm["past_due_rows"] == []
