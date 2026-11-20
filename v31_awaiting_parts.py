"""Small facility-wide Awaiting Parts list from daily Cetaris WO Details.

Every WO Status P (Parts Pending) qualifies irrespective of Primary RFR,
shift attribution, technician, or physical OOS. Unlike PM urgency, unresolved
parts-pending observations persist when a later daily file omits a WO; only
a newer explicit non-P status clears it. No historical PM data is consulted.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

from v31_pm_due_scanner import _grid, _cell
from v31_cetaris_codes import normalize_wo_status
from v31_cetaris import _parse_dt

TZ = ZoneInfo("America/Indiana/Indianapolis")


def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS awaiting_parts_state(
        wo_number TEXT PRIMARY KEY,
        asset TEXT NOT NULL,
        is_pending INTEGER NOT NULL,
        cetaris_status TEXT NOT NULL,
        observed_at TEXT NOT NULL)""")


def observe_wo_details(conn, content):
    """Consume one newly staged DAILY WO Details export, including no-tech WOs.

    Repeated WO labor rows are collapsed to their latest status by the
    export's Update Date/Time. When there is no timestamp, an explicit
    non-P status wins a tie to avoid a false outstanding parts flag.
    """
    ensure(conn)
    newest = {}
    for headers, grid in _grid(content):
        if not headers:
            return {"observed": 0, "parts_pending": 0}
        columns = {_cell(name).casefold(): index for index, name in enumerate(headers)
                   if _cell(name)}
        if not {"wo number", "wo status"} <= columns.keys():
            return {"observed": 0, "parts_pending": 0}
        def get(row, name):
            position = columns.get(name)
            return row[position] if position is not None and position < len(row) else None
        for row in grid:
            if not row:
                continue
            wo = _cell(get(row, "wo number"))
            asset = _cell(get(row, "asset id")) or _cell(get(row, "asset #"))
            if not wo or asset.casefold().startswith("pkm"):
                continue
            status = normalize_wo_status(_cell(get(row, "wo status")))[1]
            if not status:
                continue
            raw_updated = (get(row, "update date / time") or
                           get(row, "wo last complete date / time") or
                           get(row, "wo closed date/time"))
            parsed = _parse_dt(raw_updated)
            updated = parsed.isoformat() if parsed else ""
            record = {"wo_number": wo, "asset": asset,
                      "status": status, "updated": updated}
            previous = newest.get(wo)
            if (previous is None or updated > previous["updated"] or
                    (updated == previous["updated"] and
                     previous["status"] == "P" and status != "P")):
                newest[wo] = record
    now = datetime.now(TZ).isoformat()
    pending = 0
    for record in newest.values():
        wo, asset, status = (record["wo_number"], record["asset"], record["status"])
        old = conn.execute("""SELECT asset,is_pending FROM awaiting_parts_state
                              WHERE wo_number=?""", (wo,)).fetchone()
        if status == "P" and not asset and not old:
            continue
        if status != "P" and not old:
            continue
        pending += int(status == "P")
        conn.execute("""INSERT INTO awaiting_parts_state(
            wo_number,asset,is_pending,cetaris_status,observed_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(wo_number) DO UPDATE SET
              asset=excluded.asset,is_pending=excluded.is_pending,
              cetaris_status=excluded.cetaris_status,
              observed_at=excluded.observed_at""",
            (wo, asset or old["asset"], int(status == "P"), status, now))
    return {"observed": len(newest), "parts_pending": pending}


def list_records(conn):
    ensure(conn)
    return [dict(row) for row in conn.execute(
        """SELECT asset, wo_number FROM awaiting_parts_state
           WHERE is_pending=1 AND trim(asset)<>'' 
           ORDER BY lower(asset), wo_number""")]
