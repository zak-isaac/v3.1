"""Review and optionally apply ORIGINAL V3 database changes to shared Drax.

On first run the bridge records a baseline and does NOT write to Drax.
Only later changes in old V3's associates table are eligible to be applied.
Conflicting changes to both systems are reported and never overwritten.

Dry run:
  python scripts/sync_drax_from_v3.py --v3-db "C:\path\to\dbs\ind3.db"

After IT approval, with the authorized, locally synced Drax workbook:
  set IND3_V31_DRAX_WRITE_ENABLED=1
  python scripts/sync_drax_from_v3.py --v3-db "C:\path\to\dbs\ind3.db" --apply

No changes are made to the old V3 SQLite database.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from v31_drax_bridge import reconcile,BridgeError
from v31_roster import _source


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--v3-db",required=True,type=Path,help="Original V3 IND3 SQLite database (read only)")
    p.add_argument("--drax",type=Path,help="Approved synchronized Drax .xlsx path")
    p.add_argument("--apply",action="store_true",help="Write approved, nonconflicting cell edits (requires env flag)")
    args=p.parse_args()
    file=args.drax or _source(ROOT/".local-data")[0]
    if file is None:
        p.error("No authorized Drax workbook available. Configure ASSOCIATES_PATH.")
    try:
        outcome=reconcile(args.v3_db,file,ROOT/".local-data",write=args.apply)
    except (OSError,BridgeError) as exc:
        print(f"Sync aborted: {exc}",file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(outcome,indent=2))
    if args.apply and not outcome["write_enabled"]:
        print("WRITE DISABLED. Set IND3_V31_DRAX_WRITE_ENABLED=1 explicitly.")
    if outcome["conflicts"]:
        print("Conflicting or missing records require manual review; no forced merge.")


if __name__=="__main__":
    main()
