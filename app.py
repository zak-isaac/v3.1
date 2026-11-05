"""Isolated V3.1 login and shift-context service. No Workshop integration.

Development convenience: associate-number-only access is NOT authentication.
Never expose this application on the facility network or Internet.
"""
from __future__ import annotations

import base64
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, time as clock, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
import v31_roster
import v31_legacy_history
import v31_drax_bridge
import v31_reporting
import v31_facility
import v31_oos
import v31_pm_needing_completion as v31_pm
import v31_report_export
import v31_analytics
import v31_breakdown
import ai_remark_rewriter

ROOT = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get('IND3_V31_PRIVATE_DIR', str(ROOT / '.local-data'))).resolve()
SESSION_SECRET_FILE = PRIVATE / 'session_secret'
DB_FILE = PRIVATE / 'shifts.sqlite3'
TZ = ZoneInfo('America/Indiana/Indianapolis')
SESSION_TTL = 18 * 3600
WINDOW_GRACE = timedelta(hours=1)
SESSION_COOKIE = 'ind3_v31_session'
# Local-only development IDs, NOT secure identity verification.
MANAGEMENT_ASSOCIATES = {
    '8972': {'name': 'Management', 'all_shifts': True, 'shift': None},
    '2759': {'name': 'Isaac Ssaku', 'all_shifts': True, 'shift': None},
}
SHIFT_RULES = {
    'S1': ('Day Shift', (5, 30), (16, 0), (0, 1, 2, 3)),
    'S2': ('Weekday Shift', (12, 0), (22, 30), (0, 1, 2, 3)),
    'S3': ('Overnight Shift', (20, 30), (7, 0), (0, 1, 2, 3)),
    'S4': ('Weekend Day Shift', (5, 30), (18, 0), (4, 5, 6)),
    'S5': ('Weekend Night Shift', (17, 30), (6, 0), (4, 5, 6)),
}

@asynccontextmanager
async def lifespan(_app):
    # Isolated boot: don't touch Workshop V3 data or services.
    roster()
    # Initialize all private SQLite schemas before parallel browser requests
    # begin. This avoids staging/PM/breakdown racing migration DDL.
    with _db() as conn:
        v31_reporting._table(conn)
        v31_pm.ensure(conn)
        v31_oos._table(conn)
        v31_breakdown.ensure(conn)
    expire_shifts()
    task = asyncio.create_task(_periodic_maintenance())
    bridge = asyncio.create_task(_periodic_drax_bridge())
    try:
        yield
    finally:
        for job in (task,bridge):
            job.cancel()
        for job in (task,bridge):
            try:
                await job
            except asyncio.CancelledError:
                pass


async def _periodic_maintenance():
    tick = 0
    while True:
        await asyncio.sleep(60)
        try:
            expire_shifts()
            tick += 1
            if tick % 5 == 0:
                v31_roster.sync(PRIVATE, DB_FILE, force=True)
        except Exception:
            # Do not terminate reporting due to temporary unreadable workbook.
            pass


async def _periodic_drax_bridge():
    """Opt-in bridge: read old V3 database, write shared Drax only when enabled.

    A no-write first baseline prevents existing mismatches from being
    interpreted as new changes. Never modifies original V3 SQLite.
    """
    while True:
        await asyncio.sleep(30)
        old_db = os.getenv("IND3_V31_LEGACY_DB_PATH", "").strip()
        if not old_db:
            continue
        drax, _ = v31_roster._source(PRIVATE)
        if not drax:
            continue
        try:
            write = v31_drax_bridge.enabled()
            await asyncio.to_thread(v31_drax_bridge.reconcile, Path(old_db),
                                    drax, PRIVATE, write=write)
            # The normal Drax cache refresh sees changes from either system.
            if write:
                await asyncio.to_thread(v31_roster.sync, PRIVATE, DB_FILE,
                                        set(MANAGEMENT_ASSOCIATES), True)
        except Exception:
            # Retain last good data; operator can run the bridge CLI to inspect.
            pass


app = FastAPI(title='IND3 V3.1', docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=lifespan)
app.mount('/web', StaticFiles(directory=ROOT / 'web'), name='web')


def _secret() -> bytes:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    if SESSION_SECRET_FILE.exists():
        v = SESSION_SECRET_FILE.read_bytes()
        if len(v) < 32:
            raise RuntimeError('Session key too short')
        return v
    # O_EXCL prevents accidental replacement by concurrent server instances.
    try:
        fd = os.open(SESSION_SECRET_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(secrets.token_bytes(48))
    except FileExistsError:
        pass
    return SESSION_SECRET_FILE.read_bytes()


def roster() -> dict:
    return v31_roster.profiles(PRIVATE, DB_FILE, MANAGEMENT_ASSOCIATES, SHIFT_RULES)


def issue(assoc: str, shift_id: int = 0) -> str:
    payload = {'a': assoc, 's': shift_id, 't': int(time.time())}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    mac = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + '.' + mac


def parse_cookie(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        encoded, mac = value.rsplit('.', 1)
        if not hmac.compare_digest(mac, hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()):
            return None
        data = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        age = time.time() - int(data['t'])
        if not 0 <= age < SESSION_TTL or not isinstance(data['s'], int):
            return None
        if data['a'] not in roster():
            return None
        return data
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeDecodeError):
        return None


def who(request: Request) -> tuple[dict, dict]:
    session = parse_cookie(request.cookies.get(SESSION_COOKIE))
    if not session:
        raise HTTPException(401, 'Sign in required')
    return session, roster()[session['a']]


def _origin_check(request: Request) -> None:
    origin = request.headers.get('origin', '').rstrip('/')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Cross-origin request blocked')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site request blocked')


def _set_cookie(response: Response, assoc: str, shift_id: int = 0) -> None:
    response.set_cookie(SESSION_COOKIE, issue(assoc, shift_id), max_age=SESSION_TTL,
                        httponly=True, samesite='strict', path='/',
                        secure=os.getenv('IND3_V31_HTTPS') == '1')


