"""Recorded V3.1 reliability analytics -- no fabricated downtime or uptime."""
import csv
from datetime import date

import app as m
import v31_analytics
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


def test_equipment_reliability_deduplicates_cross_shift_wo_and_excludes_hidden(client,monkeypatch):
    """One physical Breakdown WO may be reported by two overlapping shifts.
    A second distinct Breakdown WO is a repeat; duplicate reporting is not.
    """
    clock(monkeypatch,day=26,hour=10)
    sid1=enter(client,'S1')
    clock(monkeypatch,day=26,hour=13)
    second=client.post('/api/shifts/start',json={'code':'S2'})
    assert second.status_code==200,second.text
    sid2=second.json()['id']

    records=[
      (sid1,'B101','CR0005','Breakdown','Photoeye not sensing','RTS',0,0),
      (sid2,'B101','cr0005','Breakdown','Photoeye not sensing','RTS',0,0),
      (sid2,'B102','CR0005','Breakdown','Photoeye not sensing','RTS',0,0),
      (sid1,'P103','CR0005','Proactive Repair','Tightened loose connector','RTS',0,0),
      (sid1,'B104','VOIDFILL0201','Breakdown','Unwind motor alarm','OOS',0,0),
      (sid1,'R105','','Work Request','Unlabeled asset','PENDING',0,0),
      (sid1,'B106','VOIDFILL0201','Breakdown','Hidden repair','RTS',0,1),
      (sid1,'B107','VOIDFILL0201','Breakdown','Excluded repair','RTS',1,0),
    ]
    with m._db() as conn:
        for sid,wo,asset,category,cause,status,excluded,hidden in records:
            conn.execute("""INSERT INTO report_wo
                  (shift_id,wo_number,asset,category,cause,status,excluded,
                   selection_hidden,imported_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (sid,wo,asset,category,cause,status,excluded,hidden,
                 '2026-10-26T14:00:00'))
    response=client.get('/api/analytics/summary?period=all')
    assert response.status_code==200,response.text
    data=response.json()
    totals=data['totals']
    assert totals['work_orders']==6   # report entries across S1/S2
    assert totals['distinct_wo_ids']==5
    assert totals['assets_reported']==2
    assert totals['missing_asset_entries']==1
    assert totals['repeat_breakdown_assets']==1
    assert totals['proactive_repairs']==1

    assert data['top_assets'][0]['asset']=='CR0005'
    assert data['top_assets'][0]['work_orders']==3
    assert data['top_assets'][0]['breakdowns']==2
    assert data['top_assets'][0]['proactive_repairs']==1
    assert data['top_assets'][0]['shifts']==['S1','S2']
    assert data['top_assets'][1]['asset']=='VOIDFILL0201'
    assert data['top_assets'][1]['work_orders']==1
    assert data['repeat_breakdown_assets'][0]['asset']=='CR0005'
    assert data['repeat_breakdown_assets'][0]['repeat_breakdown_wos']==1
    assert data['common_breakdown_descriptions'][0]=={
        'description':'Photoeye not sensing','work_orders':2
    }
    assert sum(x['work_orders'] for x in data['by_shift'])==6
    assert next(x for x in data['by_shift'] if x['code']=='S2')['breakdowns']==2
    assert next(x for x in data['by_shift'] if x['code']=='S1')['pending_closure']==1
    assert data['scope'].startswith('V3.1 database only')
    assert any('not independently verified root causes' in x for x in data['notes'])


def test_equipment_reliability_date_bounds_and_no_fake_failure_data(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    sid=enter(client,'S1')
    with m._db() as conn:
        conn.execute("""INSERT INTO report_wo
             (shift_id,wo_number,asset,category,cause,imported_at)
             VALUES(?,?,?,?,?,?)""",
             (sid,'M100','TEST-01','Proactive Repair','Sensor cleaned','2026-10-26'))
        result=v31_analytics.summarize(
            conn,owner='8972',period='7d',today=date(2026,10,26))
    assert result['totals']['work_orders']==1
    assert result['repeat_breakdown_assets']==[]
    assert result['common_breakdown_descriptions']==[]
    assert result['top_assets'][0]['breakdowns']==0
    assert result['totals']['repeat_breakdown_assets']==0
    assert all('uptime' not in k.lower() and 'mttr' not in k.lower()
               for k in result['totals'])


def test_analytics_layout_three_sections_and_no_ten_card_wall(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,'S1')
    page=client.get('/shift-report.html')
    assert page.status_code==200
    for title in ('Maintenance Performance','Equipment Reliability','Shift Performance',
                  'Most Repaired Assets','Repeat Breakdown Assets','Shift Comparison'):
        assert title in page.text
    script=client.get('/web/analytics.js').text
    assert "'repeat_breakdown_assets'" in script or 'data.repeat_breakdown_assets' in script
    assert script.count("['work_orders','Reported Work Orders'")==1
    assert script.count("['completed_pms','Completed PMs'")==1
    assert 'textContent' in script
