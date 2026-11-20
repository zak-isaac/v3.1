"""V3-inspired Cetaris breakdown lifecycle with confirmed RTS and manual OOS holds.

C/D/I/W/O/P Cetaris WO statuses remain independent of equipment return.
Active repairs (including Parts Pending) persist; Completed/Closed require
explicit human RTS or keep-OOS review by an active shift, regardless of
which shift started or last imported the work order. Explicit holds remain.
Never use unrelated maintenance as RTS evidence.
Global state survives shift boundaries; historical snapshots are immutable.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

import v31_oos
from v31_cetaris_codes import normalize_wo_status, normalize_primary_rfr

TZ=ZoneInfo("America/Indiana/Indianapolis")
# These are not a strict chronological ladder: P -> I and I -> P are both
# legitimate, and W/O are intermediate statuses, not repair completion.
PRIORITY={"in process":1,"parts pending":1,"manager review required":1,
          "outside service":1,"completed":2,"closed":3}
ACTIVE=tuple(status for status,level in PRIORITY.items() if level == 1)

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
        AND rts_logged=0 AND status IN
          ('in process','parts pending','manager review required','outside service',
           'completed','closed')
        LIMIT 1""",(asset,exclude_wo)).fetchone() is not None

def _resolve(conn,wo,asset,shift_id,actor,*,confirm_manual=False,actor_name=""):
    row=conn.execute("SELECT * FROM breakdown_state WHERE wo_number=?",(wo,)).fetchone()
    if not row or row["rts_logged"]:
        return False
    # Other repair WOs on the same asset remain independently tracked.
    # Their existence cannot block an operator-confirmed return of THIS
    # completed WO or make an unrelated WO appear repaired.
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
    # Use exact accepted labor-row identity from the current import.
    # WO+tech+status is NOT unique: a monthly export can have several days
    # with the same technician and status, only one attributable here.
    accepted_ids={id(r) for r in accepted if not r.get('_activity_only')}
    grouped={}
    for raw in rows:
        if normalize_primary_rfr(raw.get('rfr'))[1]!='01':
            continue
        wo=str(raw.get('wo_number') or '').strip()
        asset=str(raw.get('asset_id') or '').strip()
        status=normalize_wo_status(raw.get('status'))[0].casefold()
        if not wo or not asset or status not in PRIORITY:
            continue
        if id(raw) not in accepted_ids:
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
        # Never overwrite report_wo's Cetaris C/D/I/W/O/P work status with
        # an equipment status. Physical OOS is recorded separately by manual
        # OOS, an explicit hold, or a keep-OOS review decision.
    # No automatic RTS on Closed. Both D and C require documented
    # physical RTS confirmation from an active shift. Later administrative
    # closure cannot create a duplicate return or change historical reports.
    return changes

def pending(conn,shift_id=None):
    ensure(conn)
    # Every current shift may verify a still-unresolved completed breakdown.
    # Never restrict the physical equipment status to the originating shift.
    return [dict(r) for r in conn.execute("""SELECT wo_number,asset,status,updated_shift_id
      FROM breakdown_state WHERE status IN ('completed','closed')
        AND decision='' AND rts_logged=0 ORDER BY updated_at""")]

def decide(conn,wo,choice,shift_id,actor,actor_name=""):
    ensure(conn)
    if choice not in ('rts','keep_oos'):raise ValueError('Invalid RTS decision')
    state=conn.execute("SELECT * FROM breakdown_state WHERE wo_number=?",(wo,)).fetchone()
    if (not state or state['status'] not in ('completed','closed') or
            state['rts_logged'] or state['decision'] not in ('','keep_oos')):
        raise KeyError('Breakdown not pending a decision')
    if state['decision']=='keep_oos' and choice=='keep_oos':
        return {'wo_number':wo,'decision':choice,'resolved':False}
    # A later shift may verify physical RTS even when another shift
    # performed the original work. A separate open Breakdown WO is not an
    # automatic equipment lockout. Explicit holds must be released first.
    if choice=='rts' and conn.execute("""SELECT 1 FROM breakdown_manual_holds
            WHERE lower(trim(asset))=lower(trim(?))""",(state['asset'],)).fetchone():
        raise ValueError("Release the explicit OOS hold before confirming RTS")
    resolved=False
    if choice=='rts':
        resolved=_resolve(conn,wo,state['asset'],shift_id,actor,
                          confirm_manual=True,actor_name=actor_name)
        if not resolved:
            raise ValueError("Unable to verify RTS while the equipment is held")
    conn.execute("UPDATE breakdown_state SET decision=?,updated_at=? WHERE wo_number=?",
                 (choice,_now(),wo))
    return {'wo_number':wo,'decision':choice,'resolved':resolved}

def confirm_completed_asset_rts(conn,asset,shift_id,actor,actor_name=""):
    """Reconcile finished Breakdown WOs when a technician manually marks RTS.

    Completion alone never triggers this. Only the explicit physical RTS
    action does. Other In Process/Parts Pending WOs stay in the Breakdown
    list; their presence does not block a return of a completed repair.
    """
    ensure(conn)
    completed=conn.execute("""SELECT wo_number FROM breakdown_state
        WHERE lower(trim(asset))=lower(trim(?)) AND rts_logged=0
          AND status IN ('completed','closed')
        ORDER BY updated_at""",(asset,)).fetchall()
    confirmed=[]
    for item in completed:
        wo=item["wo_number"]
        if _resolve(conn,wo,asset,shift_id,actor,
                    confirm_manual=True,actor_name=actor_name):
            conn.execute("""UPDATE breakdown_state
                SET decision='rts',updated_at=? WHERE wo_number=?""",
                (_now(),wo))
            confirmed.append(wo)
    return confirmed


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
      WHERE rts_logged=0 AND status IN ('in process','parts pending',
        'manager review required','outside service','completed','closed')
      ORDER BY updated_at DESC""")]
    hold_rows=[dict(r) for r in conn.execute('SELECT * FROM breakdown_manual_holds ORDER BY asset')]
    if shift_id is not None:
        rts=[dict(r) for r in conn.execute(
            'SELECT * FROM breakdown_rts_events WHERE shift_id=? ORDER BY recorded_at',(shift_id,))]
    else:rts=[]
    return {'open':unresolved_rows,'pending':pending(conn,shift_id),
            'holds':hold_rows,'resolved_this_shift':rts}
