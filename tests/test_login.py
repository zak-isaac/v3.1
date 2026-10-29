"""Regression tests for isolated V3.1 manager/MST login and shift time windows."""
import csv
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
import app as m

ZONE = ZoneInfo('America/Indiana/Indianapolis')


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ZONE)


def freeze(monkeypatch, clock):
    original = datetime
    class Clock:
        combine = original.combine
        @staticmethod
        def now(tz):
            return clock
    monkeypatch.setattr(m, 'datetime', Clock)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'PRIVATE', tmp_path)
    monkeypatch.setattr(m, 'SESSION_SECRET_FILE', tmp_path / 'session_secret')
    monkeypatch.setattr(m, 'DB_FILE', tmp_path / 'shifts.sqlite3')
    monkeypatch.delenv('IND3_V31_ROSTER_PATH', raising=False)
    # Test-only ephemeral associates; never installed in the actual app.
    roster = tmp_path / 'mst-roster.csv'
    with roster.open('w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Name', 'Assoc. #', 'Shift', 'Job Title'])
        writer.writerow(['Temporary Test A', '111111', 'S1', 'MST'])
        writer.writerow(['Temporary Test B', '222222', 'S2', 'MST'])
        writer.writerow(['Temporary Test C', '333333', 'S3', 'MST'])
        writer.writerow(['Temporary Test D', '444444', 'S4', 'MST'])
        writer.writerow(['Temporary Test E', '555555', 'S5', 'MST'])
        writer.writerow(['Temporary Test F', '666666', 'S1', 'MST'])
        writer.writerow(['Other Role', '777777', 'S1', 'Associate'])
    with TestClient(m.app) as c:
        yield c


def sign_in(c, assoc='8972', **options):
    return c.post('/api/login', json={'associate': assoc, **options})


def test_manager_first_signin_always_requires_shift_popup(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 8))
    for number in ['8972', '2759']:
        r = sign_in(client, number)
        assert r.status_code == 200
        assert r.json()['manager_selection_required'] is True
        assert r.json()['role'] == 'manager'
        assert r.json()['destination'] == '/'
        state = client.get('/api/me').json()
        assert state['role'] == 'manager'
        assert state['selected'] is None
        assert len(state['catalog']) == 5
        assert {x['code'] for x in state['catalog']} == {'S1', 'S2', 'S3', 'S4', 'S5'}
        assert client.get('/shift-report.html', follow_redirects=False).status_code == 303
        client.post('/api/logout')


def test_mst_goes_directly_to_roster_assigned_shift(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 8))
    result = sign_in(client, '111111')
    assert result.status_code == 200, result.text
    assert result.json()['destination'] == '/shift-report.html'
    assert result.json()['shift'] == 'S1'
    selected = client.get('/api/me').json()['selected']
    assert selected['code'] == 'S1'
    assert selected['date'] == '2026-10-27'
    assert client.get('/shift-report.html').status_code == 200
    with m._db() as db:
        assert db.execute('SELECT owner FROM shifts WHERE id=?',
                          (selected['id'],)).fetchone()['owner'] == '111111'


def test_mst_resumes_own_shift_on_next_signin(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 26, 21))
    first = sign_in(client, '333333')
    assert first.status_code == 200
    sid = client.get('/api/me').json()['selected']['id']
    client.post('/api/logout')
    freeze(monkeypatch, at(2026, 10, 27, 7, 30))
    again = sign_in(client, '333333')
    assert again.status_code == 200
    selected = client.get('/api/me').json()['selected']
    assert selected['id'] == sid and selected['date'] == '2026-10-26'
    assert selected['code'] == 'S3'


def test_mst_cannot_select_unassigned_shift_or_others_report(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 13))
    assert sign_in(client, '111111').status_code == 200
    sid = client.get('/api/me').json()['selected']['id']
    assert client.post('/api/shifts/start', json={'code':'S2'}).status_code == 403
    client.post('/api/logout')
    assert sign_in(client, '666666').status_code == 409
    assert sign_in(client, '222222').status_code == 200
    assert client.post('/api/shifts/resume', json={'shift_id':sid}).status_code == 403
    with m._db() as db:
        assert db.execute('SELECT owner FROM shifts WHERE id=?',
                          (sid,)).fetchone()['owner'] == '111111'


