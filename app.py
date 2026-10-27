"""Isolated V3.1 login and shift-context service. No Workshop integration.

Development convenience: associate-number-only access is NOT authentication.
Never expose this application on the facility network or Internet.
"""
from __future__ import annotations

import base64
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

ROOT = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get('IND3_V31_PRIVATE_DIR', str(ROOT / '.local-data'))).resolve()
SESSION_SECRET_FILE = PRIVATE / 'session_secret'
DB_FILE = PRIVATE / 'shifts.sqlite3'
TZ = ZoneInfo('America/Indiana/Indianapolis')
SESSION_TTL = 18 * 3600
WINDOW_GRACE = timedelta(hours=1)
SESSION_COOKIE = 'ind3_v31_session'
# Local-only development IDs, NOT secure identity verification.
DEV_ASSOCIATES = {
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

app = FastAPI(title='IND3 V3.1', docs_url=None, redoc_url=None, openapi_url=None)
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
    """Local-only preview identifiers. NOT production authentication."""
    return DEV_ASSOCIATES


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
    conn = sqlite3.connect(DB_FILE, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL, shift_day TEXT NOT NULL,
        owner TEXT NOT NULL, open INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, UNIQUE(code, shift_day)
    )''')
    conn.commit()
    return conn


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
            **window_info(row['code'], d)}


def allowed(profile: dict, code: str) -> bool:
    return bool(profile.get('all_shifts')) or profile.get('shift') == code


def shift_choices(profile: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    """Live and ready reports are bounded by the shift's actual time window.
    Do not delete historical records or automatically finalize a past report."""
    with _db() as conn:
        records = conn.execute('SELECT * FROM shifts ORDER BY id DESC').fetchall()
    by_key = {(r['code'], r['shift_day']): r for r in records}
    open_items, ready_items = [], []
    for code in SHIFT_RULES:
        if not allowed(profile, code):
            continue
        day = eligible_date(code, now)
        if day is None:
            continue
        key = (code, day.isoformat())
        record = by_key.get(key)
        if record is not None:
            if record['open']:
                open_items.append({**entry(record), 'action': 'resume'})
        else:
            ready_items.append({'id': None, 'code': code, 'date': day.isoformat(),
                                'label': SHIFT_RULES[code][0], 'open': False,
                                'action': 'start', **window_info(code, day)})
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
    opened, ready = shift_choices(profile, now)
    available = opened + ready
    selected = next((x for x in opened if x['id'] == session['s']), None)
    return {'name': profile.get('name', 'Associate'), 'associate': session['a'],
            'all_shifts': bool(profile.get('all_shifts')),
            'assigned_shift': profile.get('shift'), 'selected': selected,
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
    html = html[:start] + '<script defer src="/web/auth-ui.js"></script>' + html[end + len('</script>'):]
    return HTMLResponse(html)


@app.get('/shift-report.html', response_class=HTMLResponse)
def landing(request: Request):
    session, profile = who(request)
    if not state(session, profile)['selected']:
        return RedirectResponse('/', status_code=303)
    html = (ROOT / 'prototypes' / 'shift-report.html').read_text(encoding='utf-8')
    return HTMLResponse(html.replace('</body>', '<script defer src="/web/workspace-auth.js"></script></body>'))


class LoginRequest(BaseModel):
    associate: str = Field(pattern=r'^\d{1,12}$', max_length=12)


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
    now = datetime.now(TZ)
    opened, ready = shift_choices(profile, now)
    # Bind automatically ONLY when the choice is unambiguous.
    if len(opened) + len(ready) == 1:
        if opened:
            _set_cookie(response, payload.associate, opened[0]['id'])
        else:
            with _db() as conn:
                code, day = ready[0]['code'], ready[0]['date']
                conn.execute('INSERT OR IGNORE INTO shifts (code,shift_day,owner,created_at) VALUES (?,?,?,?)',
                             (code, day, payload.associate, now.isoformat()))
                row = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                                   (code, day)).fetchone()
                if not row['open']:
                    raise HTTPException(409, 'Shift was already closed')
                _set_cookie(response, payload.associate, row['id'])
        return {'destination': '/shift-report.html'}
    _set_cookie(response, payload.associate)
    return {'destination': '/', 'selection_required': True}


@app.get('/api/me')
def me(request: Request):
    session, profile = who(request)
    return state(session, profile)


@app.post('/api/shifts/start')
def start_shift(payload: ShiftChoice, request: Request, response: Response):
    _origin_check(request)
    session, profile = who(request)
    if not allowed(profile, payload.code):
        raise HTTPException(403, 'Shift not assigned to this associate')
    day = eligible_date(payload.code, datetime.now(TZ))
    if day is None:
        raise HTTPException(409, 'Shift reporting window is not active')
    with _db() as conn:
        conn.execute('INSERT OR IGNORE INTO shifts (code,shift_day,owner,created_at) VALUES (?,?,?,?)',
                     (payload.code, day.isoformat(), session['a'], datetime.now(TZ).isoformat()))
        row = conn.execute('SELECT * FROM shifts WHERE code=? AND shift_day=?',
                           (payload.code, day.isoformat())).fetchone()
        if not row['open']:
            raise HTTPException(409, 'This shift was already ended')
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
    if not allowed(profile, row['code']):
        raise HTTPException(403, 'Shift not assigned to this associate')
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
        if row is None or not allowed(profile, row['code']):
            raise HTTPException(404, 'Active shift not found')
        conn.execute('UPDATE shifts SET open=0 WHERE id=?', (session['s'],))
    _set_cookie(response, session['a'], 0)
    return {'ended': True, 'shift_id': session['s']}
