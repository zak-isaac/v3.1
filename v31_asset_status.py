"""Unified read-only live OOS/RTS and verified-breakdown KPI evidence.

OOS persists across shift boundaries; RTS shows *this shift's* events.
Only a breakdown actually confirmed RTS qualifies for the Breakdown KPI.
These counts are informational, not an instruction to change equipment status.
"""
from __future__ import annotations

import v31_oos
import v31_breakdown
import v31_reporting
import v31_returned_to_service


def _key(asset):
    return str(asset or "").strip().casefold()


def summary(conn, shift_id):
    v31_reporting._table(conn)
    manual_open = v31_oos.list_records(conn, open_only=True)
    manual_rts = v31_oos.resolved_this_shift(conn, shift_id)
    breakdown = v31_breakdown.overview(conn, shift_id)
    shift_rows = [dict(row) for row in conn.execute("""
        SELECT asset,wo_number,category,status,is_manual FROM report_wo
        WHERE shift_id=? AND excluded=0 AND selection_hidden=0 AND trim(asset)<>''
        ORDER BY id""", (shift_id,))]
    # A prior unresolved OOS may originate in any shift, not just today's
    # worksheet. Use each asset's most recently recorded shift work status.
    history = conn.execute("""
        SELECT w.asset,w.wo_number,w.category,w.status,w.is_manual,s.shift_day,
               w.work_at,w.imported_at,w.id
        FROM report_wo w JOIN shifts s ON s.id=w.shift_id
        WHERE w.excluded=0 AND w.selection_hidden=0 AND trim(w.asset)<>''
          AND w.status IN ('OOS','RTS') AND w.category='Breakdown'
        ORDER BY s.shift_day,w.work_at,w.imported_at,w.id
    """).fetchall()
    latest={}
    for row in history:
        latest[_key(row['asset'])] = dict(row)

    # The latest explicit manual action overrides stale generic imported
    # status. A genuine open Cetaris breakdown or explicit hold still wins.
    last_manual={}
    for item in v31_oos.list_records(conn, open_only=False):
        key=_key(item['asset'])
        if key not in last_manual or item['id']>last_manual[key]['id']:
            last_manual[key]=item

    oos={}
    for key,row in latest.items():
        if row['status']!='OOS' or (key in last_manual and not last_manual[key]['open']):
            continue
        oos[key]={'asset':row['asset'],'source':'Cetaris WO',
                  'wo_number':row['wo_number'],'reason':'Open work order'}
    for row in manual_open:
        key=_key(row['asset'])
        oos[key]={'asset':row['asset'],'source':'Manually reported',
                  'wo_number':'','reason':'Marked OOS',
                  'manual_oos_id':row['id']}
    for row in breakdown['open']:
        key=_key(row['asset'])
        oos[key]={'asset':row['asset'],'source':'Cetaris breakdown',
                  'wo_number':row['wo_number'],'reason':
                    'Completed — awaiting confirmation' if row['status']=='completed'
                    else 'In Process',
                  'pending_decision':row['status']=='completed' and not row['decision'],
                  'decision':row['decision']}
    for row in breakdown['holds']:
        key=_key(row['asset'])
        prior=oos.get(key,{})
        oos[key]={'asset':row['asset'],'source':'Manual hold',
                  'wo_number':prior.get('wo_number',''),
                  'reason':row.get('reason') or 'Hold remains active',
                  'hold':True,'pending_decision':prior.get('pending_decision',False)}

    # Keep displayed RTS and the Outlook email in sync via one authoritative
    # list. Count breakdown WOs only after a documented RTS confirmation.
    verified=v31_returned_to_service.confirmed_returns(conn,shift_id)
    rts={_key(item['asset']):item for item in verified}
    confirmed={item['wo_number']:item for item in verified
               if item['breakdown'] and item['wo_number']}

    return {'oos':sorted(oos.values(),key=lambda item:item['asset'].casefold()),
            'rts':sorted(rts.values(),key=lambda item:item['asset'].casefold()),
            'breakdowns':sorted(confirmed.values(),key=lambda item:item['asset'].casefold()),
            'pending':breakdown['pending']}
