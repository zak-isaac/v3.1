"""Real-browser regression tests for Phase 1 report consistency:

- Shift Happenings shows corrected remarks.
- The original Cetaris remark is preserved and recoverable.
- A manual MST edit is respected -- a reimport must never discard it.
- Preview Report and the downloaded Outlook EML show the same underlying
  facts (same WO, same corrected/edited text), not independently-drifted
  copies.
- HTML escaping and Unicode content survive intact into the EML.
"""
import base64
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


def _workbook_bytes(wo, remark, cause="Fault reported"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    wb = Workbook()
    sheet = wb.active
    sheet.append(HEADERS)
    sheet.append([wo, "ASSET" + wo, "Closed", "Proactive Repair", cause,
                  "Browser Check Tech", remark, now, now, "", "", "", "", ""])
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


def _stage_and_import(page, base_url, wo, remark, cause, techs=("Browser Check Tech",)):
    b64 = base64.b64encode(_workbook_bytes(wo, remark, cause)).decode("ascii")
    staged = page.evaluate(
        """async (b64) => {
            const binary = atob(b64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            const form = new FormData();
            form.append('file', new Blob([bytes]), 'cetaris.xlsx');
            const r = await fetch('/api/reporting/stage', {method:'POST', credentials:'same-origin', body: form});
            return {status: r.status, body: await r.json().catch(()=>({}))};
        }""",
        b64,
    )
    assert staged["status"] == 200, staged
    imported = page.evaluate(
        """async (techs) => {
            const r = await fetch('/api/reporting/import', {method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({technicians: techs})});
            return {status: r.status, body: await r.json().catch(()=>({}))};
        }""",
        list(techs),
    )
    assert imported["status"] == 200, imported
    page.evaluate("async () => { await window.V31Reporting?.refresh?.(); }")
    return imported


@pytest.fixture
def page_ctx(live_server):
    base_url, tmp_path = live_server
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
        yield page, base_url, tmp_path
        browser.close()


def test_manual_edit_survives_reimport_and_matches_in_preview_and_eml(page_ctx):
    page, base_url, _ = page_ctx

    # 1. Original Cetaris import with a typo.
    _stage_and_import(page, base_url, "9601", "Repalced the intstalled servor", "Conveyer adressed")
    state1 = page.evaluate(
        "async () => (await (await fetch('/api/reporting/state',{credentials:'same-origin'})).json())"
    )
    row1 = next(r for r in state1["rows"] if r["wo_number"] == "9601")
    assert row1["remarks"] == "Replaced the installed servo"  # corrected for display
    assert row1["remarks_original"] == "Repalced the intstalled servor"  # original preserved

    # 2. MST manually edits the remark (approved final wording).
    edited = page.evaluate(
        """async () => {
            const r = await fetch('/api/reporting/work-orders/9601', {method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({remarks: 'MST confirmed: unit recieved full service.'})});
            return {status: r.status};
        }"""
    )
    assert edited["status"] == 200, edited

    # 3. Reimport the SAME WO with different raw Cetaris text -- the manual
    # edit must survive untouched (existing edited-flag guard).
    _stage_and_import(page, base_url, "9601", "Completely different raw text", "Different cause")
    state2 = page.evaluate(
        "async () => (await (await fetch('/api/reporting/state',{credentials:'same-origin'})).json())"
    )
    row2 = next(r for r in state2["rows"] if r["wo_number"] == "9601")
    assert row2["remarks"] == "MST confirmed: unit received full service.", \
        "Manual edit must survive reimport, corrected for display (recieved->received)"
    assert "Completely different raw text" not in row2["remarks"]

    # 4. Preview Report and the downloaded EML must show the SAME final
    # (edited + corrected) text -- not independently-derived copies. Drive
    # this through the real user flow: open Preview, read it, then use the
    # modal's own "Generate Outlook EML" button.
    page.locator("#preview-report").click()
    page.locator('#v31-server-report-preview').wait_for(timeout=10000)
    preview_text = page.evaluate("document.querySelector('#v31-server-report-preview')?.srcdoc")
    assert "MST confirmed: unit received full service." in preview_text
    assert "9601" in preview_text

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Generate Outlook EML").click()
    eml_content = download_info.value.path().read_text(encoding="utf-8")
    assert "MST confirmed: unit received full service." in eml_content
    assert "9601" in eml_content
    assert "recieved" not in eml_content


def test_html_special_characters_and_unicode_survive_into_the_eml(page_ctx):
    page, base_url, _ = page_ctx
    remark = "Checked <script>alert(1)</script> & \"quoted\" 'notes' — café résumé 5° OK"
    _stage_and_import(page, base_url, "9701", remark, "Inspection")
    page.wait_for_timeout(200)

    with page.expect_download() as download_info:
        page.locator("#generate-eml").click()
    content = download_info.value.path().read_text(encoding="utf-8")

    # Never an unescaped, potentially-executable tag in the output.
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content
    # Ampersand/quotes must be entity-escaped, not raw.
    assert "&amp;" in content
    assert "&quot;" in content or "&#39;" in content
    # Non-ASCII content must survive UTF-8 round-trip intact, not mangled.
    assert "café" in content
    assert "résumé" in content
    assert "—" in content
    assert "5°" in content


def test_pm_needing_completion_section_never_invents_unuploaded_rows(page_ctx):
    page, base_url, _ = page_ctx
    _stage_and_import(page, base_url, "9801", "Routine check", "PM support")
    page.wait_for_timeout(200)

    with page.expect_download() as download_info:
        page.locator("#generate-eml").click()
    content = download_info.value.path().read_text(encoding="utf-8")

    assert "PMs Needing Completion" in content
    assert "No PMs Needing Completion report uploaded" in content
    assert "No PMs pending this shift." not in content
