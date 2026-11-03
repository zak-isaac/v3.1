"""Shift Report UI simplification (2026-11-03), per direct feedback:
- Manual Breakdown/OOS entry trimmed to just Asset ID (no Cause/Notes).
- The 'Returned to Service (this shift)' list must actually be visible --
  previously resolved manual OOS records had nowhere to show up in the UI.
- 'WO Review Items' metric card replaced by the S3/S5 evening/morning
  completeness guide (falls back to the old pending-review count for
  non-overnight shifts, since that information still has real value).
- The standalone 'Import Audit' button(s) are gone; the audit panel is
  always visible instead of hidden behind a click.
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
    # The audit panel itself must render without a hidden gate now.
    panel = markup.split('id="audit"', 1)[1][:60]
    assert "hidden" not in panel


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