def test_popup_login_does_not_create_any_shift(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 13))
    r = sign_in(client, '111111', intent='select')
    assert r.status_code == 200
    assert r.json()['selection_required'] is True
    assert client.get('/api/me').json()['selected'] is None
    with m._db() as db:
        assert db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0] == 0
    s = client.post('/api/shifts/start', json={'code':'S1'})
    assert s.status_code == 200
    assert client.get('/api/me').json()['selected']['code'] == 'S1'


def test_manager_can_choose_and_resume_another_mst_report(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 13))
    assert sign_in(client, '111111').status_code == 200
    sid = client.get('/api/me').json()['selected']['id']
    client.post('/api/logout')
    assert sign_in(client, '2759').json()['manager_selection_required']
    state = client.get('/api/me').json()
    assert any(x['code'] == 'S1' and x['action'] == 'resume' for x in state['catalog'])
    assert client.post('/api/shifts/resume',json={'shift_id':sid}).status_code == 200
    assert client.get('/api/me').json()['selected']['id'] == sid
    assert client.get('/shift-report.html').status_code == 200


def test_unknown_or_nonmst_associates_rejected(client):
    assert sign_in(client, '777777').status_code == 401
    assert sign_in(client, '999999').status_code == 401
    assert sign_in(client, '123abc').status_code == 422
    assert sign_in(client, '').status_code == 422


def test_time_window_one_hour_before_after_and_overnight():
    t = m.eligible_date
    assert t('S1', at(2026, 10, 27, 4, 29)) is None
    assert t('S1', at(2026, 10, 27, 4, 30)) == date(2026, 10, 27)
    assert t('S1', at(2026, 10, 27, 17, 0)) == date(2026, 10, 27)
    assert t('S1', at(2026, 10, 27, 17, 1)) is None
    assert t('S3', at(2026, 10, 27, 7, 59)) == date(2026, 10, 26)
    assert t('S3', at(2026, 10, 27, 8, 0)) == date(2026, 10, 26)
    assert t('S3', at(2026, 10, 27, 8, 1)) is None
    assert t('S5', at(2026, 10, 31, 7, 0)) == date(2026, 10, 30)
    assert t('S5', at(2026, 10, 31, 7, 1)) != date(2026, 10, 30)


def test_not_create_new_shift_in_late_closing_grace(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 16, 30))
    sign_in(client, '8972')
    assert 'S1' not in {x['code'] for x in client.get('/api/me').json()['ready']}
    assert client.post('/api/shifts/start',json={'code':'S1'}).status_code == 409


def test_signout_preserves_open_session_and_overlap_screen(client,monkeypatch):
    freeze(monkeypatch, at(2026,10,26,21,0))
    sign_in(client,'333333')
    sid = client.get('/api/me').json()['selected']['id']
    client.post('/api/logout')
    freeze(monkeypatch, at(2026,10,27,4,45))
    entry=client.get('/api/entry').json()
    assert entry['screen'] == 'handoff'
    assert any(x['code']=='S3' and x['id']==sid for x in entry['open'])
    assert any(x['code']=='S1' for x in entry['ready'])


def test_end_shift_only_changes_selected_one(client, monkeypatch):
    freeze(monkeypatch, at(2026,10,27,13))
    sign_in(client,'8972')
    first = client.post('/api/shifts/start',json={'code':'S1'}).json()['id']
    second = client.post('/api/shifts/start',json={'code':'S2'}).json()['id']
    assert client.post('/api/shifts/end').status_code == 409
    saved=client.post('/api/shifts/report',json={'content':'IND3 S2 SHIFT HAPPENINGS: No work orders. Shift completed and ready for handoff.'})
    assert saved.status_code == 200, saved.text
    assert client.post('/api/shifts/end').json()['shift_id'] == second
    with m._db() as db:
        rows = {r['id']: r['open'] for r in db.execute('SELECT id,open FROM shifts')}
    assert rows[first]==1 and rows[second]==0


