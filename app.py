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

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import v31_roster

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
    '2759': {'name': 'MST Management', 'all_shifts': True, 'shift': None},
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
    expire_shifts()
    task = asyncio.create_task(_periodic_maintenance())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
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
                v31_roster.sync(PRIVATE, DB_FILE, set(MANAGEMENT_ASSOCIATES), force=True)
        except Exception:
            # Do not terminate reporting due to temporary unreadable workbook.
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
        'is_ot': 'INTEGER NOT NULL DEFAULT 0',
        'closed_at': "TEXT NOT NULL DEFAULT ''",
        'closed_reason': "TEXT NOT NULL DEFAULT ''",
        'report_saved_at': "TEXT NOT NULL DEFAULT ''",
        'report_content': "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE shifts ADD COLUMN {name} {declaration}")
    conn.execute('''CREATE TABLE IF NOT EXISTS overtime_grants (
        associate TEXT NOT NULL, code TEXT NOT NULL, shift_day TEXT NOT NULL,
        granted_by TEXT NOT NULL, granted_at TEXT NOT NULL,
        PRIMARY KEY(associate,code,shift_day)
    )''')
    conn.commit()
    return conn


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
            'is_ot': bool(row['is_ot']), 'report_saved': bool(row['report_saved_at']),
            **window_info(row['code'], d)}


def overtime_granted(assoc: str, code: str, day: date) -> bool:
    with _db() as conn:
        return conn.execute('''SELECT 1 FROM overtime_grants
                              WHERE associate=? AND code=? AND shift_day=?''',
                            (assoc, code, day.isoformat())).fetchone() is not None


def allowed(profile: dict, code: str, day: date | None = None, assoc: str | None = None) -> bool:
    if profile.get('all_shifts') or profile.get('shift') == code:
        return True
    return bool(day and assoc and overtime_granted(assoc, code, day))


def shift_choices(profile: dict, now: datetime, associate: str | None = None) -> tuple[list[dict], list[dict]]:
    """Live and ready reports are bounded by the shift's actual time window.
    Do not delete historical records or automatically finalize a past report."""
    expire_shifts(now)
    with _db() as conn:
        records = conn.execute('SELECT * FROM shifts ORDER BY id DESC').fetchall()
    by_key = {(r['code'], r['shift_day']): r for r in records}
    open_items, ready_items = [], []
    has_capacity = sum(bool(r['open']) for r in records) < 2
    for code in SHIFT_RULES:
        day = eligible_date(code, now)
        if day is None or not allowed(profile, code, day, associate):
            continue
        key = (code, day.isoformat())
        record = by_key.get(key)
        if record is not None:
            if record['open'] and (profile.get('all_shifts') or record['owner'] == associate):
                open_items.append({**entry(record), 'action': 'resume'})
        else:
            # A new shift starts before its scheduled end. The extra hour
            # after closing belongs only to an already-open passdown.
            _, scheduled_end = shift_times(code, day)
            if now <= scheduled_end and has_capacity:
                ready_items.append({'id': None, 'code': code, 'date': day.isoformat(),
                                    'label': SHIFT_RULES[code][0], 'open': False,
                                    'action': 'start', 'is_ot': not profile.get('all_shifts') and profile.get('shift') != code,
                                    **window_info(code, day)})
    open_items.sort(key=lambda x: x['scheduled_start'])
    ready_items.sort(key=lambda x: x['scheduled_start'])
    return open_items, ready_items


def public_flow(now: datetime | None = None) -> dict:
    """Local-only shift context for the approved signed-out screen designs."""
    moment = now or datetime.now(TZ)
    # Never expose an associate number or a shift owner's identity here.
    opened, ready = shift_choices({'all_shifts': True}, moment)
    screen = 'handoff' if opened and ready else ('active' if opened else 'login')
    return {'screen': screen, 'open': opened, 'ready': ready, 'now': moment.isoformat(),
            'grace_minutes': 60}


