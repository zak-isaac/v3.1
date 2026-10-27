import hashlib, json, secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
import pytest
import app as m

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'PRIVATE', tmp_path)
    monkeypatch.setattr(m, 'USERS', tmp_path/'users.json')
    monkeypatch.setattr(m, 'SESSION_SECRET_FILE', tmp_path/'session_secret')
    monkeypatch.setattr(m, 'DB_FILE', tmp_path/'shifts.sqlite3')
    salt=secrets.token_bytes(16)
    pw='a-long-private-passphrase'
    pw_hash=hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()
    accounts={k:{'name':name,'salt':salt.hex(),'hash':pw_hash,'all_shifts':all_shifts,'shift':shift} for k,name,all_shifts,shift in [('111','Manager A',True,None),('222','Manager B',True,None),('333','Associate C',False,'S2')]}
    m.USERS.write_text(json.dumps(accounts))
    with TestClient(m.app) as c:yield c


def login(c, assoc='111'):
    r=c.post('/api/login',json={'associate':assoc,'passphrase':'a-long-private-passphrase'})
    assert r.status_code==200,r.text
    return r


def test_login_opens_landing_page_directly(client):
    assert client.get('/shift-report.html').status_code==401
    assert client.get('/').status_code==200
    assert 'Verify your access' not in client.get('/').text
    assert login(client).json()['destination']=='/shift-report.html'
    assert client.get('/', follow_redirects=False).status_code==303
    assert client.get('/shift-report.html').status_code==200
    assert 'workspace-auth.js' in client.get('/shift-report.html').text


def test_password_is_required(client):
    assert client.post('/api/login',json={'associate':'111'}).status_code==422
    assert client.post('/api/login',json={'associate':'111','passphrase':'incorrect'}).status_code==401
    assert client.post('/api/login',json={'associate':'999','passphrase':'a-long-private-passphrase'}).status_code==401
    assert client.get('/api/me').status_code==401


def test_access_for_both_management_accounts(client,monkeypatch):
    # S1 and S2 are simultaneously eligible at 1:15 PM local.
    class FakeDT:
        @staticmethod
        def now(tz): return datetime(2026,10,27,13,15,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        combine=datetime.combine
    monkeypatch.setattr(m,'datetime',FakeDT)
    login(client)
    choices=client.get('/api/me').json()['shifts']
    assert {x['code'] for x in choices}=={'S1','S2'}
    s1=client.post('/api/shifts/start',json={'code':'S1'})
    assert s1.status_code==200,s1.text
    first=s1.json()['id']
    client.post('/api/logout')
    login(client,'222')
    resumed=client.post('/api/shifts/resume',json={'shift_id':first})
    assert resumed.status_code==200
    s2=client.post('/api/shifts/start',json={'code':'S2'})
    assert s2.status_code==200
    assert client.get('/api/me').json()['selected']['code']=='S2'
    with m._db() as conn:
        rows=conn.execute('SELECT id,open FROM shifts ORDER BY id').fetchall()
        assert len(rows)==2 and all(r['open']==1 for r in rows)


def test_assigned_shift_access_restriction(client,monkeypatch):
    class FakeDT:
        @staticmethod
        def now(tz): return datetime(2026,10,27,13,15,tzinfo=ZoneInfo('America/Indiana/Indianapolis'))
        combine=datetime.combine
    monkeypatch.setattr(m,'datetime',FakeDT)
    login(client,'333')
    assert [x['code'] for x in client.get('/api/me').json()['shifts']]==['S2']
    assert client.post('/api/shifts/start',json={'code':'S1'}).status_code==403
    assert client.post('/api/shifts/start',json={'code':'S2'}).status_code==200


def test_secure_cookie_and_private_folder_not_accessible(client):
    r=login(client)
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert 'samesite=strict' in r.headers['set-cookie'].lower()
    for path in ['/users.json','/.local-data/users.json','/web/../users.json','/web/../app.py','/web/../.local-data/session_secret','/app.py','/prototypes/login-flow.html']:
        assert client.get(path).status_code in (404,405),path


def test_cross_site_denied(client):
    r=client.post('/api/login',json={'associate':'111','passphrase':'a-long-private-passphrase'},headers={'Origin':'https://attacker.example'})
    assert r.status_code==403


def test_logout_and_tampered_cookie(client):
    login(client)
    assert client.get('/api/me').status_code==200
    client.post('/api/logout')
    assert client.get('/api/me').status_code==401
    client.cookies.set(m.SESSION_COOKIE,'invalid')
    assert client.get('/shift-report.html').status_code==401


def test_no_preseeded_users_or_shifts(client):
    with m._db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM shifts').fetchone()[0]==0
    assert len(m.roster())==3 # Only temporary fixture users created inside test.