def test_login_popup_regex_and_manager_selector(client):
    js = (m.ROOT / 'web' / 'auth-ui.js').read_text()
    assert 'v31-associate-dialog' in js
    assert 'v31-manager-shift-picker' in js
    assert 'manager_selection_required' in js
    assert "'/api/shifts/enter'" in js
    assert "action: choice.kind" in js
    assert r'/^\\d{1,12}$/' not in js
    assert r'/^\d{1,12}$/' in js
    assert 'showManagerPicker()' in js
    assert client.get('/').status_code == 200


def test_all_login_screens_have_viewport_layout(client):
    html = client.get('/').text
    assert '/web/login-layout.css' in html
    css = client.get('/web/login-layout.css')
    assert css.status_code == 200
    assert '100dvh' in css.text and 'max-width:100%' in css.text


def test_zero_sample_maintenance_data_in_report(client,monkeypatch):
    freeze(monkeypatch, at(2026,10,27,8))
    sign_in(client,'111111')
    html=client.get('/shift-report.html').text
    assert 'const sampleRows = []' in html
    assert 'No Cetaris File Imported' in html
    assert 'No work orders yet' in html


def test_local_only_cookie_and_private_roster_not_served(client):
    freeze_clock = sign_in(client,'8972')
    assert freeze_clock.status_code == 200
    assert 'httponly' in freeze_clock.headers['set-cookie'].lower()
    assert 'samesite=strict' in freeze_clock.headers['set-cookie'].lower()
    for p in ['/.local-data/mst-roster.csv','/.local-data/shifts.sqlite3',
              '/web/../app.py','/app.py','/prototypes/login-flow.html']:
        assert client.get(p).status_code in (404,405)
    assert client.post('/api/login',json={'associate':'8972'},
             headers={'Origin':'https://other.example'}).status_code == 403


def test_signed_out_login_shows_two_already_open_weekday_shifts(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 26, 13, 0))
    assert sign_in(client, '8972').status_code == 200
    assert client.post('/api/shifts/start', json={'code': 'S1'}).status_code == 200
    assert client.post('/api/shifts/start', json={'code': 'S2'}).status_code == 200
    assert client.post('/api/logout').status_code == 200
    entry = client.get('/api/entry').json()
    assert entry['screen'] == 'handoff'
    assert {x['code'] for x in entry['open']} == {'S1', 'S2'}
    assert entry['ready'] == []
    with m._db() as db:
        assert db.execute('SELECT COUNT(*) FROM shifts WHERE open=1').fetchone()[0] == 2


def test_signed_out_login_shows_two_already_open_weekend_shifts(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 30, 17, 45))
    sign_in(client, '8972')
    assert client.post('/api/shifts/start', json={'code': 'S4'}).status_code == 200
    assert client.post('/api/shifts/start', json={'code': 'S5'}).status_code == 200
    assert client.post('/api/logout').status_code == 200
    entry = client.get('/api/entry').json()
    assert entry['screen'] == 'handoff'
    assert {x['code'] for x in entry['open']} == {'S4', 'S5'}
    assert entry['ready'] == []
    html=client.get('/').text
    assert '/web/auth-ui.js' in html
    js=(m.ROOT / 'web' / 'auth-ui.js').read_text()
    assert 'choices.length >= 2' in js
    assert 'Two Active Shifts' in js


def test_signout_weekend_overnight_s5_and_next_s4_ready(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 30, 21, 0))
    sign_in(client, '8972')
    created = client.post('/api/shifts/start', json={'code': 'S5'})
    assert created.status_code == 200, created.text
    sid=created.json()['id']
    assert client.post('/api/logout').status_code == 200
    freeze(monkeypatch, at(2026, 10, 31, 5, 45))
    entry=client.get('/api/entry').json()
    assert entry['screen']=='handoff'
    assert any(x['code']=='S5' and x['id']==sid and
               x['date']=='2026-10-30' for x in entry['open'])
    assert any(x['code']=='S4' and x['date']=='2026-10-31' for x in entry['ready'])


