"""Shift Report UI simplification (2026-11-03), per direct feedback:
- Manual Breakdown/OOS entry trimmed to just Asset ID (no Cause/Notes).
- The 'Returned to Service (this shift)' list must actually be visible --
  previously resolved manual OOS records had nowhere to show up in the UI.
- 'WO Review Items' metric card replaced by the S3/S5 evening/morning
  completeness guide (falls back to the old pending-review count for
  non-overnight shifts, since that information still has real value).
- The standalone 'Import Audit' button(s) are gone; the audit panel has no
  manual click-to-reveal gate -- it renders automatically whenever there is
  real audit content, and 2026-11-04 refined this further: it also
  auto-collapses (not merely empty-looking) when there is nothing to show,
  instead of occupying space as a visibly empty box.
- LOTO's 20-key checklist is collapsed behind a dropdown toggle by default.
"""
from fastapi.testclient import TestClient
import pytest
import app as m

from test_reporting import clock, enter


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    with TestClient(m.app) as c:
        yield c


def test_import_audit_button_and_toggle_are_gone(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    markup = client.get("/").text
    assert 'id="view-audit"' not in markup
    assert 'id="v31-empty-audit"' not in markup
    assert 'id="audit-close"' not in markup
    # The redundant Import Audit panel is no longer rendered. The single
    # KPI shows evening/morning counts, and clicking it opens two counts only.
    assert 'id="audit"' not in markup
    assert 'id="v31-import-audit-metric"' in markup
    assert "Evening / Morning" in markup


def test_metric_card_replaces_wo_review_items_with_day_night_guide(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    markup = client.get("/").text
    assert 'id="metric-pending"' not in markup
    assert 'id="metric-daynight"' in markup
    assert 'id="metric-daynight-label"' in markup


def test_loto_keys_are_collapsed_behind_a_toggle_by_default(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    markup = client.get("/").text
    assert 'id="loto-keys-toggle"' in markup
    select_area = markup.split('id="loto-select-area"', 1)[1][:40]
    assert "hidden" in select_area


def test_manual_oos_form_is_asset_id_only(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    js = client.get("/web/manual-oos.js").text
    assert "v31-oos-asset" in js
    assert "v31-oos-cause" not in js
    assert "v31-oos-notes" not in js


def test_manual_oos_js_renders_a_resolved_this_shift_list(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    js = client.get("/web/manual-oos.js").text
    assert "v31-oos-rts-list" in js
    assert "rts_this_shift" in js



def test_successful_cetaris_import_does_not_leave_feedback_under_buttons(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client,"S1")
    html=client.get("/shift-report.html").text
    assert 'id="v31-import-feedback" role="status" aria-live="polite" hidden' in html
    js=client.get("/web/shift-data.js").text
    assert "feedback('Imported '+" not in js
    assert "node.hidden=!message" in js
    assert "feedback('');" in js
    # Real import errors still appear prominently.
    assert "No qualifying work orders for this shift" in js


def test_pm_needing_completion_workspace_renders_five_column_table(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client,"S1")
    script=client.get("/shift-report.html").text
    for heading in ("WO #","Asset","PM Due Date","Promise Date / Time","WO Date / Time"):
        assert heading in script
    assert "needed.rows.map((r,i)" in script
    assert "needed.count||0" in script
