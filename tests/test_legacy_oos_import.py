"""Legacy OOS carry-forward: units still broken in the old V2/V3 app at
V3.1 cutover must not silently vanish just because the history import
itself is otherwise read-only.
"""
import sqlite3

from v31_oos import import_legacy_oos, list_records, create


def make_legacy_archive(path, units):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE oos_units (id INTEGER PRIMARY KEY, unit TEXT NOT NULL UNIQUE, "
                  "added_at TEXT NOT NULL)")
        for i, (unit, added_at) in enumerate(units, start=1):
            c.execute("INSERT INTO oos_units VALUES (?,?,?)", (i, unit, added_at))


def test_imports_legacy_open_units_as_open_manual_oos(tmp_path):
    archive = tmp_path / "legacy-v2-v3-archive.sqlite3"
    make_legacy_archive(archive, [("RTU-N2-11", "2026-07-13 05:15:51"), ("TK51", "2026-07-27 05:22:28")])

    conn = sqlite3.connect(tmp_path / "live.sqlite3")
    conn.row_factory = sqlite3.Row
    result = import_legacy_oos(conn, archive)

    assert result["imported"] == ["RTU-N2-11", "TK51"]
    assert result["skipped_already_open"] == []
    open_records = list_records(conn, open_only=True)
    assets = {r["asset"]: r for r in open_records}
    assert set(assets) == {"RTU-N2-11", "TK51"}
    assert assets["RTU-N2-11"]["logged_at"] == "2026-07-13 05:15:51"
    assert assets["RTU-N2-11"]["logged_by"] == "legacy-import"
    assert assets["RTU-N2-11"]["open"] == 1


def test_rerunning_is_idempotent(tmp_path):
    archive = tmp_path / "legacy-v2-v3-archive.sqlite3"
    make_legacy_archive(archive, [("SCRUB0001", "2026-09-30 22:16:57")])
    conn = sqlite3.connect(tmp_path / "live.sqlite3")
    conn.row_factory = sqlite3.Row

    first = import_legacy_oos(conn, archive)
    second = import_legacy_oos(conn, archive)

    assert first["imported"] == ["SCRUB0001"]
    assert second["imported"] == []
    assert second["skipped_already_open"] == ["SCRUB0001"]
    assert len(list_records(conn, open_only=True)) == 1


def test_skips_asset_already_logged_open_by_a_technician(tmp_path):
    """If a tech independently re-logs the same asset after cutover (before
    this script ever runs), the legacy import must not create a duplicate
    open entry for the same piece of equipment."""
    archive = tmp_path / "legacy-v2-v3-archive.sqlite3"
    make_legacy_archive(archive, [("WAV0008", "2026-10-26 05:40:11")])
    conn = sqlite3.connect(tmp_path / "live.sqlite3")
    conn.row_factory = sqlite3.Row
    create(conn, asset="WAV0008", cause="Noticed again this shift", notes="",
           logged_by="111111", logged_by_name="Real Tech", shift_id=1,
           shift_code="S1", now_iso="2026-11-05 10:00:00")

    result = import_legacy_oos(conn, archive)

    assert result["imported"] == []
    assert result["skipped_already_open"] == ["WAV0008"]
    records = list_records(conn, open_only=True)
    assert len(records) == 1
    assert records[0]["logged_by_name"] == "Real Tech"  # the real entry, not overwritten