def test_shift_controls_and_brand_relocated_to_sidebar(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 8, 0))
    sign_in(client,'8972')
    assert client.post('/api/shifts/start',json={'code':'S1'}).status_code==200
    page=client.get('/shift-report.html')
    assert page.status_code==200
    assert 'id="v31-sidebar-controls"' in page.text
    assert 'class="sidebar-brand"' in page.text
    assert 'class="v31-brand-logo"' in page.text
    assert 'data-nav="support"' not in page.text
    assert '/web/workspace-layout.css' in page.text
    css=client.get('/web/workspace-layout.css')
    assert css.status_code==200
    assert '.v31-context-actions' in css.text
    assert '.shift-meta{display:none!important}' in css.text
    js=client.get('/web/workspace-auth.js')
    assert js.status_code==200
    for item in ('v31-sidebar-date','v31-sidebar-schedule','v31-sidebar-window',
                 'v31-sidebar-clock','v31-menu-choose','v31-signout'):
        assert item in js.text
    assert 'window.V31Reporting.refresh()' in js.text
    admin=client.get('/web/roster-admin.js')
    assert admin.status_code==200
    assert "window.addEventListener('v31:administration'" in admin.text
    assert ".v31-context-actions" not in admin.text


def test_administration_is_full_management_workspace_without_selected_shift(client,monkeypatch):
    freeze(monkeypatch,at(2026,10,27,8))
    assert sign_in(client,'8972').status_code==200
    assert client.get('/api/me').json()['selected'] is None
    page=client.get('/administration')
    assert page.status_code==200,page.text[:200]
    for item in ('id="v31-admin-screen"', 'id="v31-admin-content"',
                 'id="v31-roster-rows"', 'id="v31-admin-add-form"',
                 'id="v31-sync-source"', 'id="v31-roster-search"',
                 '/web/admin-standalone.js', '/web/roster-admin.js',
                 '/web/administration.css'):
        assert item in page.text
    assert 'Manage Drax Roster</button>' not in page.text
    assert '/web/workspace-auth.js' not in page.text
    js=client.get('/web/roster-admin.js')
    assert js.status_code==200
    assert '/api/admin/roster' in js.text
    assert 'v31:administration' in js.text


def test_administration_requires_manager_and_protects_roster(client,monkeypatch):
    freeze(monkeypatch,at(2026,10,27,8))
    assert client.get('/administration').status_code==401
    assert sign_in(client,'111111').status_code==200
    assert client.get('/administration').status_code==403
    assert client.get('/api/admin/roster').status_code==403


def test_stationary_sidebar_mst_identity_and_signout(client,monkeypatch):
    freeze(monkeypatch,at(2026,10,27,8))
    sign_in(client,'111111')
    page=client.get('/shift-report.html')
    assert page.status_code==200
    markup=page.text
    assert 'id="v31-sidebar-user"' in markup
    assert 'id="v31-user-name"' in markup
    assert markup.index('id="v31-user-name"') < markup.index('id="v31-signout"')
    assert 'id="v31-select-shift"' not in markup
    assert 'id="v31-menu-choose"' in markup
    layout=client.get('/web/workspace-layout.css')
    assert layout.status_code==200
    assert '.sidebar{height:100%;min-height:0!important;overflow:hidden' in layout.text
    assert '.main-content{min-height:0;overflow-y:auto' in layout.text
    workspace=client.get('/web/workspace-auth.js')
    assert workspace.status_code==200
    assert "username.textContent=context.name" in workspace.text
    assert "context.role==='manager'" in workspace.text
    assert "v31-select-shift" not in workspace.text
    assert "$('#v31-signout')" in workspace.text


