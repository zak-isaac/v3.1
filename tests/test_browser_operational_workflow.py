"""Chromium smoke-test of the full V3.1 operational workspace.

Tests real clicks and network APIs: associate login, manual proactive work,
separate PM report upload, Breakdown OOS hold, canonical Outlook EML,
readiness review, and completed End Shift.
"""
import csv
import sqlite3

import pytest
pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from test_browser_end_shift import live_server


def test_real_browser_manual_pm_breakdown_passdown_and_end_shift(live_server):
    base_url,tmp_path=live_server
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:
        pytest.skip(f"Chromium unavailable: {exc}")

    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(accept_downloads=True)
        page.on('dialog',lambda d:d.accept())
        page.goto(base_url)
        signin=page.evaluate("""async () => {
            const r=await fetch('/api/login',{method:'POST',
              credentials:'same-origin',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({associate:'555099',intent:'direct'})});
            return {status:r.status,data:await r.json()};
        }""")
        assert signin['status']==200,signin
        page.goto(base_url+'/shift-report.html')
        page.wait_for_function("!!window.V31Reporting && !!window.__v31ShiftContext",timeout=12000)

        # This manually discovered proactive repair has no Cetaris WO.
        page.locator('#v31-add-manual').click()
        page.locator('#m-asset').fill('VOIDFILL-UI')
        page.locator('#m-cause').fill('Loose terminal discovered during PM inspection')
        page.locator('#m-remarks').fill('Connection tightened; function verified')
        page.locator('#m-status').select_option('RTS')
        # The real modal must use an actual valid form; do not assume that
        # clicking a detached footer button always dispatches a request.
        page.locator('#v31-manual-form').wait_for(timeout=5000)
        assert page.locator('#v31-manual-form').evaluate(
            "(form) => form.checkValidity()"), "Manual work form did not validate"
        script_errors=[]
        page.on('pageerror',lambda exc:script_errors.append(str(exc)))
        try:
            with page.expect_response(
                lambda r:'/api/reporting/manual' in r.url,timeout=12000
            ) as changed:
                page.get_by_role('button',name='Save Manual Happening').click()
        except PlaywrightTimeoutError as exc:
            diag=page.evaluate("""() => ({
              modalVisible:!document.querySelector('#modal-root')?.hidden,
              valid:document.querySelector('#v31-manual-form')?.checkValidity(),
              asset:document.querySelector('#m-asset')?.value,
              toast:document.querySelector('#toast')?.textContent,
              saveButton:document.querySelector('#modal-actions')?.textContent
            })""")
            raise AssertionError(
                f'Manual Save request never reached server: {diag}, '
                f'JavaScript exceptions: {script_errors}'
            ) from exc
        assert changed.value.status==200
        # Playwright expect_response fires when response headers arrive; wait
        # for the subsequent reporting read to see the committed entry.
        # This is NOT a blind sleep or a retry of Save.
        page.wait_for_function("""async () => {
            const response=await fetch('/api/reporting/state',
                {credentials:'same-origin'});
            if(!response.ok)return false;
            const state=await response.json();
            return (state.rows||[]).some(
                row=>row.asset==='VOIDFILL-UI' && row.is_manual);
        }""",timeout=12000)
        state=page.evaluate("""async () => (await (await fetch(
            '/api/reporting/state',{credentials:'same-origin'})).json())""")
        assert any(row['asset']=='VOIDFILL-UI' and row['is_manual'] for row in state['rows'])
        # Navigate the actual Analytics page and require the manually entered
        # asset to appear from the backend, never hardcoded demo figures.
        page.locator('[data-nav="analytics"]').click()
        page.locator('#v31-equipment-heading').wait_for(timeout=10000)
        page.locator('#v31-analytics-assets').get_by_text('VOIDFILL-UI').wait_for(timeout=10000)
        assert page.locator('#v31-analytics-kpis .v31-analytics-kpi').count()==5
        page.locator('[data-nav="report"]').click()

        # PM needing completion: an actual file, not just a preview filename.
        pm_csv=chr(10).join(['WO Number,PM Due Date,Asset #',
                             'PMUI001,11/05/2026,BALER-UI','']).encode('utf-8')
        with page.expect_response(lambda r:'/api/pm-needing-completion/upload' in r.url) as uploaded:
            page.locator('#pending-picker').set_input_files({
              'name':'pm-needing-ui.csv','mimeType':'text/csv','buffer':pm_csv
            })
        assert uploaded.value.status==200
        page.locator('[data-tab="pm"]').click()
        page.get_by_text('PMUI001',exact=False).wait_for(timeout=10000)

        # The V3 Breakdown lifecycle must actually load and create an OOS hold.
        page.locator('[data-tab="breakdown"]').click()
        page.locator('#v31-breakdown-lifecycle').get_by_text(
            'Cetaris Breakdown Lifecycle').wait_for(timeout=10000)
        page.locator('#v31-breakdown-lifecycle input[placeholder="Asset ID to hold OOS"]').fill(
            'CONV-BROWSER')
        with page.expect_response(lambda r:'/api/breakdowns/hold' in r.url) as held:
            page.locator('#v31-breakdown-lifecycle').get_by_role(
                'button',name='Keep Asset OOS').click()
        assert held.value.status==200
        page.locator('#v31-breakdown-lifecycle').get_by_text('CONV-BROWSER').wait_for(timeout=10000)

        # Shift Progress must reach 100% (LOTO verified, no pending review
        # items) before Generate Passdown will proceed -- exercise the real
        # LOTO toggle checkbox, not a backdoor API call.
        page.locator('label.switch .switch-track').click()
        assert page.locator('#loto-toggle').is_checked(), "LOTO toggle click did not check the box"
        page.evaluate("async () => { await window.V31Reporting?.flushDraft?.(); "
                       "await window.V31Reporting?.refresh?.(); }")

        # Server creates the exact saved Outlook message, including manual OOS.
        with page.expect_download() as dl:
            page.locator('#generate-eml').click()
        eml=dl.value.path().read_text(encoding='utf-8')
        assert 'VOIDFILL-UI' in eml
        assert 'PMUI001' in eml
        assert 'CONV-BROWSER' in eml
        assert 'Manual OOS hold' in eml

        # A repeat preview cannot invalidate an unchanged saved EML. The
        # Preview Report button was removed from the UI, but the underlying
        # server render must still be side-effect-free -- hit it directly.
        preview=page.evaluate("""async () => (await (await fetch(
          '/api/shifts/report/preview',{credentials:'same-origin'})).json())""")
        assert 'VOIDFILL-UI' in preview['html']
        review=page.evaluate("""async () => (await (await fetch(
          '/api/shifts/end/readiness',{credentials:'same-origin'})).json())""")
        assert review['ready'] is True,review
        assert review['warnings']

        page.locator('#end-shift').click()
        page.locator('#v31-end-ack').wait_for(timeout=10000)
        page.locator('#v31-end-ack').check()
        with page.expect_response(lambda r:r.url.endswith('/api/shifts/end')) as ended:
            page.locator('#modal-actions .btn-end').click()
        assert ended.value.status==200
        browser.close()

    with sqlite3.connect(tmp_path/'shifts.sqlite3') as db:
        closed=db.execute("SELECT open FROM shifts WHERE owner='555099' ORDER BY id DESC LIMIT 1").fetchone()
        assert closed and closed[0]==0
        reviewed=db.execute("SELECT warnings_json FROM shift_end_review ORDER BY shift_id DESC LIMIT 1").fetchone()
        assert reviewed and 'out of service' in reviewed[0]
