"""Small facility-wide Awaiting Parts list from daily Cetaris WO Details.

Any WO observed as status P (Parts Pending) qualifies regardless of Primary
RFR, technician, shift or physical OOS. Once known, it stays in the outstanding
parts-repair list through other unfinished statuses (including In Process)
until a newer explicit Completed, Closed or Void status clears it.
An omitted WO is never silently resolved. This is NOT the PM urgency backlog.
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
        observed_at TEXT NOT NULL,
        source_updated_at TEXT NOT NULL DEFAULT '')""")
    columns={row['name'] for row in conn.execute(
        "PRAGMA table_info(awaiting_parts_state)")}
    if "source_updated_at" not in columns:
        # Existing production data is preserved. This column lets an older
        # daily export avoid overwriting a more recently verified WO status.
        conn.execute("""ALTER TABLE awaiting_parts_state
             ADD COLUMN source_updated_at TEXT NOT NULL DEFAULT ''""")


def observe_wo_details(conn, content):
    """Consume one newly staged DAILY WO Details export, including no-tech WOs.

    Repeated WO labor rows collapse to their newest status by Cetaris's
    Update Date/Time; later explicit C/D/V resolves a previously pending WO.
    Other unfinished statuses retain the tracked WO so an unresolved repair
    never disappears simply because its workflow advanced to In Process.
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
            # Actual WO equipment is Asset #. Asset ID can be a different
            # alias (e.g. SCRUB0011 vs SCRUB0002 on WO# 28055231).
            asset = _cell(get(row, "asset #")) or _cell(get(row, "asset id"))
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
        old = conn.execute("""SELECT asset,is_pending,cetaris_status,
                                 source_updated_at FROM awaiting_parts_state
                              WHERE wo_number=?""", (wo,)).fetchone()
        if status == "P" and not asset and not old:
            continue
        if status != "P" and not old:
            # The Awaiting Parts list is populated only by evidence of P;
            # generic active repairs belong in the existing WO workflows.
            continue
        previous_update = old["source_updated_at"] if old else ""
        if old and updated and previous_update and updated < previous_update:
            # Do not regress from Closed/Completed to an older historic P.
            continue
        if (old and updated and previous_update and
                updated == previous_update and
                old["cetaris_status"] in ("C", "D", "V") and
                status not in ("C", "D", "V")):
            continue
        still_open = status not in ("C", "D", "V")
        pending_now = bool(still_open and
                           (status == "P" or (old and old["is_pending"])))
        pending += int(pending_now)
        conn.execute("""INSERT INTO awaiting_parts_state(
            wo_number,asset,is_pending,cetaris_status,observed_at,
            source_updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(wo_number) DO UPDATE SET
              asset=excluded.asset,is_pending=excluded.is_pending,
              cetaris_status=excluded.cetaris_status,
              observed_at=excluded.observed_at,
              source_updated_at=excluded.source_updated_at""",
            (wo, asset or old["asset"], int(pending_now), status, now,
             updated or previous_update))
    return {"observed": len(newest), "parts_pending": pending}


def list_records(conn):
    ensure(conn)
    return [dict(row) for row in conn.execute(
        """SELECT asset, wo_number FROM awaiting_parts_state
           WHERE is_pending=1 AND trim(asset)<>'' 
           ORDER BY lower(asset), wo_number""")]
