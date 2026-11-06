"""Legacy email/facility settings carry-forward: the old V2/V3 app's
key-value `settings` table held the real passdown recipient list, and a
fresh V3.1 cutover must not silently start with an empty one.
"""
import sqlite3

import pytest

from v31_facility import import_legacy_settings, load, save


def make_legacy_db(path, **overrides):
    values = {
        "lead_pin_hash": "deadbeef",
        "dc_number": '"IND3"',
        "teams_webhook_url": "https://walmart.webhook.office.com/not-our-concern",
        "drax_path": r'"C:\\roster.xlsx"',
        "email_from_name": "IND3 Shift Passdown",
        "email_to": "mx-72@wal-mart.com,mt7-60@wal-mart.com",
        "ms_client_id": "not-our-concern",
        "ai_rewrite_enabled": "true",
        **overrides,
    }
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        c.executemany("INSERT INTO settings VALUES (?,?)", values.items())


def test_imports_recipients_sender_and_dc_only(tmp_path):
    legacy = tmp_path / "ind3.db"
    make_legacy_db(legacy)

    result = import_legacy_settings(tmp_path, legacy)

    assert result["applied"] is True
    assert set(result["imported_keys"]) == {"recipients", "sender_name", "distribution_center"}
    settings = result["settings"]
    assert settings["recipients"] == "mx-72@wal-mart.com,mt7-60@wal-mart.com"
    assert settings["sender_name"] == "IND3 Shift Passdown"
    assert settings["distribution_center"] == "IND3"

    on_disk = load(tmp_path)
    assert on_disk["recipients"] == "mx-72@wal-mart.com,mt7-60@wal-mart.com"


def test_refuses_to_overwrite_existing_recipients_without_force(tmp_path):
    save(tmp_path, {"recipients": "already-set@wal-mart.com"})
    legacy = tmp_path / "ind3.db"
    make_legacy_db(legacy)

    result = import_legacy_settings(tmp_path, legacy)

    assert result["applied"] is False
    assert result["current"]["recipients"] == "already-set@wal-mart.com"
    assert load(tmp_path)["recipients"] == "already-set@wal-mart.com"


def test_force_overwrites_existing_recipients(tmp_path):
    save(tmp_path, {"recipients": "already-set@wal-mart.com"})
    legacy = tmp_path / "ind3.db"
    make_legacy_db(legacy)

    result = import_legacy_settings(tmp_path, legacy, force=True)

    assert result["applied"] is True
    assert load(tmp_path)["recipients"] == "mx-72@wal-mart.com,mt7-60@wal-mart.com"


def test_rejects_malformed_legacy_recipient_without_writing_anything(tmp_path):
    legacy = tmp_path / "ind3.db"
    make_legacy_db(legacy, email_to="not-an-email")

    with pytest.raises(ValueError):
        import_legacy_settings(tmp_path, legacy)
    assert load(tmp_path)["recipients"] == ""


def test_missing_legacy_keys_are_simply_skipped(tmp_path):
    legacy = tmp_path / "ind3.db"
    make_legacy_db(legacy, email_to="", email_from_name="")

    result = import_legacy_settings(tmp_path, legacy)

    assert result["applied"] is True
    assert result["imported_keys"] == ["distribution_center"]