def test_management_chooser_admin_shortcut_and_shift_chooser_not_in_sidebar(client,monkeypatch):
    freeze(monkeypatch,at(2026,10,27,8))
    assert sign_in(client,'8972').status_code==200
    login=client.get('/').text
    assert '/web/auth-ui.js' in login
    chooser=client.get('/web/auth-ui.js').text
    assert '>Administration</button>' in chooser
    adminjs=client.get('/web/roster-admin.js').text
    assert "window.location.assign('/administration')" in adminjs
    assert ".v31-context-actions" not in adminjs
    page=client.get('/administration').text
    assert '<button id="v31-signout"' in page
    assert 'Choose Shift</button>' in page  # Account-menu action, not sidebar.
    sidebar=page.split('<aside class="sidebar">',1)[1].split('</aside>',1)[0]
    assert 'Choose Shift' not in sidebar
    assert 'Manage Drax Roster' not in sidebar


def test_public_shift_start_popup_only_signs_in_after_validated_shift(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 13))
    rejected = client.post('/api/shifts/enter', json={
        'associate': '111111', 'action': 'start', 'code': 'S2'})
    assert rejected.status_code == 403
    assert client.cookies.get(m.SESSION_COOKIE) is None
    assert client.get('/api/me').status_code == 401
    with m._db() as db:
        assert db.execute('SELECT COUNT(*) FROM shifts').fetchone()[0] == 0
    accepted = client.post('/api/shifts/enter', json={
        'associate': '111111', 'action': 'start', 'code': 'S1'})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['shift'] == 'S1'
    selected = client.get('/api/me').json()['selected']
    assert selected['id'] == accepted.json()['shift_id']


def test_public_resume_popup_rejects_wrong_owner_without_cookie(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 13))
    assert sign_in(client, '111111').status_code == 200
    shift_id = client.get('/api/me').json()['selected']['id']
    assert client.post('/api/logout').status_code == 200
    rejected = client.post('/api/shifts/enter', json={
        'associate': '666666', 'action': 'resume', 'shift_id': shift_id})
    assert rejected.status_code == 403
    assert client.cookies.get(m.SESSION_COOKIE) is None
    assert client.get('/api/me').status_code == 401
    accepted = client.post('/api/shifts/enter', json={
        'associate': '111111', 'action': 'resume', 'shift_id': shift_id})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['shift_id'] == shift_id
    assert client.get('/api/me').json()['selected']['id'] == shift_id


def test_manager_public_popup_starts_selected_shift_without_prelogin(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 13))
    result = client.post('/api/shifts/enter', json={
        'associate': '8972', 'action': 'start', 'code': 'S2'})
    assert result.status_code == 200, result.text
    assert result.json()['role'] == 'manager'
    state = client.get('/api/me').json()
    assert state['selected']['code'] == 'S2'
    assert state['role'] == 'manager'


def test_login_entry_health_and_live_refresh_wiring(client, tmp_path, monkeypatch):
    state = client.get('/api/entry')
    assert state.status_code == 200
    assert state.json()['roster_health'] == 'connected'
    js = client.get('/web/auth-ui.js').text
    assert "setInterval(refreshEntry, 30000)" in js
    assert "window.addEventListener('focus'" in js
    assert "document.addEventListener('visibilitychange'" in js
    assert "/api/shifts/enter" in js
    assert "Drax Sync Needs Attention" in js
    assert "if (signedIn && managerPicker.open)" in js
    assert "showManagerPicker(latestAccount)" in js
    html = client.get('/').text
    assert 'Version 3.1' in html and 'v1.0.0' not in html
    (tmp_path / 'mst-roster.csv').unlink()
    m.v31_roster.sync(m.PRIVATE, m.DB_FILE, set(m.MANAGEMENT_ASSOCIATES), force=True)
    assert client.get('/api/entry').json()['roster_health'] == 'stale'


def test_workspace_hides_manager_controls_for_mst(client, monkeypatch):
    freeze(monkeypatch, at(2026, 10, 27, 8))
    assert sign_in(client, '111111').status_code == 200
    js = client.get('/web/workspace-auth.js').text
    assert "adminNav.hidden=context.role!=='manager'" in js
    assert "adminMenu.hidden=context.role!=='manager'" in js
    assert client.get('/administration').status_code == 403
