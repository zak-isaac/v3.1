"""V3-inspired Cetaris breakdown lifecycle with confirmed RTS and manual OOS holds.

In Process -> globally OOS. Completed/Manager Review Required -> human RTS or
keep OOS decision. Closed -> RTS only when no outstanding same-asset breakdown
or explicit/manual hold. Never use unrelated maintenance as RTS evidence.
Global state survives shift boundaries; historical snapshots are immutable.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

import v31_oos

TZ=ZoneInfo("America/Indiana/Indianapolis")
PRIORITY={"in process":1,"completed":2,"closed":3}
ALIASES={"manager review required":"completed"}

def _now():
    return datetime.now(TZ).isoformat()

def ensure(conn):
    # Avoid executescript()'s implicit COMMIT during report imports.
    # The only reason to run DDL is upgrading a missing historical table.
    existing={r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if {'breakdown_state','breakdown_manual_holds','breakdown_rts_events'} <= existing:
        return
    conn.executescript("""CREATE TABLE IF NOT EXISTS breakdown_state(
        wo_number TEXT PRIMARY KEY,asset TEXT NOT NULL,status TEXT NOT NULL,
        updated_shift_id INTEGER NOT NULL,decision TEXT NOT NULL DEFAULT '',
        rts_logged INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS breakdown_manual_holds(
        asset TEXT PRIMARY KEY,held_by TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',
        held_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS breakdown_rts_events(
        wo_number TEXT PRIMARY KEY,asset TEXT NOT NULL,shift_id INTEGER NOT NULL,
        actor TEXT NOT NULL,recorded_at TEXT NOT NULL);
    """)

def _has_hold(conn,asset):
    if conn.execute("SELECT 1 FROM breakdown_manual_holds WHERE asset=?",(asset,)).fetchone():
        return True
    # Explicit manual OOS incidents stay open until a person marks them RTS.
    return any(x["asset"].strip().casefold()==asset.strip().casefold()
               for x in v31_oos.list_records(conn,open_only=True))

def unresolved(conn,asset,exclude_wo=""):
    return conn.execute("""SELECT 1 FROM breakdown_state
        WHERE lower(trim(asset))=lower(trim(?)) AND wo_number<>?
        AND (status='in process' OR (status='completed' AND decision<>'rts'))
        LIMIT 1""",(asset,exclude_wo)).fetchone() is not None

def _resolve(conn,wo,asset,shift_id,actor,*,confirm_manual=False,actor_name=""):
    row=conn.execute("SELECT * FROM breakdown_state WHERE wo_number=?",(wo,)).fetchone()
    if not row or row["rts_logged"] or unresolved(conn,asset,wo):
        return False
    # An explicit equipment hold cannot be bypassed by Cetaris closure or
    # a completed-WO review; it must be separately released first.
    if conn.execute("""SELECT 1 FROM breakdown_manual_holds
        WHERE lower(trim(asset))=lower(trim(?))""",(asset,)).fetchone():
        return False
    manual=[r for r in v31_oos.list_records(conn,open_only=True)
            if r["asset"].strip().casefold()==asset.strip().casefold()]
    if manual and not confirm_manual:
        return False
    # The completing shift explicitly confirmed physical RTS. Close matching
    # legacy/manual OOS incidents in the SAME transaction so they cannot
    # remain as stale duplicates on the global OOS board.
    shift=conn.execute("SELECT code FROM shifts WHERE id=?",(shift_id,)).fetchone()
    for item in manual:
        v31_oos.mark_rts(conn,item["id"],rts_by=actor,
            rts_by_name=actor_name or actor,shift_id=shift_id,
            shift_code=shift["code"] if shift else "",
            now_iso=_now(),notes="Linked breakdown RTS")
    conn.execute("""INSERT OR IGNORE INTO breakdown_rts_events
       (wo_number,asset,shift_id,actor,recorded_at) VALUES(?,?,?,?,?)""",
       (wo,asset,shift_id,actor,_now()))
    conn.execute("UPDATE breakdown_state SET rts_logged=1 WHERE wo_number=?",(wo,))
    # Never mutate historic closed-shift work. The current shift's matching
    # imported OOS row may be moved to RTS if the asset is truly resolved.
    conn.execute("""UPDATE report_wo SET status='RTS' WHERE shift_id=? AND wo_number=?
       AND status='OOS' AND is_manual=0
       AND EXISTS (SELECT 1 FROM shifts WHERE id=? AND open=1)""",
       (shift_id,wo,shift_id))
    return True

def sync(conn,rows,selected,accepted,shift_id):
    """V3 parity: track same WO across imports even if later closure is on
    a different shift; accept new WOs only if selected/attributed here.
    """
    ensure(conn)
    # A daily export can contain another shift's labor or newer WO status.
    # Only current-shift completion evidence can change the global breakdown
    # lifecycle. Activity-only rows remain visible in that shift's report,
    # but are NOT proof that it completed or returned the asset.
    accepted_keys={(str(r.get('wo_number') or '').strip(),
                    str(r.get('tech_name') or '').strip(),
                    str(r.get('status') or '').strip().casefold())
                   for r in accepted if not r.get('_activity_only')}
    grouped={}
    for raw in rows:
        if str(raw.get('rfr') or '').strip().casefold()!='breakdown':
            continue
        wo=str(raw.get('wo_number') or '').strip()
        asset=str(raw.get('asset_id') or '').strip()
        status=ALIASES.get(str(raw.get('status') or '').strip().casefold(),
                           str(raw.get('status') or '').strip().casefold())
        if not wo or not asset or status not in PRIORITY:
            continue
        if (wo,str(raw.get('tech_name') or '').strip(),
            str(raw.get('status') or '').strip().casefold()) not in accepted_keys:
            continue
        old=grouped.get(wo)
        if not old or PRIORITY[status]>PRIORITY[old[0]]:
            grouped[wo]=(status,asset)
    changes=0
    for wo,(status,asset) in grouped.items():
        previous=conn.execute("SELECT * FROM breakdown_state WHERE wo_number=?",(wo,)).fetchone()
        if previous and PRIORITY[status]<PRIORITY.get(previous['status'],0):
            continue
        renewed=(previous and status=='completed' and
                 previous['status']=='completed' and previous['decision']=='keep_oos'
                 and previous['updated_shift_id']!=shift_id)
        if previous and status==previous['status'] and asset==previous['asset'] and not renewed:
            continue
        # A later shift with NEW, genuinely attributed completion evidence
        # can review the same unfinished repair again. A reimport by the
        # original shift cannot silently reopen a decision.
        decision=("" if renewed else
                  previous['decision'] if previous and status==previous['status'] else '')
        logged=previous['rts_logged'] if previous else 0
        conn.execute("""INSERT INTO breakdown_state(
          wo_number,asset,status,updated_shift_id,decision,rts_logged,updated_at)
          VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(wo_number) DO UPDATE SET
          asset=excluded.asset,status=excluded.status,
          updated_shift_id=excluded.updated_shift_id,decision=excluded.decision,
          rts_logged=excluded.rts_logged,updated_at=excluded.updated_at""",
          (wo,asset,status,shift_id,decision,logged,_now()))
        changes+=1
        if status in ('in process','completed') and not (status=='completed' and decision=='rts'):
            # Ensure import doesn't falsely mark an unresolved asset RTS.
            conn.execute("""UPDATE report_wo SET status='OOS'
              WHERE shift_id=? AND wo_number=? AND is_manual=0""",(shift_id,wo))
    for wo,(status,asset) in grouped.items():
        if status=='closed':
            _resolve(conn,wo,asset,shift_id,'cetaris_closed')
        elif status=='completed':
            state=conn.execute('SELECT decision FROM breakdown_state WHERE wo_number=?',(wo,)).fetchone()
            if state and state['decision']=='rts':
                _resolve(conn,wo,asset,shift_id,'prior_review')
    return changes

def pending(conn,shift_id=None):
    ensure(conn)
    # A completed breakdown is reviewable only in its verified completion
    # shift, never merely by the shift that imported a later daily file.
    sql="""SELECT wo_number,asset,updated_shift_id
      FROM breakdown_state WHERE status='completed' AND decision=''"""
    params=()
    if shift_id is not None:
        sql+=" AND updated_shift_id=?"
        params=(shift_id,)
    return [dict(r) for r in conn.execute(sql+" ORDER BY updated_at",params)]

def decide(conn,wo,choice,shift_id,actor,actor_name=""):
    ensure(conn)
    if choice not in ('rts','keep_oos'):raise ValueError('Invalid RTS decision')
    state=conn.execute("SELECT * FROM breakdown_state WHERE wo_number=?",(wo,)).fetchone()
    if not state or state['status']!='completed' or state['decision']:
        raise KeyError('Breakdown not pending a decision')
    if state["updated_shift_id"]!=shift_id:
        raise ValueError("Only the documented completing shift can review this breakdown")
    if choice=='rts' and (
        unresolved(conn,state['asset'],wo) or
        conn.execute("""SELECT 1 FROM breakdown_manual_holds
            WHERE lower(trim(asset))=lower(trim(?))""",(state['asset'],)).fetchone()
    ):
        raise ValueError("Another active breakdown or OOS hold prevents RTS")
    resolved=False
    if choice=='rts':
        resolved=_resolve(conn,wo,state['asset'],shift_id,actor,
                          confirm_manual=True,actor_name=actor_name)
        if not resolved:
            raise ValueError("Unable to verify RTS while the equipment is held")
    conn.execute("UPDATE breakdown_state SET decision=?,updated_at=? WHERE wo_number=?",
                 (choice,_now(),wo))
    return {'wo_number':wo,'decision':choice,'resolved':resolved}

def hold(conn,asset,enabled,actor,reason=''):
    ensure(conn)
    if enabled:
        conn.execute("""INSERT INTO breakdown_manual_holds(asset,held_by,reason,held_at)
          VALUES(?,?,?,?) ON CONFLICT(asset) DO UPDATE SET
          held_by=excluded.held_by,reason=excluded.reason,held_at=excluded.held_at""",
          (asset,actor,reason,_now()))
    else:
        conn.execute("DELETE FROM breakdown_manual_holds WHERE asset=?",(asset,))
    return {'asset':asset,'hold':enabled}

def overview(conn,shift_id=None):
    ensure(conn)
    unresolved_rows=[dict(r) for r in conn.execute("""SELECT * FROM breakdown_state
      WHERE status='in process' OR (status='completed' AND decision<>'rts')
      ORDER BY updated_at DESC""")]
    hold_rows=[dict(r) for r in conn.execute('SELECT * FROM breakdown_manual_holds ORDER BY asset')]
    if shift_id is not None:
        rts=[dict(r) for r in conn.execute(
            'SELECT * FROM breakdown_rts_events WHERE shift_id=? ORDER BY recorded_at',(shift_id,))]
    else:rts=[]
    return {'open':unresolved_rows,'pending':pending(conn,shift_id),
            'holds':hold_rows,'resolved_this_shift':rts}
