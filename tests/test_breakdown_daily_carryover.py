"""Regression coverage for daily Cetaris imports and cross-shift breakdowns.

Synthetic work orders only. Never connect to a workshop database.
"""
import v31_breakdown
import app as m
from test_reporting import client, clock, enter, upload, import_techs, row


def test_in_process_non_breakdown_is_reported_not_falsely_rts(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    work = row("ACTIVE-WORK", category="Proactive Repair", status="In Process",
               started="2026-10-26 08:10", serviced=None,
               remarks="Investigated drive fault; repair continuing")
    assert upload(client, [work]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    state = client.get("/api/reporting/state").json()
    assert len(state["rows"]) == 1
    assert state["rows"][0]["wo_number"] == "ACTIVE-WORK"
    assert state["rows"][0]["status"] == "IN_PROGRESS"
    assert "Investigated drive fault" in state["rows"][0]["remarks"]
    assets=client.get("/api/assets/status").json()
    assert assets["rts"] == [] and assets["oos"] == []
    # Unfinished work is available to FLAG as a carryover, not invented
    # as one simply because Cetaris still says In Process.
    html=client.get("/api/shifts/report/preview").json()["html"]
    follow=html.split('>Open Follow-ups / Shift Carry-overs</strong>',1)[1].split(
        '>Reliable Operations</strong>',1)[0]
    assert 'ACTIVE-WORK' not in follow
    flagged=client.post("/api/reporting/carryovers/flag",
                        json={"wo_number":"ACTIVE-WORK","enabled":True})
    assert flagged.status_code == 200,flagged.text
    html=client.get("/api/shifts/report/preview").json()["html"]
    follow=html.split('>Open Follow-ups / Shift Carry-overs</strong>',1)[1].split(
        '>Reliable Operations</strong>',1)[0]
    assert 'ACTIVE-WORK' in follow


def test_completed_breakdown_stays_with_completing_shift_and_reconciles_oos(
    client, monkeypatch
):
    clock(monkeypatch, day=26, hour=10)
    s1 = enter(client, "S1")
    manual = client.post("/api/oos", json={"asset": "ASSETBD-01"})
    assert manual.status_code == 200, manual.text
    early = row("BD-01", category="Breakdown", status="In Process",
                started="2026-10-26 09:00", serviced=None,
                remarks="Drive motor failed; repair initiated")
    assert upload(client, [early]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200

    # Same WO, documented new S2 labor, and no Cetaris administrative closure
    # (e.g. vendor invoice still pending). It must still prompt for physical RTS.
    clock(monkeypatch, day=26, hour=17)
    response = client.post("/api/shifts/start", json={"code": "S2"})
    assert response.status_code == 200, response.text
    s2 = response.json()["id"]
    completed = row("BD-01", category="Breakdown", status="Completed",
                    started="2026-10-26 16:10", serviced="2026-10-26 16:30",
                    remarks="Replacement installed; tested operational")
    assert upload(client, [completed]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200

    with m._db() as conn:
        assert v31_breakdown.pending(conn, s1) == []
        assert [x["wo_number"] for x in v31_breakdown.pending(conn, s2)] == ["BD-01"]
        try:
            v31_breakdown.decide(conn, "BD-01", "rts", s1, "8972")
        except ValueError:
            pass
        else:
            raise AssertionError("Earlier shift was incorrectly allowed to claim RTS")

    assert any(x["wo_number"] == "BD-01" for x in
               client.get("/api/assets/status").json()["pending"])
    result = client.post("/api/breakdowns/BD-01/decision",
                         json={"decision": "rts"})
    assert result.status_code == 200, result.text
    assert result.json()["resolved"] is True
    with m._db() as conn:
        old = conn.execute("SELECT status FROM report_wo WHERE shift_id=? AND wo_number=?",
                           (s1, "BD-01")).fetchone()
        new = conn.execute("SELECT status FROM report_wo WHERE shift_id=? AND wo_number=?",
                           (s2, "BD-01")).fetchone()
    assert old["status"] == "OOS"
    assert new["status"] == "RTS"
    assert client.get("/api/oos").json()["open"] == []
    assets = client.get("/api/assets/status").json()
    assert assets["oos"] == []
    assert [x["asset"] for x in assets["rts"]] == ["ASSETBD-01"]
    assert [x["wo_number"] for x in assets["breakdowns"]] == ["BD-01"]

    # Reimport the same Completed WO: no duplicate review or RTS event.
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    assert client.get("/api/assets/status").json()["pending"] == []


def test_keep_oos_requires_new_verified_completion_before_next_shift_rts(
    client, monkeypatch
):
    clock(monkeypatch, day=26, hour=17)
    s2 = enter(client, "S2")
    first = row("BD-REWORK", category="Breakdown", status="Completed",
                started="2026-10-26 16:15", serviced="2026-10-26 16:40",
                remarks="Repair attempted; equipment still down")
    assert upload(client, [first]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    review = client.post("/api/breakdowns/BD-REWORK/decision",
                         json={"decision": "keep_oos"})
    assert review.status_code == 200, review.text
    with m._db() as conn:
        assert v31_breakdown.pending(conn, s2) == []

    # Reimporting S2's unchanged Completed file does not reopen the decision.
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    with m._db() as conn:
        assert v31_breakdown.pending(conn, s2) == []

    # S4 performs new repair work four days later and actually completes it.
    clock(monkeypatch, day=30, hour=16)
    started = client.post("/api/shifts/start", json={"code": "S4"})
    assert started.status_code == 200, started.text
    s4 = started.json()["id"]
    later = row("BD-REWORK", category="Breakdown", status="Completed",
                started="2026-10-30 14:40", serviced="2026-10-30 15:05",
                remarks="Final repairs completed; operation checked")
    assert upload(client, [later]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    with m._db() as conn:
        assert v31_breakdown.pending(conn, s2) == []
        assert [x["wo_number"] for x in v31_breakdown.pending(conn, s4)] == [
            "BD-REWORK"
        ]
    result = client.post("/api/breakdowns/BD-REWORK/decision",
                         json={"decision": "rts"})
    assert result.status_code == 200, result.text
    assert result.json()["resolved"] is True


def test_other_shift_completed_status_does_not_reassign_breakdown(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    s1 = enter(client, "S1")
    early = row("BD-EXT", category="Breakdown", status="In Process",
                started="2026-10-26 08:45", serviced=None)
    assert upload(client, [early]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200

    # A daily file may show somebody else's subsequent completion. Choosing
    # only S1 technicians must not award that completion to S1.
    unrelated = row("BD-EXT", tech="Synthetic Night", category="Breakdown",
                    status="Completed", started="2026-10-26 22:10",
                    serviced="2026-10-26 22:45")
    assert upload(client, [early, unrelated]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200
    with m._db() as conn:
        state = conn.execute(
            "SELECT status,updated_shift_id FROM breakdown_state WHERE wo_number=?",
            ("BD-EXT",)).fetchone()
        assert state["status"] == "in process"
        assert state["updated_shift_id"] == s1
        assert v31_breakdown.pending(conn, s1) == []
    assert client.get("/api/assets/status").json()["rts"] == []
