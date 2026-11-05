"""Manually-reported Breakdown/OOS tracking: POST /api/oos, GET /api/oos,
POST /api/oos/{id}/rts.

Independent of Cetaris-imported work orders -- an MST can log an asset as
Down/Degraded with no WO behind it at all, it stays open across shifts
(global, not scoped to the logging shift), and any signed-in associate
(not just whoever logged it) can mark it RTS later, possibly from a
completely different shift.
"""
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
import app as m

from test_reporting import clock, enter

TZ = ZoneInfo("America/Indiana/Indianapolis")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    with TestClient(m.app) as c:
        yield c


def test_create_requires_an_active_shift(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    client.post("/api/login", json={"associate": "8972"})
    resp = client.post("/api/oos", json={"asset": "CONV-12", "cause": "Jammed belt"})
    assert resp.status_code == 409


def test_create_list_and_mark_rts_round_trip(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    created = client.post("/api/oos", json={
        "asset": "CONV-12", "cause": "Jammed belt", "notes": "Smoking slightly",
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["asset"] == "CONV-12"
    assert body["open"] == 1
    assert body["logged_by_name"]
    oos_id = body["id"]

    listed = client.get("/api/oos").json()
    assert any(r["id"] == oos_id for r in listed["open"])
    assert listed["resolved_this_shift"] == []

    resolved = client.post(f"/api/oos/{oos_id}/rts", json={"notes": "Belt replaced"})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["open"] == 0
    assert resolved.json()["rts_by_name"]

    listed_after = client.get("/api/oos").json()
    assert all(r["id"] != oos_id for r in listed_after["open"])
    assert any(r["id"] == oos_id for r in listed_after["resolved_this_shift"])


def test_cannot_mark_already_resolved_record_rts_again(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    oos_id = client.post("/api/oos", json={"asset": "CONV-12"}).json()["id"]
    assert client.post(f"/api/oos/{oos_id}/rts", json={}).status_code == 200
    again = client.post(f"/api/oos/{oos_id}/rts", json={})
    assert again.status_code == 409


def test_marking_nonexistent_record_rts_404s(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    assert client.post("/api/oos/999999/rts", json={}).status_code == 404


def test_different_associate_can_see_and_resolve_a_globally_open_record(client, tmp_path, monkeypatch):
    """The whole point: equipment logged down by one shift stays visible
    and resolvable by a completely different shift/day/associate."""
    import v31_roster
    clock(monkeypatch, day=26, hour=10)
    import csv
    with (tmp_path / "mst-roster.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Assoc. #", "Shift", "Job Title"])
        w.writerow(["First Tech", "555001", "S1", "MST"])
        w.writerow(["Second Tech", "555002", "S2", "MST"])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", force=True)

    assert client.post("/api/login", json={"associate": "555001", "intent": "direct"}).status_code == 200
    oos_id = client.post("/api/oos", json={"asset": "BALER-3", "cause": "Hydraulic leak"}).json()["id"]
    client.post("/api/logout", json={})

    clock(monkeypatch, day=26, hour=13)  # inside S2's window
    assert client.post("/api/login", json={"associate": "555002", "intent": "direct"}).status_code == 200
    listed = client.get("/api/oos").json()
    assert any(r["id"] == oos_id for r in listed["open"])
    resolved = client.post(f"/api/oos/{oos_id}/rts", json={})
    assert resolved.status_code == 200
    assert resolved.json()["rts_by_name"] == "Second Tech"
    assert resolved.json()["logged_by_name"] == "First Tech"


def test_stale_technician_owner_cannot_edit_manual_oos_after_mst_relief(client, tmp_path, monkeypatch):
    import csv
    import v31_roster
    clock(monkeypatch, day=26, hour=13)
    with (tmp_path / "mst-roster.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Assoc. #", "Shift", "Job Title"])
        w.writerow(["Assigned Non-MST", "555001", "S1", "Associate"])
        w.writerow(["Assigned MST", "555002", "S1", "MST"])
    v31_roster.sync(tmp_path, tmp_path / "v31.sqlite3", force=True)

    assert client.post('/api/login',json={'associate':'555001'}).status_code == 200
    owner_cookie=client.cookies.get(m.SESSION_COOKIE)
    oos_id=client.post('/api/oos',json={'asset':'TEST-001'}).json()['id']
    shift_id=client.get('/api/me').json()['selected']['id']
    client.post('/api/logout')

    assert client.post('/api/login',json={'associate':'555002'}).json()['takeover_required']
    assert client.post('/api/shifts/takeover',json={'shift_id':shift_id}).status_code == 200
    mst_cookie=client.cookies.get(m.SESSION_COOKIE)
    client.cookies.set(m.SESSION_COOKIE,owner_cookie)
    assert client.get('/api/oos').status_code == 403
    assert client.post('/api/oos',json={'asset':'TEST-002'}).status_code == 403
    assert client.post(f'/api/oos/{oos_id}/rts',json={}).status_code == 403
    client.cookies.set(m.SESSION_COOKIE,mst_cookie)
    assert client.post(f'/api/oos/{oos_id}/rts',json={}).status_code == 200


def test_manual_oos_requires_report_inside_shift_window(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, 'S1')
    clock(monkeypatch, day=26, hour=18)  # outside S1's 5 PM closing grace
    assert client.post('/api/oos',json={'asset':'TEST-001'}).status_code == 409
    assert client.get('/api/oos').status_code == 409


def test_manual_oos_changes_invalidate_previous_passdown_before_end_shift(client, monkeypatch):
    clock(monkeypatch, day=26, hour=13)
    enter(client, 'S1')
    draft = 'IND3 S1 SHIFT HAPPENINGS: Verified shift passdown before OOS.'
    assert client.post('/api/shifts/report',json={'content':draft}).status_code == 200
    oos = client.post('/api/oos',json={'asset':'TEST-001'})
    assert oos.status_code == 200
    assert client.post('/api/shifts/end',json={'acknowledge_warnings':True}).status_code == 409
    assert client.post('/api/shifts/report',json={'content':draft}).status_code == 200
    assert client.post(f"/api/oos/{oos.json()['id']}/rts",json={}).status_code == 200
    assert client.post('/api/shifts/end',json={'acknowledge_warnings':True}).status_code == 409
    assert client.post('/api/shifts/report',json={'content':draft}).status_code == 200
    assert client.post('/api/shifts/end',json={'acknowledge_warnings':True}).status_code == 200


def test_resolved_manual_oos_is_in_shift_rts_kpi_and_eml(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    shift_id = enter(client, "S1")
    oos_id = client.post("/api/oos", json={"asset": "CONV-12", "cause": "Jammed belt"}).json()["id"]
    assert client.post(f"/api/oos/{oos_id}/rts", json={}).status_code == 200
    saved = client.post("/api/shifts/report",
        json={"content": "IND3 S1 SHIFT HAPPENINGS: None."})
    assert saved.status_code == 200, saved.text
    ended = client.post("/api/shifts/end",json={"acknowledge_warnings":True})
    assert ended.status_code == 200, ended.text

    eml = client.get(f"/api/history/shifts/{shift_id}/eml")
    assert eml.status_code == 200
    section=eml.text.split('>Returned to Service</strong>',1)[1].split(
        '>Current Out of Service</strong>',1)[0]
    assert '<strong>CONV-12</strong>' in section
    assert '(manually reported)' not in eml.text
    # A manual OOS resolved on this shift is explicitly confirmed RTS and
    # belongs in its email and RTS KPI, without inventing a breakdown WO.
    with m._db() as conn:
        import v31_asset_status
        summary=v31_asset_status.summary(conn,shift_id)
    assert any(r['asset']=='CONV-12' for r in summary['rts'])
    assert summary['breakdowns']==[]



def test_v2_asset_catalog_and_unified_oos_rts_lifecycle(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,'S1')
    catalog=client.get('/api/assets/catalog')
    assert catalog.status_code==200,catalog.text
    assert len(catalog.json()['assets'])>=1700
    assert 'PERCART0010' in catalog.json()['assets']
    assert 'BLDG' in catalog.json()['assets']

    created=client.post('/api/oos',json={'asset':'PERCART0010'})
    assert created.status_code==200,created.text
    assert client.post('/api/oos',json={'asset':'percart0010'}).status_code==409
    overview=client.get('/api/assets/status').json()
    assert [r['asset'] for r in overview['oos']]==['PERCART0010']
    assert overview['rts']==[] and overview['breakdowns']==[]

    # Current OOS remains visible on S2. Then a manually verified return
    # records RTS for S2 without fabricating a repaired breakdown WO.
    clock(monkeypatch,day=26,hour=14)
    s2=client.post('/api/shifts/start',json={'code':'S2'})
    assert s2.status_code==200,s2.text
    assert 'PERCART0010' in [r['asset'] for r in client.get('/api/assets/status').json()['oos']]
    returned=client.post('/api/oos/rts',json={'asset':'PERCART0010'})
    assert returned.status_code==200,returned.text
    assert returned.json()['direct_rts']==0 # An actual prior OOS record was resolved
    result=client.get('/api/assets/status').json()
    assert not result['oos']
    assert [r['asset'] for r in result['rts']]==['PERCART0010']
    assert result['breakdowns']==[]
    preview=client.get('/api/shifts/report/preview')
    assert preview.status_code==200,preview.text
    html=preview.json()['html']
    breakdown_section=html.split('>Returned to Service</strong>',1)[1].split(
        '>Current Out of Service</strong>',1)[0]
    assert '<strong>PERCART0010</strong>' in breakdown_section
    assert '(manually reported)' not in html


def test_status_kpis_exclude_routine_repairs_and_include_manual_rts_in_email(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,'S1')
    for asset,category in [('PERCART0010','Breakdown'),('VOIDFILL','Proactive Repair')]:
        saved=client.post('/api/reporting/manual',json={
            'asset':asset,'cause':'Equipment corrected and verified',
            'remarks':'Tested safe for operation','category':category,'status':'RTS'})
        assert saved.status_code==200,saved.text
    # A third direct RTS observation without a backed breakdown is valid
    # as an operational update, but not a breakdown completion.
    direct=client.post('/api/oos/rts',json={'asset':'BALER0001'})
    assert direct.status_code==200,direct.text
    assert direct.json()['direct_rts']==1

    stats=client.get('/api/assets/status')
    assert stats.status_code==200,stats.text
    body=stats.json()
    assert {x['asset'] for x in body['rts']}=={
        'PERCART0010','BALER0001'}
    assert {x['asset'] for x in body['breakdowns']}=={'PERCART0010'}
    assert body['oos']==[]
    preview=client.get('/api/shifts/report/preview')
    assert preview.status_code==200,preview.text
    section=preview.json()['html'].split('>Returned to Service</strong>',1)[1].split(
        '>Current Out of Service</strong>',1)[0]
    assert '<strong>PERCART0010</strong>' in section
    assert 'VOIDFILL' not in section
    assert '<strong>BALER0001</strong>' in section
    assert '(manually reported)' not in preview.json()['html']


def test_rts_direct_cannot_override_an_unresolved_cetaris_breakdown(client,monkeypatch):
    clock(monkeypatch,day=26,hour=10)
    enter(client,'S1')
    import v31_breakdown
    with m._db() as conn:
        v31_breakdown.ensure(conn)
        conn.execute("""INSERT INTO breakdown_state(
            wo_number,asset,status,updated_shift_id,decision,rts_logged,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            ('BD100','BLDG','in process',1,'',0,'2026-10-26T10:00:00-04:00'))
    res=client.post('/api/oos/rts',json={'asset':'BLDG'})
    assert res.status_code==409
    assert 'breakdown' in res.json()['detail'].lower()
    assert any(x['asset']=='BLDG' for x in client.get('/api/assets/status').json()['oos'])


def test_email_current_oos_matches_live_dashboard_across_shifts(client, monkeypatch):
    """Manual OOS plus unresolved historical breakdown must both appear in EML.

    The Current Out of Service section must use the facility-wide inventory,
    not only the current shift's work-order rows.
    """
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    manual = client.post("/api/oos", json={
        "asset": "BAG0001", "cause": "Paper jam awaiting repair",
    })
    assert manual.status_code == 200, manual.text

    # Simulate an unresolved earlier-shift Breakdown WO from the retained
    # historical worksheet, before it was added to breakdown_state tracking.
    with m._db() as conn:
        import v31_reporting
        v31_reporting._table(conn)
        prior_id = conn.execute(
            """INSERT INTO shifts(code, shift_day, owner, open, created_at)
               VALUES(?, ?, ?, ?, ?)""",
            ("S1", "2026-10-19", "8972", 0,
             "2026-10-19T10:00:00-04:00"),
        ).lastrowid
        conn.execute(
            """INSERT INTO report_wo(
                shift_id, wo_number, asset, category, status, imported_at)
               VALUES(?, ?, ?, ?, ?, ?)""",
            (prior_id, "BD-700", "CONV-12", "Breakdown", "OOS",
             "2026-10-19T10:30:00-04:00"),
        )

    status = client.get("/api/assets/status")
    assert status.status_code == 200, status.text
    expected = {row["asset"] for row in status.json()["oos"]}
    assert expected == {"BAG0001", "CONV-12"}

    preview = client.get("/api/shifts/report/preview")
    assert preview.status_code == 200, preview.text
    html = preview.json()["html"]
    section = html.split(">Current Out of Service</strong>", 1)[1].split("</ul>", 1)[0]
    assert section.count("<li ") == len(expected)
    for asset in expected:
        assert f"<strong>{asset}</strong>" in section
    assert "(manually reported)" not in section

    saved = client.post("/api/shifts/report", json={"content": "Current OOS verified"})
    assert saved.status_code == 200, saved.text
    eml = client.get("/api/shifts/report/download")
    assert eml.status_code == 200, eml.text
    for asset in expected:
        assert f"<strong>{asset}</strong>".encode() in eml.content
