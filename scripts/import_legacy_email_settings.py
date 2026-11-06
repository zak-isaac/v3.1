"""Carry forward the facility/email settings (passdown recipients, sender
display name, distribution center) an admin already configured in the old
V2/V3 app, so V3.1's "Administration > Facility & Email Settings" doesn't
start with a blank recipient list after cutover.

Only recipients/sender_name/distribution_center are migrated -- see
v31_facility.import_legacy_settings() docstring for what's deliberately
left behind and why.

Refuses to overwrite an already-configured recipients list unless --force
is passed: this is a one-time cutover bridge, not a standing sync.

Examples:
python scripts/import_legacy_email_settings.py --source ../v3/dbs/ind3.db
python scripts/import_legacy_email_settings.py --source ../v3/dbs/ind3.db --apply
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as m
from v31_facility import import_legacy_settings, LEGACY_SETTINGS_MAP


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path,
                        help="Old V2/V3 ind3.db (or the already-imported legacy archive)")
    parser.add_argument("--apply", action="store_true",
                        help="Write to V3.1's live facility settings; default is a dry-run preview")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite recipients even if already configured in V3.1")
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"Source database not found: {args.source}", file=sys.stderr)
        raise SystemExit(2)

    if not args.apply:
        import sqlite3
        with sqlite3.connect(f"file:{args.source}?mode=ro", uri=True) as src:
            legacy = {row[0]: row[1] for row in
                      src.execute("SELECT key,value FROM settings")}
        print("Would carry forward:")
        for legacy_key, settings_key in LEGACY_SETTINGS_MAP.items():
            if legacy.get(legacy_key):
                print(f"  {legacy_key} -> {settings_key}: {legacy[legacy_key]!r}")
        print("DRY RUN ONLY. Re-run with --apply to write to V3.1's live settings.")
        return

    result = import_legacy_settings(m.PRIVATE, args.source, force=args.force)
    if not result["applied"]:
        print(f"Not applied: {result['reason']}")
        print(f"Current settings: {result['current']}")
        print("Pass --force to overwrite anyway.")
        return
    print(f"Imported keys: {result['imported_keys']}")
    print(f"Settings now: {result['settings']}")


if __name__ == "__main__":
    main()
