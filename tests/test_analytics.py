"""Recorded V3.1 reliability analytics -- no fabricated downtime or uptime."""
import csv

import v31_roster
from test_reporting import client, clock, enter, upload, row, import_techs


def test_analytics_empty_state_is_zero_and_honestly_has_no_coverage(client, monkeypatch):
    clock(monkeypatch)
    assert client.post('/api/login',json={'associate':'8972'}).status_code==200
    resp=client.get('/api/analytics/summary?period=30d')
    assert resp.status_code==200,resp.text
    data=resp.json()
    assert data['totals']['shifts']==0
    assert data['totals']['work_orders']==0
    assert data['totals']['report_coverage_pct'] is None
    assert data['by_category']==[]
    assert data['trend']==[]
    assert len(data['by_shift'])==5
    assert all(r['work_orders']==0 for r in data['by_shift'])


def test_analytics_real_shift_workload_manual_pm_and_report_snapshot(client, monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,'S1')
    assert upload(client,[
        row('R100',category='Proactive Repair'),
        row('R200',category='Breakdown',status='In Process',
            started='2026-10-26 09:00',serviced=None),
        row('PM500',category='Preventive Maintenance',pm='RNPK.001.PKM'),
    ]).status_code==200
    assert import_techs(client,['Synthetic A']).status_code==200
    assert client.post('/api/reporting/manual',json={
       'asset':'CONV-92','cause':'Found roller wear during PM','category':'Proactive Repair',
       'status':'RTS'
    }).status_code==200
    source=b'WO Number,PM Due Date,Asset #\nPM600,10/27/2026,BALER-1\n'
    assert client.post('/api/pm-needing-completion/upload',
             files={'file':('unfinished.csv',source,'text/csv')}).status_code==200
    assert client.post('/api/shifts/report',json={}).status_code==200
    payload=client.get('/api/analytics/summary?period=7d').json()
    total=payload['totals']
    assert total['shifts']==1
    assert total['work_orders']==3
    assert total['breakdowns']==1
    assert total['manual_happenings']==1
    assert total['completed_pms']==1
    assert total['pm_needing_completion']==1
    assert total['saved_passdowns']==1
    assert total['report_coverage_pct']==100.0
    assert total['closed_without_passdown']==0
    assert next(r for r in payload['by_shift'] if r['code']=='S1')['work_orders']==3
    assert sum(r['count'] for r in payload['by_category'])==3
    assert payload['scope'].startswith('V3.1 database only')
    assert all('MTTR' not in str(v) for v in total.values())


def test_analytics_only_shows_own_shift_to_regular_associates(client,tmp_path,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,'S1')
    assert upload(client,[row('OWNER_A')]).status_code==200
    assert import_techs(client,['Synthetic A']).status_code==200
    client.post('/api/logout')
    with (tmp_path/'mst-roster.csv').open('w',newline='') as output:
        w=csv.writer(output)
        w.writerow(['Name','Assoc. #','Shift','Job Title'])
        w.writerow(['Different Tech','555002','S2','Associate'])
    v31_roster.sync(tmp_path,tmp_path/'v31.sqlite3',set(__import__('app').MANAGEMENT_ASSOCIATES),force=True)
    clock(monkeypatch,day=26,hour=13)
    assert client.post('/api/login',json={'associate':'555002','intent':'direct'}).status_code==200
    data=client.get('/api/analytics/summary').json()
    assert data['totals']['shifts']==1
    assert data['totals']['work_orders']==0
    assert next(r for r in data['by_shift'] if r['code']=='S1')['shifts']==0
    page=client.get('/shift-report.html')
    assert page.status_code==200
    assert 'id="v31-analytics-screen"' in page.text
    assert '/web/analytics.js' in page.text
    assert '/web/analytics.css' in page.text


def test_invalid_analytics_period_rejected(client,monkeypatch):
    clock(monkeypatch)
    client.post('/api/login',json={'associate':'8972'})
    assert client.get('/api/analytics/summary?period=tomorrow').status_code==422