def _db() -> sqlite3.Connection:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=8000')
    conn.execute('''CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL, shift_day TEXT NOT NULL,
        owner TEXT NOT NULL, open INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, UNIQUE(code, shift_day)
    )''')
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(shifts)')}
    for name, declaration in {
        'closed_at': "TEXT NOT NULL DEFAULT ''",
        'closed_reason': "TEXT NOT NULL DEFAULT ''",
        'report_saved_at': "TEXT NOT NULL DEFAULT ''",
        'report_content': "TEXT NOT NULL DEFAULT ''",
        'report_count': "INTEGER NOT NULL DEFAULT 0",
        'report_html': "TEXT NOT NULL DEFAULT ''",
        'report_hash': "TEXT NOT NULL DEFAULT ''",
        'report_eml': "BLOB NOT NULL DEFAULT X''",
        # Existing V3.1 shifts were MST-owned, so preserve their authorization.
        'owner_role': "TEXT NOT NULL DEFAULT 'mst'",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE shifts ADD COLUMN {name} {declaration}")
    conn.execute('''CREATE TABLE IF NOT EXISTS login_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        associate TEXT NOT NULL,
        event TEXT NOT NULL,
        shift_id INTEGER,
        recorded_at TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS shift_owner_transfers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shift_id INTEGER NOT NULL,
        previous_owner TEXT NOT NULL,
        new_owner TEXT NOT NULL,
        transferred_at TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT 'mst_relief'
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS mst_overtime_grants(
        associate TEXT NOT NULL,
        code TEXT NOT NULL,
        shift_day TEXT NOT NULL,
        granted_by TEXT NOT NULL,
        granted_at TEXT NOT NULL,
        PRIMARY KEY(associate,code,shift_day)
    )''')
    conn.commit()
    return conn


def _record_login_event(associate: str, event: str, shift_id: int | None = None):
    """Record new V3.1 logins. V2/V3 start history is archived separately."""
    with _db() as conn:
        conn.execute('INSERT INTO login_events(associate,event,shift_id,recorded_at) VALUES(?,?,?,?)',
                     (associate,event,shift_id,datetime.now(TZ).isoformat()))


def expire_shifts(now: datetime | None = None) -> int:
    """Auto-close expired reporting sessions; never erase their history."""
    moment = now or datetime.now(TZ)
    count = 0
    with _db() as conn:
        rows = conn.execute('SELECT id,code,shift_day FROM shifts WHERE open=1').fetchall()
        for row in rows:
            try:
                ends = shift_times(row['code'], date.fromisoformat(row['shift_day']))[1] + WINDOW_GRACE
            except (KeyError, ValueError):
                continue
            if moment >= ends:
                conn.execute("""UPDATE shifts SET open=0, closed_at=?, closed_reason='window_expired'
                                WHERE id=? AND open=1""",(moment.isoformat(),row['id']))
                count += 1
    return count


def shift_times(code: str, day: date) -> tuple[datetime, datetime]:
    _, a, b, _ = SHIFT_RULES[code]
    begin = datetime.combine(day, clock(*a), tzinfo=TZ)
    end = datetime.combine(day, clock(*b), tzinfo=TZ)
    if end <= begin:
        end += timedelta(days=1)
    return begin, end


def window_info(code: str, day: date) -> dict:
    """Every shift opens one hour early and closes one hour late."""
    begin, end = shift_times(code, day)
    return {'scheduled_start': begin.isoformat(), 'scheduled_end': end.isoformat(),
            'opens_at': (begin - WINDOW_GRACE).isoformat(),
            'closes_at': (end + WINDOW_GRACE).isoformat()}


def eligible_date(code: str, now: datetime) -> date | None:
    # Yesterday matters for overnight S3/S5 and their one-hour closing grace.
    for n in (0, 1):
        day = now.date() - timedelta(days=n)
        if day.weekday() not in SHIFT_RULES[code][3]:
            continue
        start, end = shift_times(code, day)
        if start - WINDOW_GRACE <= now <= end + WINDOW_GRACE:
            return day
    return None


def entry(row: sqlite3.Row) -> dict:
    d = date.fromisoformat(row['shift_day'])
    return {'id': row['id'], 'code': row['code'], 'date': row['shift_day'],
            'label': SHIFT_RULES[row['code']][0], 'open': bool(row['open']),
            'report_saved': bool(row['report_saved_at']),
            **window_info(row['code'], d)}


def allowed(profile: dict, code: str, day: date | None = None, assoc: str | None = None) -> bool:
    """Drax home shift, or separately approved dated MST overtime; managers unrestricted."""
    if profile.get('all_shifts') or profile.get('shift') == code:
        return True
    # Other-shift access is an explicit, day-specific, management-approved
    # privilege for MSTs only. A normal Drax shift never implies overtime.
    if not profile.get('is_mst') or not assoc or day is None:
        return False
    with _db() as conn:
        return conn.execute(
            'SELECT 1 FROM mst_overtime_grants WHERE associate=? AND code=? AND shift_day=?',
            (assoc,code,day.isoformat())).fetchone() is not None


def _current_open_count(conn: sqlite3.Connection, now: datetime) -> int:
    """Count only overlapping sessions at the displayed facility time.

    A developer may move the OS clock backwards while running this LOCAL
    application. Open rows saved for other dates must not consume capacity
    for an earlier date. Session rows remain preserved and unmodified.
    """
    count = 0
    for row in conn.execute('SELECT code,shift_day FROM shifts WHERE open=1'):
        try:
            if eligible_date(row['code'], now) == date.fromisoformat(row['shift_day']):
                count += 1
        except (KeyError, ValueError):
            continue
    return count


def shift_choices(profile: dict, now: datetime, associate: str | None = None) -> tuple[list[dict], list[dict]]:
    """Live/ready sessions are bounded by the selected clock's shift window.

    A closed historical report can be explicitly reopened by the authorized
    local management developer without ever deleting it or disabling SSL.
    """
    expire_shifts(now)
    with _db() as conn:
        records = conn.execute('SELECT * FROM shifts ORDER BY id DESC').fetchall()
    by_key = {(r['code'], r['shift_day']): r for r in records}
    open_items, ready_items = [], []
    has_capacity = sum(
        bool(r['open']) and
        eligible_date(r['code'], now) == date.fromisoformat(r['shift_day'])
        for r in records
    ) < 2
    for code in SHIFT_RULES:
        day = eligible_date(code, now)
        if day is None or not allowed(profile, code, day, associate):
            continue
        key = (code, day.isoformat())
        record = by_key.get(key)
        if record is not None:
            if record['open'] and (profile.get('all_shifts') or record['owner'] == associate):
                open_items.append({**entry(record), 'action': 'resume'})
            elif not record['open'] and has_capacity and now <= shift_times(code, day)[1] and (
                associate == '2759' and profile.get('all_shifts')
            ):
                # An explicit management action is required to reopen a
                # prior completed/auto-expired local shift for date testing.
                ready_items.append({**entry(record), 'action': 'reopen',
                                    'closed_reason': record['closed_reason']})
        else:
            # A new shift starts before its scheduled end. The extra hour
            # after closing belongs only to an already-open passdown.
            _, scheduled_end = shift_times(code, day)
            if now <= scheduled_end and has_capacity:
                ready_items.append({'id': None, 'code': code, 'date': day.isoformat(),
                                    'label': SHIFT_RULES[code][0], 'open': False,
                                    'action': 'start',
                                    **window_info(code, day)})
    open_items.sort(key=lambda x: (x['code'] != profile.get('shift'), x['scheduled_start']))
    ready_items.sort(key=lambda x: (x['code'] != profile.get('shift'), x['scheduled_start']))
    return open_items, ready_items


def public_flow(now: datetime | None = None) -> dict:
    """Local-only shift context for the approved signed-out screen designs."""
    moment = now or datetime.now(TZ)
    # Never expose an associate number or a shift owner's identity here.
    opened, ready = shift_choices({'all_shifts': True}, moment)
    # Two active sessions also need the two-card handoff screen.
    screen = ('handoff' if len(opened)+len(ready) >= 2
              else 'active' if opened else 'login')
    return {'screen': screen, 'open': opened, 'ready': ready, 'now': moment.isoformat(),
            'grace_minutes': 60}


def state(session: dict, profile: dict) -> dict:
    now = datetime.now(TZ)
    opened, ready = shift_choices(profile, now, session['a'])
    available = opened + ready
    selected = next((x for x in opened if x['id'] == session['s']), None)
    catalog = []
    with _db() as conn:
        existing = {(r['code'],r['shift_day']):r for r in conn.execute(
            'SELECT code,shift_day,owner,open,closed_reason FROM shifts')}
        active_count = _current_open_count(conn, now)
    for code in SHIFT_RULES:
        match = next((x for x in available if x['code'] == code), None)
        if match:
            catalog.append(match)
            continue
        day = eligible_date(code, now)
        reason = 'Outside reporting window'
        if day is not None:
            record = existing.get((code,day.isoformat()))
            if record is not None and not record['open']:
                reason = 'Shift previously closed for ' + day.isoformat()
            elif record is not None and not profile.get('all_shifts') and record['owner'] != session['a']:
                reason = 'Another associate owns this shift'
            elif active_count >= 2:
                reason = 'Two overlapping shifts are already active'
            elif now > shift_times(code,day)[1]:
                reason = 'New shift start window has ended'
            elif not allowed(profile,code,day,session['a']):
                reason = 'Not assigned to this shift'
        catalog.append({'code': code,'label': SHIFT_RULES[code][0],
                        'action': 'unavailable','open': False,'reason':reason})
    return {'name': profile.get('name', 'Associate'), 'associate': session['a'],
            'role': 'manager' if profile.get('all_shifts') else
                    'mst' if profile.get('is_mst') else 'technician',
            'catalog': catalog,
            'all_shifts': bool(profile.get('all_shifts')),
            'assigned_shift': profile.get('shift'), 'selected': selected,
            'report_saved': bool(selected and selected.get('report_saved')),
            'shifts': available, 'open': opened, 'ready': ready, 'now': now.isoformat(),
            'grace_minutes': 60, 'production_connected': False,
            'facility': v31_facility.public_fields(v31_facility.load(PRIVATE))}


@app.middleware('http')
async def headers(request: Request, call_next):
    # Network publication is intentionally unsupported until SSO and TLS are in place.
    if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient', 'localhost'):
        return Response('Loopback-only prototype', status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


def _inject_before_last(html: str, closing_tag: str, insertion: str) -> str:
    """Insert `insertion` immediately before the LAST occurrence of
    `closing_tag`, never via a blanket str.replace().

    A served prototype page can legitimately contain the literal text of a
    closing tag (e.g. '</body>' or '</head>') inside a JS string -- a
    client-composed HTML report template, for instance. The real structural
    closing tag is always the final one in a well-formed document, so
    targeting only the last occurrence is safe regardless of what appears
    earlier in inline script content. Bug found 2026-10-29: a naive
    html.replace('</body>', ...) here once silently corrupted an inline
    script that happened to contain that exact substring inside a JS
    string, breaking every button wired after it on the page.
    """
    index = html.rfind(closing_tag)
    if index < 0:
        raise RuntimeError(f'Expected closing tag {closing_tag!r} not found in served page')
    return html[:index] + insertion + html[index:]


@app.get('/', response_class=HTMLResponse)
@app.get('/index.html', response_class=HTMLResponse)
def signin_page(request: Request):
    session = parse_cookie(request.cookies.get(SESSION_COOKIE))
    if session and state(session, roster()[session['a']])['selected']:
        return RedirectResponse('/shift-report.html', status_code=303)
    html = (ROOT / 'prototypes' / 'login-flow.html').read_text(encoding='utf-8')
    # Keep the approved original page CSS and media; remove its demo-only JS.
    start = html.rfind('<script>')
    end = html.rfind('</script>')
    if start < 0 or end < start:
        raise RuntimeError('Login prototype script missing')
    html = html[:start] + ('<script defer src="/web/auth-ui.js"></script>' +
                           '<script defer src="/web/roster-admin.js"></script>') + html[end + len('</script>'):]
    # Prevent the first-screen/two-pane flash while the client checks shift state.
    # Client removes this rule when the correct login, active or handoff screen is ready.
    html = _inject_before_last(html, '</head>',
        '<link rel="stylesheet" href="/web/login-layout.css"><style id="v31-preload">body{visibility:hidden}html{background:#fff}</style>')
    return HTMLResponse(html)


@app.get('/shift-report.html', response_class=HTMLResponse)
def landing(request: Request):
    session, profile = who(request)
    if not state(session, profile)['selected']:
        return RedirectResponse('/', status_code=303)
    html = (ROOT / 'prototypes' / 'shift-report.html').read_text(encoding='utf-8')
    return HTMLResponse(_inject_before_last(html, '</body>',
        '<script defer src="/web/workspace-auth.js"></script>'+
        '<script defer src="/web/roster-admin.js"></script>'+
        '<script defer src="/web/facility-admin.js"></script>'+
        '<script defer src="/web/shift-history.js"></script>'+
        '<script defer src="/web/manual-oos.js"></script>'+
        '<script defer src="/web/breakdown-lifecycle.js"></script>'+
        '<script defer src="/web/shift-data.js"></script>'+
        '<script defer src="/web/pm-needing-completion.js"></script>'+
        '<script defer src="/web/analytics.js"></script>'))


@app.get('/administration', response_class=HTMLResponse)
def administration_page(request: Request):
    """Allow authorized management to manage Drax without starting a shift."""
    session, profile = who(request)
    if not profile.get('all_shifts'):
        raise HTTPException(403, 'Management access required')
    html = (ROOT / 'prototypes' / 'shift-report.html').read_text(encoding='utf-8')
    return HTMLResponse(_inject_before_last(html, '</body>',
        '<script defer src="/web/roster-admin.js"></script>'+
        '<script defer src="/web/facility-admin.js"></script>'+
        '<script defer src="/web/shift-history.js"></script>'+
        '<script defer src="/web/admin-standalone.js"></script>'+
        '<script defer src="/web/analytics.js"></script>'))


class LoginRequest(BaseModel):
    associate: str = Field(pattern=r'^\d{1,12}$', max_length=12)
    intent: Literal['direct', 'select'] = 'direct'


class ShiftChoice(BaseModel):
    code: Literal['S1', 'S2', 'S3', 'S4', 'S5']


class ShiftResume(BaseModel):
    shift_id: int = Field(gt=0)


@app.get('/api/entry')
def entry_status():
    return public_flow()


@app.post('/api/login')
def login(payload: LoginRequest, request: Request, response: Response):
    _origin_check(request)
    profile = roster().get(payload.associate)
    if not profile:
        raise HTTPException(401, 'Associate number not authorized for this local preview')
    # Drax-active associates enter their assigned shifts. Manager-approved
    # MST overtime is the only non-management exception to that rule.
    if profile.get('all_shifts'):
        _set_cookie(response, payload.associate)
        _record_login_event(payload.associate, 'manager_signin')
        return {'destination': '/', 'manager_selection_required': True,
                'role': 'manager'}
    if payload.intent == 'select':
        # Popup path: do not create/attach an MST shift before a choice is checked.
        _set_cookie(response, payload.associate)
        _record_login_event(payload.associate, 'popup_signin')
        return {'destination': '/', 'selection_required': True,
                'role': 'mst' if profile.get('is_mst') else 'technician'}
    now = datetime.now(TZ)
    opened, ready = shift_choices(profile, now, payload.associate)
    # Owners resume; other users may not silently take control of an open report.
    if opened:
        # Assigned shift already opened by this MST: resume that report.
        selected = opened[0]
    elif ready:
        # Start assigned shift only if nobody else owns the current instance.
        target = ready[0]
        with _db() as conn:
            if _current_open_count(conn, now) >= 2:
                raise HTTPException(409, 'Two overlapping shifts are already active')
            conn.execute('''INSERT OR IGNORE INTO shifts
                (code,shift_day,owner,created_at,owner_role) VALUES (?,?,?,?,?)''',
                (target['code'], target['date'], payload.associate, now.isoformat(),
                 'mst' if profile.get('is_mst') else 'technician'))
            row = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                               (target['code'], target['date'])).fetchone()
            if not row['open'] or row['owner'] != payload.associate:
                raise HTTPException(409, 'This shift is already owned by another associate. Contact management.')
            selected = entry(row)
    else:
        # A technician-owned active report can be explicitly taken over by an
        # on-shift MST. Present a confirmation; do not change its owner here.
        if profile.get('is_mst'):
            with _db() as conn:
                candidate = conn.execute(
                    'SELECT * FROM shifts WHERE open=1 AND owner_role=? ORDER BY id DESC',
                    ('technician',)).fetchall()
            for row in candidate:
                day = date.fromisoformat(row['shift_day'])
                if (row['code'] == profile.get('shift') and
                        eligible_date(row['code'], now) == day):
                    _set_cookie(response, payload.associate)
                    _record_login_event(payload.associate, 'takeover_prompt', row['id'])
                    return {'destination': '/', 'role': 'mst',
                            'takeover_required': True, 'shift_id':row['id'],
                            'shift':row['code']}
        code = profile.get('shift')
        raise HTTPException(409, f'{code} is outside its reporting window, or its report belongs to another associate.')
    _set_cookie(response, payload.associate, selected['id'])
    _record_login_event(payload.associate, 'mst_signin' if profile.get('is_mst') else 'technician_signin', selected['id'])
    return {'destination': '/shift-report.html',
            'role': 'mst' if profile.get('is_mst') else 'technician',
            'shift': selected['code']}


@app.get('/api/me')
def me(request: Request):
    session, profile = who(request)
    return state(session, profile)


@app.post('/api/shifts/start')
def start_shift(payload: ShiftChoice, request: Request, response: Response):
    _origin_check(request)
    session, profile = who(request)
    now = datetime.now(TZ)
    expire_shifts(now)
    day = eligible_date(payload.code, now)
    if day is None:
        raise HTTPException(409, 'Shift reporting window is not active')
    if not allowed(profile, payload.code, day, session['a']):
        raise HTTPException(403, 'Shift is not assigned to this associate')
    with _db() as conn:
        existing = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                                (payload.code, day.isoformat())).fetchone()
        if existing is None and now > shift_times(payload.code, day)[1]:
            raise HTTPException(409, 'New shift may not start after its scheduled end')
        if existing is None and _current_open_count(conn, now) >= 2:
            raise HTTPException(409, 'Two overlapping shifts are already active')
        conn.execute('''INSERT OR IGNORE INTO shifts
            (code,shift_day,owner,created_at,owner_role) VALUES (?,?,?,?,?)''',
            (payload.code, day.isoformat(), session['a'], now.isoformat(),
             'management' if profile.get('all_shifts') else
             'mst' if profile.get('is_mst') else 'technician'))
        row = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                           (payload.code, day.isoformat())).fetchone()
        if not row['open']:
            raise HTTPException(409, 'This shift was already ended')
        if not profile.get('all_shifts') and row['owner'] != session['a']:
            raise HTTPException(403, 'The shift belongs to another associate')
        selected = entry(row)
    _set_cookie(response, session['a'], selected['id'])
    _record_login_event(session['a'], 'start_shift', selected['id'])
    return selected


