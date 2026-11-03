"""S3/S5 overnight shift attribution: the 30-minute post-shift completion
grace, and the gap it used to leave for S3.

Investigation (2026-11-03): v31_split._windows() gave S5 an explicit
06:00-06:30 completion grace (a technician finishing just past the hard
06:00 boundary still gets credited to the overnight shift they actually
worked, resolved like any other overlap via roster/continuity evidence --
never auto-granted). S3 had no equivalent for its 07:00 boundary. Verified
with v31_split.assign() directly: a Thursday-night S3 technician
completing a WO at 07:00-07:30 Friday was rejected by S3 (past the hard
cutoff) and never considered for S4 either (S4's MST would have no reason
to select an S3-home technician during their own import) -- the WO simply
disappeared from Shift Happenings for both shifts. Fixed by extending the
same 30-minute grace to S3, symmetrically.

These tests lock in: (1) the exact boundary for both shifts via the unit-
level API, so a future change can't silently narrow or widen either grace
without a test noticing, and (2) a full-stack proof that the previously-
lost 07:00-07:30 Friday completion now lands correctly in a real S3 import.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient
import app as m

import v31_split as split
from test_reporting import clock, workbook, row, enter, upload, import_techs


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    with TestClient(m.app) as c:
        yield c


def _accepted(shift_code, shift_day, ts, roster_code):
    rows = [{
        "wo_number": "WO1", "tech_name": "Tech A", "status": "Closed",
        "start_at": None, "in_service_at": None,
        "last_complete_at": ts, "completed_by": "Tech A", "last_technician": "Tech A",
        "asset_id": "A1", "closed_at": ts,
    }]
    accepted, _ = split.assign(rows, {"Tech A"}, shift_code, date.fromisoformat(shift_day),
                                roster_shifts={"Tech A": roster_code})
    return bool(accepted)


@pytest.mark.parametrize("ts,expected", [
    ("06:59:00", True),   # before the hard boundary: always owned by S3
    ("07:00:00", True),   # grace starts exactly at the boundary
    ("07:15:00", True),   # inside the new 30-minute grace
    ("07:30:00", True),   # grace's last inclusive moment
    ("07:31:00", False),  # one minute past grace: no longer S3's
])
def test_s3_thursday_night_gets_the_same_30_minute_morning_grace_as_s5(ts, expected):
    # 2026-10-29 is a Thursday; S3 that night covers into Friday morning.
    accepted = _accepted("S3", "2026-10-29", f"2026-10-30T{ts}", roster_code="S3")
    assert accepted is expected, f"S3 completion at {ts} Friday: expected accepted={expected}"


@pytest.mark.parametrize("ts,expected", [
    ("05:59:00", True),
    ("06:00:00", True),
    ("06:15:00", True),
    ("06:30:00", True),
    ("06:31:00", False),
])
def test_s5_friday_night_grace_unchanged_by_the_s3_fix(ts, expected):
    """Same boundary shape as S3 now has, confirming the fix made S3
    symmetric to S5 rather than changing S5's existing behavior."""
    accepted = _accepted("S5", "2026-10-30", f"2026-10-31T{ts}", roster_code="S5")
    assert accepted is expected, f"S5 completion at {ts} Saturday: expected accepted={expected}"


def test_s1_s2_s4_have_no_such_grace(monkeypatch):
    """The grace is deliberately specific to the two overnight shifts (S3,
    S5) -- day shifts ending mid-afternoon/evening have no adjacent shift
    immediately expecting a continuation, so no grace was added for them."""
    # S1 Monday ends 16:00; one second past should already be outside S1.
    assert _accepted("S1", "2026-10-26", "2026-10-26T16:00:00", roster_code="S1") is False
    # S4 Saturday ends 18:00; one second past should already be outside S4.
    assert _accepted("S4", "2026-10-31", "2026-10-31T18:00:00", roster_code="S4") is False


def test_full_import_no_longer_loses_an_s3_wo_completed_just_after_7am(client, tmp_path, monkeypatch):
    """End-to-end proof of the fix: before it, this exact scenario (a real
    S3 technician finishing a WO at 07:15 Friday morning, reported by the
    S3 MST who worked Thursday night) silently dropped the WO from Shift
    Happenings -- not rejected with an explanation, just absent."""
    clock(monkeypatch, day=29, hour=21)  # Thursday 9pm, inside S3's window
    shift_id = enter(client, "S3")
    assert upload(client, [
        row("S3_LATE_FINISH", tech="Synthetic A", status="Closed",
            started="2026-10-30 01:00", serviced="2026-10-30 07:15",
            remarks="Finished up right at shift change"),
    ]).status_code == 200
    imported = import_techs(client, ["Synthetic A"])
    assert imported.status_code == 200, imported.text
    state = client.get("/api/reporting/state").json()
    wo_numbers = {r["wo_number"] for r in state["rows"]}
    assert "S3_LATE_FINISH" in wo_numbers, (
        "A WO finished at 07:15 Friday by the Thursday-night S3 technician "
        "must still land in that S3 shift's Shift Happenings"
    )