def state(session: dict, profile: dict) -> dict:
    now = datetime.now(TZ)
    opened, ready = shift_choices(profile, now, session['a'])
    available = opened + ready
    selected = next((x for x in opened if x['id'] == session['s']), None)
    catalog = []
    for code in SHIFT_RULES:
        match = next((x for x in available if x['code'] == code), None)
        catalog.append(match if match else {'code': code, 'label': SHIFT_RULES[code][0],
                                              'action': 'unavailable', 'open': False})
    return {'name': profile.get('name', 'Associate'), 'associate': session['a'],
            'role': 'manager' if profile.get('all_shifts') else 'mst',
            'catalog': catalog,
            'all_shifts': bool(profile.get('all_shifts')),
            'assigned_shift': profile.get('shift'), 'selected': selected,
            'report_saved': bool(selected and selected.get('report_saved')),
            'shifts': available, 'open': opened, 'ready': ready, 'now': now.isoformat(),
            'grace_minutes': 60, 'production_connected': False}


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
    html = html.replace('</head>', '<link rel="stylesheet" href="/web/login-layout.css"><style id="v31-preload">body{visibility:hidden}html{background:#fff}</style></head>', 1)
    return HTMLResponse(html)


@app.get('/shift-report.html', response_class=HTMLResponse)
def landing(request: Request):
    session, profile = who(request)
    if not state(session, profile)['selected']:
        return RedirectResponse('/', status_code=303)
    html = (ROOT / 'prototypes' / 'shift-report.html').read_text(encoding='utf-8')
    return HTMLResponse(html.replace('</body>',
        '<script defer src="/web/workspace-auth.js"></script>'+
        '<script defer src="/web/roster-admin.js"></script></body>'))


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
    # Original V3 policy: managers select; ordinary MSTs enter their own
    # roster-assigned shift directly. An MST never chooses another shift.
    if profile.get('all_shifts'):
        _set_cookie(response, payload.associate)
        return {'destination': '/', 'manager_selection_required': True,
                'role': 'manager'}
    if payload.intent == 'select':
        # Popup path: do not create/attach an MST shift before a choice is checked.
        _set_cookie(response, payload.associate)
        return {'destination': '/', 'selection_required': True, 'role': 'mst'}
    now = datetime.now(TZ)
    opened, ready = shift_choices(profile, now, payload.associate)
    # Direct MST sign-in must always prefer the Drax-assigned shift,
    # never a different shift merely because overtime was granted.
    opened = [x for x in opened if x['code'] == profile.get('shift')]
    ready = [x for x in ready if x['code'] == profile.get('shift')]
    if opened:
        # Assigned shift already opened by this MST: resume that report.
        selected = opened[0]
    elif ready:
        # Start assigned shift only if nobody else owns the current instance.
        target = ready[0]
        with _db() as conn:
            if conn.execute('SELECT COUNT(*) FROM shifts WHERE open=1').fetchone()[0] >= 2:
                raise HTTPException(409, 'Two overlapping shifts are already active')
            conn.execute('''INSERT OR IGNORE INTO shifts
                (code,shift_day,owner,created_at,is_ot) VALUES (?,?,?,?,?)''',
                (target['code'], target['date'], payload.associate, now.isoformat(),
                 int(bool(target.get('is_ot')))))
            row = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                               (target['code'], target['date'])).fetchone()
            if not row['open'] or row['owner'] != payload.associate:
                raise HTTPException(409, 'This shift is already open under another MST. Contact management.')
            selected = entry(row)
    else:
        code = profile.get('shift')
        raise HTTPException(409, f'{code} is outside its reporting window, or is open under another MST.')
    _set_cookie(response, payload.associate, selected['id'])
    return {'destination': '/shift-report.html', 'role': 'mst',
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
        raise HTTPException(403, 'Shift not assigned and no authorized overtime grant')
    with _db() as conn:
        existing = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                                (payload.code, day.isoformat())).fetchone()
        if existing is None and now > shift_times(payload.code, day)[1]:
            raise HTTPException(409, 'New shift may not start after its scheduled end')
        if existing is None and conn.execute('SELECT COUNT(*) FROM shifts WHERE open=1').fetchone()[0] >= 2:
            raise HTTPException(409, 'Two overlapping shifts are already active')
        conn.execute('''INSERT OR IGNORE INTO shifts
            (code,shift_day,owner,created_at,is_ot) VALUES (?,?,?,?,?)''',
            (payload.code, day.isoformat(), session['a'], now.isoformat(),
             int(not profile.get('all_shifts') and profile.get('shift') != payload.code)))
        row = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                           (payload.code, day.isoformat())).fetchone()
        if not row['open']:
            raise HTTPException(409, 'This shift was already ended')
        if not profile.get('all_shifts') and row['owner'] != session['a']:
            raise HTTPException(403, 'The shift belongs to another MST')
        selected = entry(row)
    _set_cookie(response, session['a'], selected['id'])
    return selected


