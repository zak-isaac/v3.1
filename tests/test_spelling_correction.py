"""Phase 1 V3->V3.1 feature recovery: spelling correction.

Covers both the pure module (ported unchanged from V3 production,
commit 71bc42d) and its integration into v31_reporting.get_state() as a
display-only transform -- the DB row of record (Cetaris-original or
MST-edited) must never be touched.
"""
import sqlite3

import spelling_fix
from test_reporting import clock, client, enter, upload, import_techs, row


# ---------------------------------------------------------------------------
# Pure module behavior
# ---------------------------------------------------------------------------

def test_known_typos_are_corrected():
    assert spelling_fix.correct_spelling("He adressed the allignment issue") == \
        "He addressed the alignment issue"
    assert spelling_fix.correct_spelling("Repalced the intstalled servor") == \
        "Replaced the installed servo"


def test_approved_industrial_vocabulary_is_never_flagged():
    text = "Checked the ASRS conveyor rollers, tensioner, and VFD after the PM"
    assert spelling_fix.correct_spelling(text) == text


def test_asset_ids_and_wo_numbers_survive_untouched():
    # All-caps / mixed-alphanumeric tokens are never candidates for
    # correction, regardless of how "typo-like" they might look.
    text = "WO9001 on ASSET9001 ASRS12X adressed the issue"
    out = spelling_fix.correct_spelling(text)
    assert "WO9001" in out
    assert "ASSET9001" in out
    assert "ASRS12X" in out
    assert "addressed" in out  # the real ordinary-English word still gets fixed


def test_technician_names_are_protected():
    text = "Smith, John frist checked the sensor"
    out = spelling_fix.correct_spelling(text)
    assert "Smith, John" in out
    assert "first" in out  # "frist" nearby is still corrected


def test_correction_is_idempotent():
    text = "He adressed the allignment issue, Smith, John reviewed it"
    once = spelling_fix.correct_spelling(text)
    twice = spelling_fix.correct_spelling(once)
    assert once == twice


def test_empty_and_blank_text_unchanged():
    assert spelling_fix.correct_spelling("") == ""
    assert spelling_fix.correct_spelling("   ") == "   "
    assert spelling_fix.correct_spelling(None) is None


# ---------------------------------------------------------------------------
# Integration: v31_reporting.get_state()
# ---------------------------------------------------------------------------

def test_state_shows_corrected_text_but_db_keeps_the_original(client, monkeypatch, tmp_path):
    clock(monkeypatch)
    enter(client)
    uploaded = upload(client, [
        row("9001", tech="Synthetic A", issue="Conveyer adressed",
            remarks="Repalced the intstalled servor; allignment fixed"),
    ])
    assert uploaded.status_code == 200, uploaded.text
    assert import_techs(client, ["Synthetic A"]).status_code == 200

    state = client.get("/api/reporting/state").json()
    assert len(state["rows"]) == 1
    row_out = state["rows"][0]

    assert row_out["remarks"] == "Replaced the installed servo; alignment fixed"
    assert row_out["remarks_original"] == "Repalced the intstalled servor; allignment fixed"
    assert row_out["cause"] == "Conveyer addressed"
    assert row_out["cause_original"] == "Conveyer adressed"
    # Asset/WO fields must never pass through spelling correction.
    assert row_out["wo_number"] == "9001"
    assert row_out["asset"] == "ASSET9001"

    db_path = tmp_path / "v31.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    raw = conn.execute("SELECT cause, remarks FROM report_wo WHERE wo_number='9001'").fetchone()
    conn.close()
    assert raw["cause"] == "Conveyer adressed", "DB row of record must never be rewritten"
    assert raw["remarks"] == "Repalced the intstalled servor; allignment fixed"


def test_multiple_technician_remarks_each_corrected_after_consolidation(client, monkeypatch):
    clock(monkeypatch)
    enter(client)
    uploaded = upload(client, [
        row("9002", tech="Synthetic A", remarks="Repalced sensor", issue="Fault"),
        row("9002", tech="Synthetic B", remarks="allignment checked", issue="Fault"),
    ])
    assert uploaded.status_code == 200, uploaded.text
    assert import_techs(client, ["Synthetic A", "Synthetic B"]).status_code == 200

    state = client.get("/api/reporting/state").json()
    out = next(r for r in state["rows"] if r["wo_number"] == "9002")
    assert out["remarks"] == "Replaced sensor; alignment checked"
    assert "Synthetic A" in out["tech"] and "Synthetic B" in out["tech"]


def test_reimport_does_not_destroy_or_double_correct_data(client, monkeypatch):
    clock(monkeypatch)
    enter(client)
    assert upload(client, [row("9003", remarks="Repalced the part")]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200

    # MST manually edits -- this must survive any future reimport untouched.
    edited = client.post("/api/reporting/work-orders/9003", json={"remarks": "Manually confirmed OK"})
    assert edited.status_code == 200, edited.text

    # Replace the file with new + previously-seen WOs.
    assert upload(client, [
        row("9003", remarks="Repalced the part"),
        row("9004", remarks="allignment adjusted"),
    ]).status_code == 200
    assert import_techs(client, ["Synthetic A"]).status_code == 200

    state = client.get("/api/reporting/state").json()
    by_wo = {r["wo_number"]: r for r in state["rows"]}
    assert by_wo["9003"]["remarks"] == "Manually confirmed OK", \
        "Edited WO must be preserved verbatim across reimport, not reset or re-corrected"
    assert by_wo["9004"]["remarks"] == "alignment adjusted"

    # Reading state again must not further mutate already-corrected text.
    state_again = client.get("/api/reporting/state").json()
    by_wo_again = {r["wo_number"]: r for r in state_again["rows"]}
    assert by_wo_again["9003"]["remarks"] == by_wo["9003"]["remarks"]
    assert by_wo_again["9004"]["remarks"] == by_wo["9004"]["remarks"]
