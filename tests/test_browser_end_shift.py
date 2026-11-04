"""Real-browser regression test confirming #end-shift really closes the
shift server-side.

IMPORTANT CONTEXT (discovered while writing this test): web/workspace-auth.js
registers a CAPTURE-phase listener on #end-shift with
stopImmediatePropagation(), which intercepts every real click BEFORE the
inline shift-report.html prototype's own confirmEnd() (bubble-phase) ever
runs. workspace-auth.js's handler is the one that actually calls
/api/shifts/end, via a native confirm() dialog -- this was already
correctly wired in the real served app. The inline confirmEnd() is
unreachable/dead code in a real browser; it only ever ran in a narrow
startup race before workspace-auth.js's deferred script finished loading.

This test exists because TestClient-only tests can never catch this kind of
JS event-listener wiring issue -- it never loads or executes frontend code
at all. It pins down the ACTUAL real-browser behavior with a real server.
"""
import csv
import os
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from browser_shift_support import shift_for_browser, local_work_time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(tmp_path):
    roster_csv = tmp_path / "roster.csv"
    with roster_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Assoc. #", "Shift", "Job Title"])
        w.writerow(["Browser Check Tech", "555099", shift_for_browser(), "MST"])

    env = os.environ.copy()
    env["IND3_V31_PRIVATE_DIR"] = str(tmp_path)
    env["IND3_V31_ROSTER_PATH"] = str(roster_csv)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        for _ in range(60):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.25)
        else:
            raise RuntimeError("Server did not come up in time")
        yield base_url, tmp_path
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


def test_end_shift_button_really_closes_the_shift_server_side(live_server):
    base_url, tmp_path = live_server
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:
        pytest.skip(f"Chromium not installed/usable: {exc}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(base_url)
        login = page.evaluate(
            """async () => {
                const r = await fetch('/api/login', {method:'POST', credentials:'same-origin',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({associate:'555099', intent:'direct'})});
                return {status: r.status, body: await r.json()};
            }"""
        )
        assert login["status"] == 200, login

        page.goto(base_url + "/shift-report.html")
        page.wait_for_function("document.readyState === 'complete'", timeout=10000)

        saved = page.evaluate(
            """async () => {
                const r = await fetch('/api/shifts/report', {method:'POST', credentials:'same-origin',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({content:'IND3 S1 SHIFT HAPPENINGS: Browser-check passdown.'})});
                return {status: r.status};
            }"""
        )
        assert saved["status"] == 200, saved

        page.locator("#end-shift").click()
        page.locator("#modal-actions .btn-end").wait_for(timeout=10000)
        with page.expect_response(lambda r: "/api/shifts/end" in r.url) as response_info:
            page.locator("#modal-actions .btn-end").click()
        response = response_info.value
        assert response.status == 200, response.status

        page.wait_for_url(lambda url: url == base_url + "/" or url == base_url, timeout=5000)
        browser.close()

    db_path = tmp_path / "shifts.sqlite3"
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT open, closed_at FROM shifts WHERE owner='555099' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0, "Shift is still marked OPEN -- End Shift did not really close it"
    assert row[1], "closed_at was never recorded"
