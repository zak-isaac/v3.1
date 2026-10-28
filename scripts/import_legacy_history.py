"""Import an exact copy of the original V2/V3 SQLite database, read-only source.

Examples:
python scripts/import_legacy_history.py --source ../ind3-shift-passdown/dbs/ind3.db
python scripts/import_legacy_history.py --source /approved/path/dbs/ind3.db --import

No source tables/rows are modified. Historical database remains in .local-data/
and is never included in the public repository.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from v31_legacy_history import inspect,import_snapshot,LegacyHistoryError


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",required=True,type=Path,help="Existing V2/V3 SQLite IND3 database")
    parser.add_argument("--import",dest="apply",action="store_true",
                        help="Copy and verify the historical database into this V3.1 project")
    args=parser.parse_args()
    try:
        preview=inspect(args.source)
        print("Legacy V2/V3 verification:")
        print(json.dumps(preview,indent=2))
        if args.apply:
            result=import_snapshot(args.source,ROOT/".local-data")
            print("Created independent exact-schema historical archive:")
            print(json.dumps(result,indent=2))
        else:
            print("DRY RUN ONLY. Re-run with --import to create the V3.1 archive.")
    except LegacyHistoryError as exc:
        print("Import aborted:",exc,file=sys.stderr)
        raise SystemExit(2)


if __name__=="__main__":
    main()