@app.post('/api/shifts/reopen')
def reopen_shift(payload: ShiftChoice, request: Request, response: Response):
    """Explicitly resume a prior local test date without changing Mac time.

    Limited to the designated local developer management account. Preserves
    all previous work, EML snapshots and audit history until deliberately
    edited; never changes a report silently just because the clock moved.
    """
    _origin_check(request)
    session, profile = who(request)
    if session['a'] != '2759' or not profile.get('all_shifts'):
        raise HTTPException(403, 'Only the authorized local developer can reopen a dated report')
    now = datetime.now(TZ)
    day = eligible_date(payload.code, now)
    if day is None or now > shift_times(payload.code, day)[1]:
        raise HTTPException(409, 'Shift must be inside its start window to reopen')
    expire_shifts(now)
    with _db() as conn:
        row = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                           (payload.code,day.isoformat())).fetchone()
        if row is None or row['open']:
            raise HTTPException(409, 'No previously closed report exists for this shift date')
        if _current_open_count(conn, now) >= 2:
            raise HTTPException(409, 'Two overlapping shifts are already active')
        result=conn.execute("""UPDATE shifts SET open=1,closed_at='',closed_reason=''
                WHERE id=? AND open=0""",(row['id'],))
        if result.rowcount != 1:
            raise HTTPException(409, 'Shift state changed before reopening')
        selected_id=row['id']
    _set_cookie(response,session['a'],selected_id)
    _record_login_event(session['a'],'explicit_historical_reopen',selected_id)
    with _db() as conn:
        current=conn.execute('SELECT * FROM shifts WHERE id=?',(selected_id,)).fetchone()
    return entry(current)


