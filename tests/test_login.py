"""V3.1 shift-bound login, signed-out screens, 60-minute grace and overnight windows."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
import app as m

ZONE = ZoneInfo('America/Indiana/Indianapolis')


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZONE)


def freeze(monkeypatch, clock):
    real_datetime = datetime

    class FakeDateTime:
        combine = real_datetime.combine

        @staticmethod
        def now(tz):
            return clock

    monkeypatch.setattr(m, 'datetime', FakeDateTime)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'PRIVATE', tmp_path)
    monkeypatch.setattr(m, 'SESSION_SECRET_FILE', tmp_path / 'session_secret')
    monkeypatch.setattr(m, 'DB_FILE', tmp_path / 'shifts.sqlite3')
    with TestClient(m.app) as c:
        yield c


def login(client, assoc='8972'):
    r = client.post('/api/login', json={'associate':assoc})
    assert r.status_code == 200, r.text
    return r.json()


def test_only_approved_local_associate_numbers(client):
    assert set(m.roster()) == {'8972', '2759'}
    assert client.post('/api/login', json={'associate':'random'}).status_code == 422
    assert client.post('/api/login', json={'associate':'9999'}).status_code == 401
    assert client.post('/api/login', json={'associate':'8972'}).status_code == 200
    client.post('/api/logout')
    assert client.post('/api/login', json={'associate':'2759'}).status_code == 200


def test_one_hour_before_and_after_s1_and_s2():
    day = date(2026, 10, 27)  # Tuesday
    assert m.eligible_date('S1', at(2026,10,27,4,29)) is None
    assert m.eligible_date('S1', at(2026,10,27,4,30)) == day
    assert m.eligible_date('S1', at(2026,10,27,17,0)) == day
    assert m.eligible_date('S1', at(2026,10,27,17,1)) is None
    assert m.eligible_date('S2', at(2026,10,27,10,59)) is None
    assert m.eligible_date('S2', at(2026,10,27,11,0)) == day
    assert m.eligible_date('S2', at(2026,10,27,23,30)) == day
    assert m.eligible_date('S2', at(2026,10,27,23,31)) is None


def test_overnight_s3_includes_next_morning_and_grace():
    monday = date(2026,10,26)
    assert m.eligible_date('S3', at(2026,10,26,19,29)) is None
    assert m.eligible_date('S3', at(2026,10,26,19,30)) == monday
    assert m.eligible_date('S3', at(2026,10,27,7,59)) == monday
    assert m.eligible_date('S3', at(2026,10,27,8,0)) == monday
    assert m.eligible_date('S3', at(2026,10,27,8,1)) is None
    details = m.window_info('S3', monday)
    assert details['scheduled_end'].startswith('2026-10-27T07:00')
    assert details['closes_at'].startswith('2026-10-27T08:00')


def test_weekend_s4_and_s5_and_overnight():
    friday = date(2026,10,30)
    assert m.eligible_date('S4', at(2026,10,30,4,29)) is None
    assert m.eligible_date('S4', at(2026,10,30,4,30)) == friday
    assert m.eligible_date('S4', at(2026,10,30,19,0)) == friday
    assert m.eligible_date('S5', at(2026,10,30,16,29)) is None
    assert m.eligible_date('S5', at(2026,10,30,16,30)) == friday
    assert m.eligible_date('S5', at(2026,10,31,6,59)) == friday
    assert m.eligible_date('S5', at(2026,10,31,7,0)) == friday
    # After 7 AM the Friday S5 can no longer be resumed.
    assert m.eligible_date('S5', at(2026,10,31,7,1)) != friday


def test_unique_eligible_shift_binds_login_and_opens_landing(client, monkeypatch):
    freeze(monkeypatch, at(2026,10,27,8,0))
    assert client.get('/shift-report.html').status_code == 401
    assert login(client)['destination'] == '/shift-report.html'
    who = client.get('/api/me').json()
    assert who['selected']['code'] == 'S1'
    assert who['selected']['date'] == '2026-10-27'
    assert who['selected']['opens_at'].startswith('2026-10-27T04:30')
    assert client.get('/', follow_redirects=False).status_code == 303
    assert client.get('/shift-report.html').status_code == 200
    assert 'workspace-auth.js' in client.get('/shift-report.html').text


def test_signed_out_active_shift_screen_and_logout_preserves_shift(client, monkeypatch):
    freeze(monkeypatch, at(2026,10,27,8,0))
    login(client)
    assert client.get('/api/entry').json()['screen'] == 'active'
    assert client.post('/api/logout').status_code == 200
    assert client.get('/api/me').status_code == 401
    assert client.get('/api/entry').json()['screen'] == 'active'
    assert 'auth-ui.js' in client.get('/').text
    assert login(client, '2759')['destination'] == '/shift-report.html'
    assert client.get('/api/me').json()['selected']['code'] == 'S1'


def test_overlap_displays_handoff_and_both_reports_remain_open(client, monkeypatch):
    freeze(monkeypatch, at(2026,10,26,21,0))
    login(client)
    s3 = client.post('/api/shifts/start', json={'code':'S3'})
    assert s3.status_code == 200
    old_id = s3.json()['id']
    client.post('/api/logout')
    freeze(monkeypatch, at(2026,10,27,4,45))
    landing = client.get('/api/entry').json()
    assert landing['screen'] == 'handoff'
    assert [(x['code'],x['date']) for x in landing['open']] == [('S3','2026-10-26')]
    assert any(x['code']=='S1' and x['date']=='2026-10-27' for x in landing['ready'])
    assert login(client, '2759')['destination'] == '/'
    assert client.get('/shift-report.html', follow_redirects=False).status_code == 303
    assert client.post('/api/shifts/resume', json={'shift_id':old_id}).status_code == 200
    assert client.get('/api/me').json()['selected']['code'] == 'S3'
    new = client.post('/api/shifts/start',json={'code':'S1'})
    assert new.status_code == 200
    assert new.json()['id'] != old_id
    assert client.get('/api/me').json()['selected']['code'] == 'S1'
    with m._db() as db:
        rows = db.execute('SELECT code,shift_day,open FROM shifts').fetchall()
        assert {(x['code'], x['shift_day'], x['open']) for x in rows} == {
            ('S3','2026-10-26',1), ('S1','2026-10-27',1)}


def test_expired_shift_is_hidden_not_erased(client, monkeypatch):
    freeze(monkeypatch, at(2026,10,27,8,0))
    login(client)
    current = client.get('/api/me').json()['selected']['id']
    freeze(monkeypatch, at(2026,10,27,17,1))
    # S1 is past the one-hour late grace; it remains in the DB for history.
    result = client.get('/api/me').json()
    assert result['selected'] is None
    assert all(x['code'] != 'S1' for x in result['shifts'])
    assert client.get('/shift-report.html', follow_redirects=False).status_code == 303
    assert client.post('/api/shifts/resume', json={'shift_id':current}).status_code == 404
    with m._db() as db:
        assert db.execute('SELECT open FROM shifts WHERE id=?',(current,)).fetchone()['open'] == 1


def test_end_shift_closes_only_selected_report(client, monkeypatch):
    freeze(monkeypatch, at(2026,10,27,13,0))
    login(client)  # two eligible choices; not bound yet
    assert client.get('/shift-report.html', follow_redirects=False).status_code == 303
    s1 = client.post('/api/shifts/start', json={'code':'S1'}).json()['id']
    s2 = client.post('/api/shifts/start', json={'code':'S2'}).json()['id']
    assert client.post('/api/shifts/end').json() == {'ended':True,'shift_id':s2}
    with m._db() as db:
        rows = db.execute('SELECT id, open FROM shifts').fetchall()
        values = {r['id']: r['open'] for r in rows}
    assert values[s1] == 1 and values[s2] == 0
    assert client.get('/api/me').json()['selected'] is None
    assert client.post('/api/shifts/resume',json={'shift_id':s2}).status_code == 404


def test_requests_are_local_only_and_private_paths_not_public(client):
    response = client.post('/api/login',json={'associate':'8972'})
    assert 'httponly' in response.headers['set-cookie'].lower()
    assert 'samesite=strict' in response.headers['set-cookie'].lower()
    for path in ['/app.py','/setup_access.py','/.local-data/shifts.sqlite3',
                 '/web/../app.py','/prototypes/login-flow.html']:
        assert client.get(path).status_code in (404,405)
    assert client.post('/api/login',json={'associate':'8972'},
                       headers={'Origin':'https://attacker.example'}).status_code == 403


def test_fresh_empty_database_and_tamper_rejected(client):
    with m._db() as db:
        assert db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0] == 0
    client.cookies.set(m.SESSION_COOKIE, 'invalid')
    assert client.get('/api/me').status_code == 401


def test_late_grace_only_resumes_an_already_open_shift(client, monkeypatch):
    freeze(monkeypatch, at(2026,10,27,16,30))
    login(client)
    assert 'S1' not in {x['code'] for x in client.get('/api/me').json()['ready']}
    assert client.post('/api/shifts/start',json={'code':'S1'}).status_code == 409
    freeze(monkeypatch, at(2026,10,27,15,59))
    started = client.post('/api/shifts/start',json={'code':'S1'})
    assert started.status_code == 200
    shift_id = started.json()['id']
    freeze(monkeypatch, at(2026,10,27,16,59))
    assert client.post('/api/shifts/resume',json={'shift_id':shift_id}).status_code == 200
    assert client.get('/api/me').json()['selected']['code'] == 'S1'


def test_report_starts_without_fabricated_work_orders(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 8, 0))
    login(client)
    response = client.get('/shift-report.html')
    assert response.status_code == 200
    html = response.text
    assert 'const sampleRows = []' in html
    assert 'No Cetaris File Imported' in html
    assert 'No work orders yet.' in html
    assert 'id="metric-work">0<' in html
    assert 'id="loto-toggle" checked' not in html
    assert 'data:image/jpeg;base64' not in html
    assert 'background:#fff' in html


def test_active_and_overlap_buttons_use_popup_not_first_signin(client, monkeypatch):
    js = (m.ROOT / 'web' / 'auth-ui.js').read_text(encoding='utf-8')
    assert 'v31-associate-dialog' in js
    assert "gate.showModal();" in js
    assert "api('/api/login', {associate})" in js
    assert "window.location.replace('/shift-report.html')" in js
    assert "if (!signedIn) { error.hidden = true; show('login')" not in js
    assert 'v31-preload' in client.get('/').text