@app.post('/api/shifts/resume')
def resume_shift(payload: ShiftResume, request: Request, response: Response):
    _origin_check(request)
    session, profile = who(request)
    with _db() as conn:
        row = conn.execute('SELECT * FROM shifts WHERE id=? AND open=1', (payload.shift_id,)).fetchone()
    if row is None or eligible_date(row['code'], datetime.now(TZ)) != date.fromisoformat(row['shift_day']):
        raise HTTPException(404, 'Active shift not found within its reporting window')
    if not allowed(profile, row['code'], date.fromisoformat(row['shift_day']), session['a']):
        raise HTTPException(403, 'Shift not assigned and no authorized overtime grant')
    if not profile.get('all_shifts') and row['owner'] != session['a']:
        raise HTTPException(403, 'Only the shift starter or management may resume this report')
    _set_cookie(response, session['a'], row['id'])
    return entry(row)


@app.post('/api/logout')
def logout(request: Request, response: Response):
    _origin_check(request)
    response.delete_cookie(SESSION_COOKIE, path='/')
    return {'signed_out': True}

@app.post('/api/shifts/end')
def end_shift(request: Request, response: Response):
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
        if not row['report_saved_at']:
            raise HTTPException(409, 'Generate and save the passdown report before ending this shift')
        conn.execute("""UPDATE shifts SET open=0, closed_at=?,closed_reason='completed'
                        WHERE id=?""",(datetime.now(TZ).isoformat(),session['s']))
    _set_cookie(response, session['a'], 0)
    return {'ended': True, 'shift_id': session['s']}


class ReportPayload(BaseModel):
    content: str = Field(min_length=30, max_length=90000)


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

class OvertimeGrant(BaseModel):
    associate: str = Field(pattern=r'^\d{1,12}$')
    code: Literal['S1', 'S2', 'S3', 'S4', 'S5']
    shift_day: date


def require_manager(request: Request) -> dict:
    session, profile = who(request)
    if not profile.get('all_shifts'):
        raise HTTPException(403, 'Management access required')
    return session


@app.get('/api/admin/roster')
def admin_roster(request: Request):
    require_manager(request)
    return v31_roster.status(DB_FILE, include_rows=True)


@app.post('/api/admin/roster/reload')
def admin_roster_reload(request: Request):
    _origin_check(request)
    require_manager(request)
    v31_roster.sync(PRIVATE, DB_FILE, set(MANAGEMENT_ASSOCIATES), force=True)
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

@app.get('/api/admin/overtime')
def admin_overtime(request: Request):
    require_manager(request)
    with _db() as conn:
        grants = conn.execute("""SELECT associate, code, shift_day, granted_by, granted_at
                                 FROM overtime_grants ORDER BY shift_day DESC,code""").fetchall()
    return {'grants': [dict(x) for x in grants]}


