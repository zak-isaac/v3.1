"""Real-browser regression test: the downloaded Outlook .eml must use V3's
HTML passdown template (bold section titles, bullet lists, LOTO line) and
must show spelling-corrected remark text, not the raw Cetaris typo -- i.e.
backend correction + V3-style EML template must work together, end to end,
exactly as a real MST would experience it.
"""
import csv
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import Workbook

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADERS = ["WO Number", "Asset #", "WO Status", "Primary RFR",
           "Service/Repair Description", "Technician", "Technician Remarks",
           "Start Date", "In Service Date / Time", "WO Closed Date/Time", "WO Date / Time",
           "WO Last Complete Date / Time", "SJ / PM Code", "Completed By"]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _workbook_bytes():
    # No frozen clock in this real-browser test -- use the real current time
    # so the WO lands inside today's actual shift-ownership window.
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    wb = Workbook()
    sheet = wb.active
    sheet.append(HEADERS)
    sheet.append(["9501", "ASSET9501", "Closed", "Proactive Repair",
                  "Conveyer adressed", "Browser Check Tech", "Repalced the intstalled servor",
                  now, now, "", "", "", "", ""])
    output = BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


@pytest.fixture
def live_server(tmp_path):
    roster_csv = tmp_path / "roster.csv"
    with roster_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Assoc. #", "Shift", "Job Title"])
        w.writerow(["Browser Check Tech", "555099", "S1", "MST"])

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


def test_eml_download_uses_v3_html_template_with_corrected_spelling(live_server):
    base_url, _ = live_server
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:
        pytest.skip(f"Chromium not installed/usable: {exc}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base_url)
        login = page.evaluate(
            """async () => {
                const r = await fetch('/api/login', {method:'POST', credentials:'same-origin',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({associate:'555099', intent:'direct'})});
                return {status: r.status};
            }"""
        )
        assert login["status"] == 200, login

        page.goto(base_url + "/shift-report.html")
        page.wait_for_function("document.readyState === 'complete'", timeout=10000)

        import base64
        file_b64 = base64.b64encode(_workbook_bytes()).decode("ascii")
        staged = page.evaluate(
            """async (b64) => {
                const binary = atob(b64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                const form = new FormData();
                form.append('file', new Blob([bytes]), 'cetaris.xlsx');
                const r = await fetch('/api/reporting/stage', {method:'POST', credentials:'same-origin', body: form});
                return {status: r.status};
            }""",
            file_b64,
        )
        assert staged["status"] == 200, staged

        imported = page.evaluate(
            """async () => {
                const r = await fetch('/api/reporting/import', {method:'POST', credentials:'same-origin',
                    headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({technicians:['Browser Check Tech']})});
                return {status: r.status, body: await r.json().catch(()=>({}))};
            }"""
        )
        assert imported["status"] == 200 and imported["body"]["summary"]["added"] == 1, imported

        refreshed = page.evaluate(
            """async () => {
                try { await window.V31Reporting?.refresh?.(); return {ok: true}; }
                catch (e) { return {ok: false, error: String(e)}; }
            }"""
        )
        assert refreshed["ok"], refreshed

        with page.expect_download() as download_info:
            page.locator("#generate-eml").click()
        content = download_info.value.path().read_text(encoding="utf-8")
        browser.close()

    assert "Content-Type: text/html" in content
    assert "End of Shift Passdown" in content
    assert "Shift Happenings" in content
    assert "Reliable Operations" in content
    assert "Needing to be Completed" in content
    assert "Returned to Service (RTS)" in content
    assert "Current Out of Service" in content

    # Spelling correction must already be applied by the time it reaches the
    # EML -- the same corrected text the MST reviewed on-screen, not the raw
    # Cetaris typo, and technical terms/asset IDs must still be intact.
    assert "Replaced the installed servo" in content
    assert "Repalced the intstalled servor" not in content
    assert "Conveyer addressed" in content
    assert "ASSET9501" in content
