"""End Shift readiness, history integrity and handover test cases."""
import hashlib
import json

import app as m
from test_reporting import client, clock, enter, row, upload, import_techs


def test_end_shift_blocks_unconfirmed_staged_export_and_requires_acknowledgement(client,monkeypatch):
    clock(monkeypatch)
    sid=enter(client)
    assert upload(client,[row('AUDIT101')]).status_code==200
    saved=client.post('/api/shifts/report',json={})
    assert saved.status_code==200,saved.text
    readiness=client.get('/api/shifts/end/readiness')
    assert readiness.status_code==200
    assert not readiness.json()['ready']
    assert any('staged' in x for x in readiness.json()['blockers'])
    assert client.post('/api/shifts/end',json={'acknowledge_warnings':True}).status_code==409
    assert import_techs(client,['Synthetic A']).status_code==200
    assert client.post('/api/shifts/report',json={}).status_code==200
    review=client.get('/api/shifts/end/readiness').json()
    assert review['ready'] and review['hash_verified']
    assert review['requires_acknowledgement']
    assert any('LOTO' in s for s in review['warnings'])
    assert client.post('/api/shifts/end').status_code==409
    ended=client.post('/api/shifts/end',json={'acknowledge_warnings':True})
    assert ended.status_code==200,ended.text
    assert ended.json()['shift_id']==sid
    with m._db() as conn:
        acknowledged=conn.execute('SELECT * FROM shift_end_review WHERE shift_id=?',(sid,)).fetchone()
    assert acknowledged['actor']=='8972'
    assert 'LOTO' in acknowledged['warnings_json']


def test_end_shift_rejects_corrupted_saved_outlook_blob(client,monkeypatch):
    clock(monkeypatch)
    sid=enter(client)
    saved=client.post('/api/shifts/report',json={})
    assert saved.status_code==200
    with m._db() as conn:
        conn.execute('UPDATE shifts SET report_eml=? WHERE id=?',
                     (b'CORRUPTED-EML',sid))
    review=client.get('/api/shifts/end/readiness').json()
    assert not review['ready'] and not review['hash_verified']
    assert any('integrity' in x for x in review['blockers'])
    assert client.post('/api/shifts/end',json={'acknowledge_warnings':True}).status_code==409


def test_pending_wo_and_carryover_appear_in_saved_outlook_handover(client,monkeypatch):
    clock(monkeypatch)
    sid=enter(client)
    manual=client.post('/api/reporting/manual',json={
        'asset':'SPUR-11','cause':'Awaiting replacement sensor','status':'PENDING',
        'category':'Work Request'})
    assert manual.status_code==200,manual.text
    wo=manual.json()['wo_number']
    assert client.post('/api/reporting/carryovers/flag',json={
        'wo_number':wo,'enabled':True
    }).status_code==200
    saved=client.post('/api/shifts/report',json={})
    assert saved.status_code==200,saved.text
    html=client.get('/api/shifts/report').json()['content']
    assert 'Open Follow-ups / Shift Carry-overs' in html
    assert 'SPUR-11' in html
    assert 'Pending completion / closure' in html
    assert 'Flagged for shift handover' in html
    assert 'Returned to Service' not in html.split('Open Follow-ups / Shift Carry-overs')[1].split('Reliable Operations')[0]


def test_history_count_excludes_selection_hidden_wo(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    assert upload(client,[row('HIST101',tech='Synthetic A'),
                          row('HIST102',tech='Synthetic B')]).status_code==200
    assert import_techs(client,['Synthetic A','Synthetic B']).status_code==200
    assert import_techs(client,['Synthetic A']).status_code==200
    data=client.get('/api/history/shifts').json()
    assert data['rows'][0]['happenings']==1
    state=client.get('/api/reporting/state').json()
    by={r['wo_number']:r for r in state['rows']}
    assert by['HIST102']['selection_hidden']
    assert not by['HIST101']['selection_hidden']


def test_unified_asset_status_ui_is_served_to_logged_shift_owner(client,monkeypatch):
    clock(monkeypatch)
    enter(client)
    html=client.get('/shift-report.html').text
    assert '<script defer src="/web/manual-oos.js"></script>' in html
    assert '<script defer src="/web/breakdown-lifecycle.js"></script>' not in html
    assert 'id="v31-manual-oos-root"' in html
    assert 'id="v31-rts-metric"' in html
    assert 'id="v31-oos-metric"' in html
    assert 'id="v31-breakdown-metric"' in html
    asset_view=client.get('/web/manual-oos.js')
    assert asset_view.status_code==200
    assert '/api/assets/status' in asset_view.text
    assert '/api/assets/catalog' in asset_view.text
    assert 'Mark OOS' in asset_view.text and 'Mark RTS' in asset_view.text
