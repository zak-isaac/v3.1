"""Carry forward units still OOS (never RTS'd) from the legacy V2/V3 archive
into V3.1's live OOS board, so equipment that was genuinely still broken at
cutover doesn't silently vanish just because the history import itself is
read-only (see v31_legacy_history.py / scripts/import_legacy_history.py).

Must run AFTER `scripts/import_legacy_history.py --import` -- this reads the
archive that script creates, it does not touch the original production
database at all.

Safe to re-run: any asset that already has an open manual_oos entry (from a
previous run of this script, or a tech independently re-logging it) is
skipped, never duplicated.

Examples:
python scripts/import_legacy_oos.py             # dry run, lists what would import
python scripts/import_legacy_oos.py --apply      # writes to the live V3.1 OOS board
"""
from __future__ import annotations
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as m
from v31_oos import import_legacy_oos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write to the live V3.1 OOS board; default is a dry-run preview")
    args = parser.parse_args()

    archive = m.PRIVATE / "legacy-v2-v3-archive.sqlite3"
    if not archive.is_file():
        print("Legacy archive not found -- run scripts/import_legacy_history.py --import first.",
              file=sys.stderr)
        raise SystemExit(2)

    if not args.apply:
        with sqlite3.connect(f"file:{archive}?mode=ro", uri=True) as src:
            units = [row[0] for row in src.execute("SELECT unit FROM oos_units")]
        print(f"Legacy archive has {len(units)} unit(s) still marked OOS:")
        for unit in units:
            print(f"  - {unit}")
        print("DRY RUN ONLY. Re-run with --apply to add these to the live V3.1 OOS board.")
        return

    conn = m._db()
    try:
        result = import_legacy_oos(conn, archive)
    finally:
        conn.close()
    print("Legacy OOS carry-forward complete:")
    print(f"  Imported ({len(result['imported'])}): {result['imported']}")
    print(f"  Already open, skipped ({len(result['skipped_already_open'])}): "
          f"{result['skipped_already_open']}")


if __name__ == "__main__":
    main()