@app.post('/api/shifts/takeover')
def takeover_shift(payload: ShiftResume, request: Request, response: Response):
    """Explicit MST relief of a non-MST-owned shift, with permanent audit.

    Never allow an MST to take over another MST's report, and never silently
    promote an associate who has not been marked MST in Drax/admin overrides.
    """
    _origin_check(request)
    session, profile = who(request)
    if profile.get('all_shifts') or not profile.get('is_mst'):
        raise HTTPException(403, 'Only an assigned MST may take over a technician-owned shift')
    now = datetime.now(TZ)
    with _db() as conn:
        row = conn.execute('SELECT * FROM shifts WHERE id=? AND open=1',
                           (payload.shift_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Active report not found')
        day = date.fromisoformat(row['shift_day'])
        if eligible_date(row['code'],now) != day:
            raise HTTPException(409, 'Reporting window has ended')
        # Even an MST with a *separate* overtime grant cannot take control of
        # an off-shift technician's report. Relief requires the home shift.
        if row['code'] != profile.get('shift'):
            raise HTTPException(403, 'MST takeover is restricted to the Drax-assigned shift')
        if row['owner_role'] != 'technician' or row['owner'] == session['a']:
            raise HTTPException(409, 'Only a report currently owned by a non-MST technician can be taken over')
        old_owner = row['owner']
        updated = conn.execute(
            "UPDATE shifts SET owner=?,owner_role='mst' WHERE id=? AND owner=? AND owner_role='technician' AND open=1",
            (session['a'], row['id'], old_owner))
        if updated.rowcount != 1:
            raise HTTPException(409, 'Shift owner changed before takeover')
        conn.execute('''INSERT INTO shift_owner_transfers
            (shift_id,previous_owner,new_owner,transferred_at,reason)
            VALUES (?,?,?,?,'mst_relief')''',
            (row['id'],old_owner,session['a'],now.isoformat()))
        selected = entry(row)
    _set_cookie(response,session['a'],selected['id'])
    _record_login_event(session['a'],'mst_takeover',selected['id'])
    return {'taken_over':True,'shift_id':selected['id'],'shift':selected['code']}


@app.post('/api/shifts/resume')
def resume_shift(payload: ShiftResume, request: Request, response: Response):
    _origin_check(request)
    session, profile = who(request)
    with _db() as conn:
        row = conn.execute('SELECT * FROM shifts WHERE id=? AND open=1', (payload.shift_id,)).fetchone()
    if row is None or eligible_date(row['code'], datetime.now(TZ)) != date.fromisoformat(row['shift_day']):
        raise HTTPException(404, 'Active shift not found within its reporting window')
    if not allowed(profile, row['code'], date.fromisoformat(row['shift_day']), session['a']):
        raise HTTPException(403, 'Shift is not assigned to this associate')
    if not profile.get('all_shifts') and row['owner'] != session['a']:
        if (profile.get('is_mst') and row['owner_role'] == 'technician'
                and profile.get('shift') == row['code']):
            raise HTTPException(409, 'MST takeover required')
        raise HTTPException(403, 'Only the current shift owner or management may resume this report')
    _set_cookie(response, session['a'], row['id'])
    _record_login_event(session['a'], 'resume_shift', row['id'])
    return entry(row)


@app.post('/api/logout')
def logout(request: Request, response: Response):
    _origin_check(request)
    session = parse_cookie(request.cookies.get(SESSION_COOKIE))
    if session:
        _record_login_event(session['a'], 'sign_out', session['s'] or None)
    response.delete_cookie(SESSION_COOKIE, path='/')
    return {'signed_out': True}

class EndShiftRequest(BaseModel):
    acknowledge_warnings: bool = False


def _end_shift_readiness(conn,row):
    """A saved, intact EML is mandatory; actual outstanding work is
    acknowledged, never automatically marked RTS or discarded."""
    saved=bool(row['report_saved_at'] and row['report_eml'] and row['report_hash'])
    digest_ok=saved and hashlib.sha256(bytes(row['report_eml'])).hexdigest()==row['report_hash']
    blockers=[]
    if not saved:
        blockers.append('Generate and save the Outlook passdown before End Shift.')
    elif not digest_ok:
        blockers.append('Saved Outlook report failed integrity verification; regenerate it.')
    stage=conn.execute("SELECT applied_at FROM report_stage WHERE shift_id=?",(row['id'],)).fetchone() if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_stage'").fetchone() else None
    if stage and not stage['applied_at']:
        blockers.append('A newer Cetaris file is staged but not imported. Confirm its technicians first.')
    warnings=[]
    import v31_reporting
    v31_reporting._table(conn)
    included=conn.execute("""SELECT COUNT(*) FROM report_wo WHERE shift_id=?
        AND excluded=0 AND selection_hidden=0""",(row['id'],)).fetchone()[0]
    outstanding=conn.execute("""SELECT COUNT(*) FROM report_wo WHERE shift_id=?
        AND excluded=0 AND selection_hidden=0 AND status='PENDING'""",(row['id'],)).fetchone()[0]
    if outstanding:
        warnings.append(f'{outstanding} pending work order(s) require follow-up.')
    # Global OOS and explicit maintenance holds can span multiple shifts.
    # They are never automatically cleared by End Shift.
    breakdowns=v31_breakdown.overview(conn,row['id'])
    active_assets={
        item['asset'].strip().casefold() for item in
        (breakdowns['open']+breakdowns['holds']+v31_oos.list_records(conn,open_only=True))
        if item['asset'].strip()
    }
    if active_assets:
        warnings.append(f'{len(active_assets)} asset(s) remain out of service or on manual hold.')
    draft=v31_reporting.get_draft(conn,row['id'])
    if not draft.get('loto'):
        warnings.append('LOTO verification is not marked complete.')
    missing=draft.get('missingKeys') or []
    if missing:
        warnings.append(f'{len(missing)} LOTO key(s) are marked missing.')
    completed_pm=conn.execute("SELECT COUNT(*) FROM report_pm WHERE shift_id=? AND selection_hidden=0",
                              (row['id'],)).fetchone()[0]
    import v31_pm_needing_completion as v31_pm
    pm_needs=v31_pm.get(conn,row['id'])['present']
    has_other=bool((draft.get('notes') or '').strip() or draft.get('vendors') or draft.get('ro'))
    if not included and not completed_pm and not pm_needs and not has_other:
        warnings.append('No reportable work or shift notes were entered. Confirm this was a quiet shift.')
    return {'ready':not blockers,'saved':saved,'hash_verified':bool(digest_ok),
            'blockers':blockers,'warnings':warnings,
            'requires_acknowledgement':bool(warnings),
            'pending_work_orders':outstanding}


@app.get('/api/shifts/end/readiness')
def end_shift_readiness(request:Request):
    _session,row=_report_access(request)
    with _db() as conn:
        return _end_shift_readiness(conn,row)


@app.post('/api/shifts/end')
def end_shift(request: Request, response: Response, payload: EndShiftRequest | None = None):
    """Finish only the bound session; overlapping sessions remain untouched."""
    _origin_check(request)
    session, profile = who(request)
    if not session['s']:
        raise HTTPException(409, 'No selected shift')
    with _db() as conn:
        row = conn.execute('SELECT * FROM shifts WHERE id=? AND open=1',
                           (session['s'],)).fetchone()
        if row is None or not allowed(profile, row['code'], date.fromisoformat(row['shift_day']), session['a']):
            raise HTTPException(404, 'Active shift not found')
        if not profile.get('all_shifts') and row['owner'] != session['a']:
            raise HTTPException(403, 'Only the shift starter or management may close it')
        if eligible_date(row['code'],datetime.now(TZ)) != date.fromisoformat(row['shift_day']):
            raise HTTPException(409, 'Reporting window has ended')
        readiness=_end_shift_readiness(conn,row)
        if readiness['blockers']:
            raise HTTPException(409,' '.join(readiness['blockers']))
        if readiness['warnings'] and not (payload and payload.acknowledge_warnings):
            raise HTTPException(409,'Review and acknowledge the outstanding End Shift warnings first')
        now=datetime.now(TZ).isoformat()
        conn.execute("""CREATE TABLE IF NOT EXISTS shift_end_review(
            shift_id INTEGER PRIMARY KEY, actor TEXT NOT NULL,
            warnings_json TEXT NOT NULL, acknowledged_at TEXT NOT NULL)""")
        conn.execute("""INSERT OR REPLACE INTO shift_end_review
           (shift_id,actor,warnings_json,acknowledged_at) VALUES(?,?,?,?)""",
           (row['id'],session['a'],json.dumps(readiness['warnings']),now))
        conn.execute("""UPDATE shifts SET open=0, closed_at=?,closed_reason='completed'
                        WHERE id=?""",(now,session['s']))
    _set_cookie(response, session['a'], 0)
    _record_login_event(session['a'], 'end_shift', session['s'])
    return {'ended': True, 'shift_id': session['s']}


class ReportPayload(BaseModel):
    # Legacy browser text is intentionally ignored. The server builds a
    # canonical report from saved shift work and drafts.
    content: str | None = Field(default=None,max_length=90000)


class AdminAddAssociate(BaseModel):
    number: str = Field(pattern=r'^\d{1,12}$')
    name: str = Field(min_length=2, max_length=180)
    shift: Literal['', 'S1', 'S2', 'S3', 'S4', 'S5'] = ''
    is_mst: bool = True


class AdminAssociateOverride(BaseModel):
    number: str = Field(pattern=r'^\d{1,12}$')
    is_mst: bool | None = None
    shift: Literal['S1', 'S2', 'S3', 'S4', 'S5'] | None = None
    reset: bool = False


class AdminEditAssociate(BaseModel):
    number: str = Field(min_length=1, max_length=12, pattern='^[0-9]+$')
    full_name: str = Field(min_length=2, max_length=180)
    new_number: str | None = Field(default=None, min_length=1, max_length=12, pattern='^[0-9]+$')

def require_manager(request: Request) -> dict:
    session, profile = who(request)
    if not profile.get('all_shifts'):
        raise HTTPException(403, 'Management access required')
    return session


class MstOvertimeGrant(BaseModel):
    associate: str = Field(pattern=r'^\d{1,12}$')
    code: Literal['S1','S2','S3','S4','S5']
    shift_day: date
    approved: bool = True


@app.get('/api/admin/mst-overtime')
def list_mst_overtime(request: Request):
    """Management review of all explicitly approved dated MST overtime."""
    require_manager(request)
    with _db() as conn:
        rows = conn.execute('''SELECT associate,code,shift_day,granted_by,granted_at
            FROM mst_overtime_grants ORDER BY shift_day DESC,code,associate''').fetchall()
    return {'grants':[dict(row) for row in rows]}


@app.post('/api/admin/mst-overtime')
def set_mst_overtime(payload: MstOvertimeGrant, request: Request):
    """Management approves/revokes a specific MST and shift date only."""
    _origin_check(request)
    manager = require_manager(request)
    target = roster().get(payload.associate)
    if not target or target.get('all_shifts') or not target.get('is_mst'):
        raise HTTPException(403, 'Overtime may be granted only to an active MST')
    if payload.shift_day.weekday() not in SHIFT_RULES[payload.code][3]:
        raise HTTPException(422, 'Shift is not scheduled on that date')
    if target['shift'] == payload.code:
        raise HTTPException(422, 'This is already the MST home shift')
    with _db() as conn:
        if payload.approved:
            conn.execute('''INSERT OR REPLACE INTO mst_overtime_grants
                (associate,code,shift_day,granted_by,granted_at)
                VALUES (?,?,?,?,?)''',
                (payload.associate,payload.code,payload.shift_day.isoformat(),
                 manager['a'],datetime.now(TZ).isoformat()))
        else:
            conn.execute('DELETE FROM mst_overtime_grants WHERE associate=? AND code=? AND shift_day=?',
                (payload.associate,payload.code,payload.shift_day.isoformat()))
    return {'associate':payload.associate,'code':payload.code,
            'shift_day':payload.shift_day.isoformat(),'approved':payload.approved}


@app.get('/api/admin/roster')
def admin_roster(request: Request):
    require_manager(request)
    return v31_roster.status(DB_FILE, include_rows=True)


@app.post('/api/admin/roster/reload')
def admin_roster_reload(request: Request):
    _origin_check(request)
    require_manager(request)
    v31_roster.sync(PRIVATE, DB_FILE, force=True)
    return v31_roster.status(DB_FILE, include_rows=True)


@app.post('/api/admin/roster/add')
def admin_roster_add(payload: AdminAddAssociate, request: Request):
    _origin_check(request)
    require_manager(request)
    if payload.number in MANAGEMENT_ASSOCIATES:
        raise HTTPException(403, 'Management profiles cannot be replaced')
    try:
        v31_roster.add_manual(DB_FILE, payload.number, payload.name.strip(), payload.shift, payload.is_mst)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return v31_roster.status(DB_FILE, include_rows=True)


@app.post('/api/admin/roster/override')
def admin_roster_override(payload: AdminAssociateOverride, request: Request):
    _origin_check(request)
    require_manager(request)
    if payload.number in MANAGEMENT_ASSOCIATES:
        raise HTTPException(403, 'Management profiles cannot be modified here')
    if not payload.reset and payload.is_mst is None and payload.shift is None:
        raise HTTPException(422, 'Provide a status/shift override or reset')
    try:
        v31_roster.set_override(DB_FILE, payload.number,
                                mst=payload.is_mst, shift=payload.shift, reset=payload.reset)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    return v31_roster.status(DB_FILE, include_rows=True)


@app.post('/api/admin/roster/edit')
def admin_roster_edit(payload: AdminEditAssociate, request: Request):
    _origin_check(request)
    require_manager(request)
    if payload.number in MANAGEMENT_ASSOCIATES or payload.new_number in MANAGEMENT_ASSOCIATES:
        raise HTTPException(403, 'Management profiles cannot be edited')
    try:
        v31_roster.edit_associate(DB_FILE, payload.number, payload.full_name.strip(),
                                  payload.new_number)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return v31_roster.status(DB_FILE, include_rows=True)


class FacilitySettingsPayload(BaseModel):
    distribution_center: str = Field(default='IND3', max_length=20)
    recipients: str = Field(default='', max_length=2000)
    sender_name: str = Field(default='IND3 Shift Passdown', max_length=120)


@app.get('/api/admin/settings')
def admin_settings(request: Request):
    require_manager(request)
    return v31_facility.load(PRIVATE)


@app.post('/api/admin/settings')
def admin_settings_save(payload: FacilitySettingsPayload, request: Request):
    _origin_check(request)
    require_manager(request)
    addresses = [a.strip() for a in payload.recipients.split(',') if a.strip()]
    for address in addresses:
        if '@' not in address or ' ' in address:
            raise HTTPException(422, f'Invalid recipient email: {address}')
    return v31_facility.save(PRIVATE, {
        'distribution_center': payload.distribution_center.strip() or 'IND3',
        'recipients': ','.join(addresses),
        'sender_name': payload.sender_name.strip() or 'IND3 Shift Passdown',
    })


def _owner_name(row) -> str:
    """Display name of whoever actually owns this shift (not necessarily the
    current viewer -- a manager can view/generate another MST's shift),
    for the IND3 Next Gen Outlook signature."""
    return roster().get(row['owner'], {}).get('name', '')


@app.get('/api/shifts/report/preview')
def preview_shift_report(request: Request):
    session,row=_report_access(request)
    with _db() as conn:
        report=v31_report_export.build(conn,row['id'],row['code'],row['shift_day'],PRIVATE,_owner_name(row))
    return {'html':report['html'],'sha256':report['sha256'],
            'attachments':report['attachments']}


@app.post('/api/shifts/report')
def save_shift_report(payload: ReportPayload, request: Request):
    """Generate and persist a canonical EML snapshot from DB, never client text."""
    _origin_check(request)
    session,row=_report_access(request)
    with _db() as conn:
        report=v31_report_export.build(conn,row['id'],row['code'],row['shift_day'],PRIVATE,_owner_name(row))
        conn.execute("""UPDATE shifts SET
          report_content=?,report_html=?,report_eml=?,report_hash=?,
          report_saved_at=?,report_count=report_count+1 WHERE id=?""",
          (report['html'],report['html'],report['eml'],report['sha256'],
           report['created_at'],row['id']))
    return {'saved':True,'shift_id':row['id'],
            'saved_at':report['created_at'],'sha256':report['sha256'],
            'filename':report['filename'],'attachments':report['attachments']}


@app.get('/api/shifts/report/download')
def download_shift_report(request: Request):
    _session,row=_report_access(request)
    with _db() as conn:
        saved=conn.execute('SELECT report_eml FROM shifts WHERE id=?',(row['id'],)).fetchone()
    if not saved or not saved['report_eml']:
        raise HTTPException(409, 'Generate and save a passdown before download')
    filename=f"IND3_{row['code']}_{row['shift_day']}_Shift_Passdown.eml"
    return Response(content=bytes(saved['report_eml']),media_type='message/rfc822',
                    headers={'Content-Disposition':f'attachment; filename="{filename}"'})


@app.get('/api/shifts/report')
def read_shift_report(request: Request):
    _session,row=_report_access(request)
    with _db() as conn:
        saved=conn.execute('SELECT report_content,report_saved_at,report_hash FROM shifts WHERE id=?',
                           (row['id'],)).fetchone()
    return {'content':saved['report_content'],
            'saved_at':saved['report_saved_at'],'sha256':saved['report_hash'],
            'shift_id':row['id']}

def _report_access(request: Request):
    session, profile = who(request)
    shift_id = session.get('s')
    if not shift_id:
        raise HTTPException(409, 'Choose a shift before using Shift Report')
    with _db() as conn:
        row = conn.execute('SELECT * FROM shifts WHERE id=? AND open=1',(shift_id,)).fetchone()
    if not row:
        raise HTTPException(409, 'Selected shift is no longer open')
    if not allowed(profile,row['code'],date.fromisoformat(row['shift_day']),session['a']) or (
        not profile.get('all_shifts') and row['owner'] != session['a']
    ):
        raise HTTPException(403, 'You are not authorized for this shift')
    if eligible_date(row['code'],datetime.now(TZ)) != date.fromisoformat(row['shift_day']):
        raise HTTPException(409, 'Reporting window has ended')
    return session,row


def _invalidate_passdown(conn,shift_id):
    # Edited data cannot be followed by End Shift until a new EML is saved.
    conn.execute("""UPDATE shifts SET report_saved_at='',report_content='',
          report_html='',report_hash='',report_eml=X'' WHERE id=?""",(shift_id,))


@app.get('/api/pm-needing-completion')
def pm_needing_completion_state(request:Request):
    _session,shift=_report_access(request)
    with _db() as conn:
        return v31_pm.get(conn,shift['id'])


@app.post('/api/pm-needing-completion/upload')
async def pm_needing_completion_upload(request:Request,file:UploadFile=File(...)):
    _origin_check(request)
    session,shift=_report_access(request)
    try:
        data=await file.read(v31_pm.MAX_BYTES+1)
    finally:
        await file.close()
    try:
        with _db() as conn:
            result=v31_pm.upload(conn,shift['id'],file.filename or '',data,session['a'])
            _invalidate_passdown(conn,shift['id'])
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    return result


@app.get('/api/pm-needing-completion/download')
def pm_needing_completion_download(request:Request):
    _session,shift=_report_access(request)
    with _db() as conn:
        pm=v31_pm.get(conn,shift['id'],include_data=True)
    if not pm['present']:
        raise HTTPException(404,'No PM report uploaded')
    from urllib.parse import quote
    return Response(content=pm['file_data'],media_type='application/octet-stream',
        headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(pm['filename'])})


@app.delete('/api/pm-needing-completion')
def pm_needing_completion_delete(request:Request):
    _origin_check(request)
    _session,shift=_report_access(request)
    with _db() as conn:
        removed=v31_pm.delete(conn,shift['id'])
        if removed:
            _invalidate_passdown(conn,shift['id'])
    return {'removed':removed}


class ReportingImport(BaseModel):
    technicians: list[str] = Field(min_length=1,max_length=100)
    closure_reviews: dict[str,Literal['add','ignore']] = Field(default_factory=dict)


class ReportingWOEdit(BaseModel):
    asset: str | None = None
    category: str | None = None
    cause: str | None = None
    tech: str | None = None
    remarks: str | None = None
    status: Literal['RTS','OOS','PENDING'] | None = None
    excluded: bool | None = None


class ReportingDraft(BaseModel):
    vendors: list[str] = Field(default_factory=list,max_length=100)
    ro: list[str] = Field(default_factory=list,max_length=100)
    notes: str = Field(default='',max_length=3000)
    loto: bool = False
    missingKeys: list[str] = Field(default_factory=list,max_length=20)


@app.get('/api/reporting/state')
def reporting_state(request: Request):
    session,row=_report_access(request)
    with _db() as conn:
        data=v31_reporting.get_state(conn,row['id'],row['code'],row['shift_day'])
        data['draft']=v31_reporting.get_draft(conn,row['id'])
        shift_row=conn.execute("SELECT report_saved_at FROM shifts WHERE id=?",(row['id'],)).fetchone()
    # Reporting UI needs technician names / shift groups, never associate IDs.
    roster_info=v31_roster.status(DB_FILE,include_rows=True)
    data['tech_suggestions']=v31_reporting.technician_suggestions(
        data['stage']['technicians'],roster_info['associates'],row['code'])
    data['roster_source_connected']=bool(roster_info['last_synced']) and not bool(roster_info['error'])
    data['report_saved']=bool(shift_row and shift_row['report_saved_at'])
    return {'shift':entry(row),**data}


def _stage_cetaris_sync(shift_id: int, content: bytes, filename: str, actor: str):
    """Run expensive Excel parsing and SQLite transaction off the event loop."""
    with _db() as conn:
        return v31_reporting.stage(conn,shift_id,content,filename,actor)


@app.post('/api/reporting/stage')
async def reporting_stage(request: Request,file: UploadFile=File(...)):
    _origin_check(request)
    session,row=_report_access(request)
    try:
        content=await file.read(v31_reporting.MAX_SIZE+1)
    finally:
        await file.close()
    if not content:
        raise HTTPException(422, 'File is empty')
    if len(content)>v31_reporting.MAX_SIZE:
        raise HTTPException(413, 'Cetaris export exceeds 12 MB')
    try:
        result=await run_in_threadpool(
            _stage_cetaris_sync,row['id'],content,
            file.filename or 'cetaris.xlsx',session['a'])
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    # Staging parses a file; it deliberately does not credit any WOs until
    # technicians have been selected and an explicit import is confirmed.
    roster_info=v31_roster.status(DB_FILE,include_rows=True)
    result['tech_suggestions']=v31_reporting.technician_suggestions(
        result['technicians'],roster_info['associates'],row['code'])
    return result


@app.post('/api/reporting/import')
def reporting_import(payload:ReportingImport,request:Request):
    _origin_check(request)
    session,row=_report_access(request)
    try:
        with _db() as conn:
            roster_info=v31_roster.status(DB_FILE,include_rows=True)
            source_shifts=v31_reporting.roster_shift_map(
                payload.technicians,roster_info['associates'])
            summary=v31_reporting.import_selection(conn,row['id'],row['code'],
                row['shift_day'],payload.technicians,payload.closure_reviews,
                roster_shifts=source_shifts)
            _invalidate_passdown(conn,row['id'])
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    return {'imported':True,'summary':summary}


@app.post('/api/reporting/work-orders/{wo_number}')
def reporting_work_order(wo_number:str,payload:ReportingWOEdit,request:Request):
    _origin_check(request)
    session,row=_report_access(request)
    if not wo_number.strip() or len(wo_number)>100:
        raise HTTPException(422,'Invalid work-order number')
    data=payload.model_dump(exclude_none=True)
    try:
        with _db() as conn:
            v31_reporting.update_wo(conn,row['id'],wo_number,data)
            _invalidate_passdown(conn,row['id'])
    except (KeyError,ValueError) as exc:
        raise HTTPException(404 if isinstance(exc,KeyError) else 422,str(exc))
    return {'updated':True,'wo_number':wo_number}


@app.post('/api/reporting/draft')
def reporting_draft(payload:ReportingDraft,request:Request):
    _origin_check(request)
    session,row=_report_access(request)
    content=payload.model_dump()
    if content['loto'] and content['missingKeys']:
        raise HTTPException(422,'LOTO cannot be verified while keys are marked missing')
    if sum(len(x) for x in content['vendors']+content['ro'])>15000:
        raise HTTPException(422,'Too much draft content')
    with _db() as conn:
        changed=v31_reporting.save_draft(conn,row['id'],content)
        if changed:
            _invalidate_passdown(conn,row['id'])
    return {'saved':True,'changed':changed,'shift_id':row['id']}


class AIApproval(BaseModel):
    text: str = Field(min_length=1,max_length=5000)


@app.get('/api/reporting/ai/status')
def reporting_ai_status(request:Request):
    _report_access(request)
    return ai_remark_rewriter.check_ai_available()


@app.post('/api/reporting/work-orders/{wo_number}/ai/suggest')
def reporting_ai_suggest(wo_number:str,request:Request):
    _origin_check(request)
    _session,row=_report_access(request)
    with _db() as conn:
        result=v31_reporting.request_ai_rewrite(conn,row['id'],wo_number)
    return result


@app.post('/api/reporting/work-orders/{wo_number}/ai/accept')
def reporting_ai_accept(wo_number:str,payload:AIApproval,request:Request):
    _origin_check(request)
    _session,row=_report_access(request)
    with _db() as conn:
        saved=conn.execute(
            'SELECT ai_status FROM report_wo WHERE shift_id=? AND wo_number=?',
            (row['id'],wo_number)).fetchone()
        if not saved or saved['ai_status']!='suggested':
            raise HTTPException(409,'No reviewable local AI suggestion available')
        v31_reporting.accept_ai_rewrite(conn,row['id'],wo_number,payload.text)
        _invalidate_passdown(conn,row['id'])
    return {'accepted':True,'wo_number':wo_number}


@app.post('/api/reporting/work-orders/{wo_number}/ai/reject')
def reporting_ai_reject(wo_number:str,request:Request):
    _origin_check(request)
    _session,row=_report_access(request)
    with _db() as conn:
        try:
            v31_reporting.reject_ai_rewrite(conn,row['id'],wo_number)
        except KeyError as exc:
            raise HTTPException(404,str(exc))
    return {'rejected':True}


@app.get('/api/breakdowns')
def breakdown_status(request:Request):
    _session,row=_report_access(request)
    with _db() as conn:
        return v31_breakdown.overview(conn,row['id'])


class BreakdownDecision(BaseModel):
    decision:Literal['rts','keep_oos']


@app.post('/api/breakdowns/{wo_number}/decision')
def breakdown_decision(wo_number:str,payload:BreakdownDecision,request:Request):
    """V3 Completed Breakdown requires explicit RTS/keep-OOS confirmation."""
    _origin_check(request)
    session,row=_report_access(request)
    try:
        with _db() as conn:
            result=v31_breakdown.decide(conn,wo_number,payload.decision,
                                         row['id'],session['a'])
            _invalidate_passdown(conn,row['id'])
    except KeyError as exc:
        raise HTTPException(409,str(exc))
    return result


class BreakdownHold(BaseModel):
    asset: str = Field(min_length=1,max_length=120)
    enabled: bool = True
    reason: str = Field(default='',max_length=500)


@app.post('/api/breakdowns/hold')
def breakdown_hold(payload:BreakdownHold,request:Request):
    """Explicit manual OOS hold; a later Cetaris Closed WO may not undo it."""
    _origin_check(request)
    session,row=_report_access(request)
    with _db() as conn:
        data=v31_breakdown.hold(conn,payload.asset.strip(),payload.enabled,
                                 session['a'],payload.reason.strip())
        _invalidate_passdown(conn,row['id'])
    return data


class ManualHappening(BaseModel):
    asset: str = Field(min_length=1,max_length=120)
    cause: str = Field(min_length=1,max_length=4000)
    remarks: str = Field(default='',max_length=5000)
    category: Literal['Breakdown','Process Interruption','Proactive Repair','Work Request'] = 'Proactive Repair'
    status: Literal['RTS','OOS','PENDING'] = 'PENDING'
    wo_reference: str = Field(default='',max_length=100)


@app.post('/api/reporting/manual')
def reporting_manual(payload:ManualHappening,request:Request):
    """Log real corrective/proactive work not present in a Cetaris export."""
    _origin_check(request)
    session,row=_report_access(request)
    profile=roster()[session['a']]
    with _db() as conn:
        linked_oos=None
        if payload.status=='OOS':
            active=next((x for x in v31_oos.list_records(conn)
                         if x['asset'].casefold()==payload.asset.strip().casefold()),None)
            if active:
                linked_oos=active['id']
            else:
                entry=v31_oos.create(conn,asset=payload.asset.strip(),
                    cause=payload.cause.strip(),notes=payload.remarks.strip(),
                    logged_by=session['a'],logged_by_name=profile['name'],
                    shift_id=row['id'],shift_code=row['code'],
                    now_iso=datetime.now(TZ).isoformat())
                linked_oos=entry['id']
        manual=v31_reporting.add_manual_happening(conn,row['id'],
            asset=payload.asset,cause=payload.cause,remarks=payload.remarks,
            category=payload.category,status=payload.status,
            technician=profile.get('name','Shift Associate'),
            reference_wo=payload.wo_reference,manual_oos_id=linked_oos)
        _invalidate_passdown(conn,row['id'])
    return manual


class CarryOverFlag(BaseModel):
    wo_number: str = Field(min_length=1,max_length=100)
    enabled: bool = True


class CarryOverDecision(BaseModel):
    source_row_id: int = Field(gt=0)
    action: Literal['adopt','dismiss']


@app.get('/api/reporting/carryovers')
def reporting_carryovers(request:Request):
    _session,row=_report_access(request)
    with _db() as conn:
        rows=v31_reporting.available_carry_overs(conn,row['id'],row['shift_day'])
    return {'available':rows}


@app.post('/api/reporting/carryovers/flag')
def reporting_flag_carryover(payload:CarryOverFlag,request:Request):
    _origin_check(request)
    _session,row=_report_access(request)
    try:
        with _db() as conn:
            result=v31_reporting.flag_carry_over(conn,row['id'],
                payload.wo_number,payload.enabled)
            _invalidate_passdown(conn,row['id'])
    except KeyError as exc:
        raise HTTPException(404,str(exc))
    return result


@app.post('/api/reporting/carryovers/decision')
def reporting_resolve_carryover(payload:CarryOverDecision,request:Request):
    _origin_check(request)
    session,row=_report_access(request)
    try:
        with _db() as conn:
            result=v31_reporting.resolve_carry_over(conn,row['id'],
                 row['shift_day'],payload.source_row_id,payload.action,session['a'])
            _invalidate_passdown(conn,row['id'])
    except KeyError as exc:
        raise HTTPException(409,str(exc))
    return result


@app.get('/api/analytics/summary')
def analytics_summary(request:Request,
                      period:Literal['7d','30d','90d','all']='30d'):
    """Actual recorded V3.1 shift metrics only; no fake uptime or V2/V3 mixing."""
    session,profile=who(request)
    scope=None if profile.get('all_shifts') else session['a']
    with _db() as conn:
        return v31_analytics.summarize(conn,scope,period,datetime.now(TZ).date())


@app.get('/api/history/legacy')
def legacy_history(request: Request, limit: int = 50, offset: int = 0):
    session, profile = who(request)
    # Legacy source contains original shift IDs but not every login attempt.
    return v31_legacy_history.history(PRIVATE,
        None if profile.get('all_shifts') else session['a'],limit,offset)


@app.get('/api/history/login-events')
def new_login_history(request: Request, limit: int = 50, offset: int = 0):
    session, profile = who(request)
    limit = max(1,min(limit,100))
    offset = max(0,offset)
    admin = bool(profile.get('all_shifts'))
    with _db() as conn:
        if admin:
            rows=conn.execute('SELECT * FROM login_events ORDER BY id DESC LIMIT ? OFFSET ?',
                              (limit,offset)).fetchall()
        else:
            rows=conn.execute('SELECT * FROM login_events WHERE associate=? ORDER BY id DESC LIMIT ? OFFSET ?',
                              (session['a'],limit,offset)).fetchall()
    return {'events':[dict(row) for row in rows],'limit':limit,'offset':offset}


@app.get('/api/history/shifts')
def shift_history(request: Request, limit: int = 25, offset: int = 0):
    """V3.1's own shift records (not the V2/V3 archive) for the Shift
    History page: shift id/code/date/owner plus Happenings and Reports
    counts, newest first. Non-management sees only their own shifts."""
    session, profile = who(request)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    admin = bool(profile.get('all_shifts'))
    names = roster()
    with _db() as conn:
        if admin:
            total = conn.execute('SELECT COUNT(*) FROM shifts').fetchone()[0]
            rows = conn.execute(
                'SELECT * FROM shifts ORDER BY id DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        else:
            total = conn.execute(
                'SELECT COUNT(*) FROM shifts WHERE owner=?', (session['a'],)).fetchone()[0]
            rows = conn.execute(
                'SELECT * FROM shifts WHERE owner=? ORDER BY id DESC LIMIT ? OFFSET ?',
                (session['a'], limit, offset)).fetchall()
        happenings = v31_reporting.happenings_counts(conn, [row['id'] for row in rows])
    out = [{
        'id': row['id'],
        'code': row['code'],
        'date': row['shift_day'],
        'owner': names.get(row['owner'], {}).get('name', row['owner']),
        'happenings': happenings.get(row['id'], 0),
        'reports': row['report_count'],
        'status': 'Open' if row['open'] else 'Closed',
        'has_report': bool(row['report_content']),
    } for row in rows]
    return {'total': total, 'limit': limit, 'offset': offset, 'rows': out}


@app.get('/api/history/shifts/{shift_id}/eml')
def shift_history_eml(shift_id:int,request:Request):
    """Download the EXACT EML saved at report creation, never regenerate it."""
    session,profile=who(request)
    with _db() as conn:
        row=conn.execute('SELECT * FROM shifts WHERE id=?',(shift_id,)).fetchone()
        if not row:raise HTTPException(404,'Shift not found')
        if not profile.get('all_shifts') and row['owner']!=session['a']:
            raise HTTPException(403,'Not your shift')
        if row['report_eml']:
            content=bytes(row['report_eml'])
        elif row['report_saved_at']:
            # Old V3.1 history: fallback for records saved before this
            # snapshot migration, never pretend this is a byte-identical copy.
            content=v31_report_export.build(conn,shift_id,row['code'],
                     row['shift_day'],PRIVATE,_owner_name(row))['eml']
        else:
            raise HTTPException(404,'No saved passdown for this shift')
    filename=f"IND3_{row['code']}_{row['shift_day']}_Shift_Passdown.eml"
    return Response(content=content,media_type='message/rfc822',
                    headers={'Content-Disposition':f'attachment; filename="{filename}"'})

class ManualOOSCreate(BaseModel):
    asset: str = Field(min_length=1, max_length=120)
    cause: str = Field(default='', max_length=500)
    notes: str = Field(default='', max_length=1000)


class ManualOOSResolve(BaseModel):
    notes: str = Field(default='', max_length=500)


@app.post('/api/oos')
def oos_create(payload: ManualOOSCreate, request: Request):
    """Log an asset as Down/Degraded with no Cetaris WO behind it at all.
    Requires an active shift so the report it came from is known, but the
    record itself is global -- visible to every signed-in associate, not
    scoped to this shift or this MST."""
    _origin_check(request)
    session, shift = _report_access(request)
    profile = roster()[session['a']]
    with _db() as conn:
        record = v31_oos.create(conn, asset=payload.asset.strip(), cause=payload.cause.strip(),
            notes=payload.notes.strip(), logged_by=session['a'],
            logged_by_name=profile.get('name', 'Associate'), shift_id=session['s'],
            shift_code=shift['code'], now_iso=datetime.now(TZ).isoformat())
        # New OOS information changes the outgoing passdown.
        _invalidate_passdown(conn, session['s'])
    return record


@app.get('/api/oos')
def oos_list(request: Request):
    """Currently-open manual OOS records (global, every associate sees the
    same list) plus this session's own shift's resolved-this-shift list
    (for that shift's own passdown RTS section and tab confirmation UI)."""
    session, _shift = _report_access(request)
    with _db() as conn:
        open_items = v31_oos.list_records(conn, open_only=True)
        resolved_this_shift = v31_oos.resolved_this_shift(conn, session['s']) if session['s'] else []
    return {'open': open_items, 'resolved_this_shift': resolved_this_shift}


@app.post('/api/oos/{oos_id}/rts')
def oos_mark_rts(oos_id: int, payload: ManualOOSResolve, request: Request):
    """Any signed-in associate can mark a manual OOS record RTS, not just
    whoever logged it -- the actual fix might come from a different shift
    or a different technician entirely than whoever first logged it down."""
    _origin_check(request)
    session, shift = _report_access(request)
    profile = roster()[session['a']]
    with _db() as conn:
        record, error = v31_oos.mark_rts(conn, oos_id, rts_by=session['a'],
            rts_by_name=profile.get('name', 'Associate'), shift_id=session['s'],
            shift_code=shift['code'], now_iso=datetime.now(TZ).isoformat(),
            notes=payload.notes.strip())
        if error is None:
            # A newly RTS asset must appear in a regenerated passdown before
            # the shift can be ended.
            _invalidate_passdown(conn, session['s'])
    if error == 'not_found':
        raise HTTPException(404, 'Record not found')
    if error == 'already_resolved':
        raise HTTPException(409, 'Already marked RTS')
    return record