def _validate_grant(payload: OvertimeGrant) -> None:
    now = datetime.now(TZ).date()
    if not (now - timedelta(days=1) <= payload.shift_day <= now + timedelta(days=30)):
        raise HTTPException(422, 'Overtime date must be yesterday through 30 days ahead')
    if payload.shift_day.weekday() not in SHIFT_RULES[payload.code][3]:
        raise HTTPException(422, 'Selected shift is not scheduled on that date')


@app.post('/api/admin/overtime/grant')
def admin_overtime_grant(payload: OvertimeGrant, request: Request):
    _origin_check(request)
    session = require_manager(request)
    _validate_grant(payload)
    profile = roster().get(payload.associate)
    if not profile or profile.get('all_shifts'):
        raise HTTPException(404, 'MST associate not found in authorized roster')
    with _db() as conn:
        conn.execute("""INSERT INTO overtime_grants (associate,code,shift_day,granted_by,granted_at)
                        VALUES (?,?,?,?,?) ON CONFLICT(associate,code,shift_day)
                        DO UPDATE SET granted_by=excluded.granted_by,granted_at=excluded.granted_at""",
                     (payload.associate,payload.code,payload.shift_day.isoformat(),
                      session['a'],datetime.now(TZ).isoformat()))
    return {'granted': True, 'is_ot': profile['shift'] != payload.code}


@app.post('/api/admin/overtime/revoke')
def admin_overtime_revoke(payload: OvertimeGrant, request: Request):
    _origin_check(request)
    require_manager(request)
    with _db() as conn:
        conn.execute("DELETE FROM overtime_grants WHERE associate=? AND code=? AND shift_day=?",
                     (payload.associate,payload.code,payload.shift_day.isoformat()))
    return {'revoked': True}


@app.post('/api/shifts/report')
def save_shift_report(payload: ReportPayload, request: Request):
    """Store a locally generated passdown before allowing a normal End Shift."""
    _origin_check(request)
    session, profile = who(request)
    if not session['s']:
        raise HTTPException(409, 'Choose a shift first')
    now = datetime.now(TZ)
    with _db() as conn:
        row = conn.execute("SELECT * FROM shifts WHERE id=? AND open=1",(session['s'],)).fetchone()
        if row is None:
            raise HTTPException(404, 'No active report selected')
        day = date.fromisoformat(row['shift_day'])
        if eligible_date(row['code'],now) != day:
            raise HTTPException(409, 'Reporting window expired')
        if not allowed(profile,row['code'],day,session['a']):
            raise HTTPException(403, 'Shift access no longer authorized')
        if not profile.get('all_shifts') and row['owner'] != session['a']:
            raise HTTPException(403, 'Only the shift starter or management can save a report')
        body = payload.content.strip()
        if 'SHIFT HAPPENINGS' not in body or 'IND3' not in body:
            raise HTTPException(422, 'Passdown content is missing required sections')
        conn.execute("""UPDATE shifts SET report_content=?, report_saved_at=?
                        WHERE id=?""",(body,now.isoformat(),session['s']))
    return {'saved':True, 'shift_id':session['s'], 'saved_at':now.isoformat()}


@app.get('/api/shifts/report')
def read_shift_report(request: Request):
    session, profile = who(request)
    if not session['s']:
        raise HTTPException(409, 'Choose a shift')
    with _db() as conn:
        row=conn.execute("SELECT * FROM shifts WHERE id=?",(session['s'],)).fetchone()
    if row is None or not allowed(profile,row['code'],date.fromisoformat(row['shift_day']),session['a']):
        raise HTTPException(404,'Shift not found')
    if not profile.get('all_shifts') and row['owner'] != session['a']:
        raise HTTPException(403,'Report belongs to another MST')
    return {'content':row['report_content'], 'saved_at':row['report_saved_at'],
            'shift_id':row['id']}
