"""Verified equipment returned to service during a specific shift.

Only genuine breakdown lifecycle RTS events or explicitly recorded manual
returns qualify. Routine closed Proactive Repair/Process Interruption WOs
never imply physical lockout or RTS. Preserve one row per asset.
"""
from __future__ import annotations
import v31_breakdown
import v31_oos


def confirmed_returns(conn, shift_id):
    breakdown=v31_breakdown.overview(conn,shift_id)
    events={}
    # Cetaris closed breakdowns or individually approved completed breakdowns.
    for row in breakdown['resolved_this_shift']:
        asset=(row.get('asset') or '').strip()
        if asset:
            events[asset.casefold()]={'asset':asset,'source':'Confirmed breakdown',
                 'wo_number':row['wo_number'],'breakdown':True}
    # A manually entered Breakdown happening explicitly marked RTS counts
    # as an inspected return, without treating a Proactive Repair WO as RTS.
    for row in conn.execute("""SELECT asset,wo_number FROM report_wo
        WHERE shift_id=? AND is_manual=1 AND excluded=0 AND selection_hidden=0
          AND category='Breakdown' AND status='RTS'""",(shift_id,)):
        asset=(row['asset'] or '').strip()
        if asset:
            events.setdefault(asset.casefold(),{'asset':asset,'source':'Manual breakdown RTS',
                 'wo_number':row['wo_number'],'breakdown':True})
    # The independent + Add / Mark RTS flow is explicitly operator confirmed.
    # A manually unlocked asset is valid in RTS even with no Cetaris WO.
    for row in v31_oos.resolved_this_shift(conn,shift_id):
        asset=(row.get('asset') or '').strip()
        if asset:
            events.setdefault(asset.casefold(),{'asset':asset,'source':'Manual RTS',
                'wo_number':'','breakdown':False})
    return sorted(events.values(),key=lambda x:x['asset'].casefold())
