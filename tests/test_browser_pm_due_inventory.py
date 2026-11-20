"""Chromium acceptance: unassigned To-Be-Done PM appears without technician import."""
from io import BytesIO
from datetime import date
import pytest
from openpyxl import Workbook

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright
from test_browser_end_shift import live_server


def _workbook(due):
    wb=Workbook()
    ws=wb.active
    headers=["WO Number","Asset #","WO Status","Primary RFR",
             "Service/Repair Description","Technician","Technician Remarks",
             "Start Date","PM Due Date"]
    ws.append(headers)
    ws.append(["PMAUTO-T","CONV-MISSING","To be Done","08",
               "Scheduled daily PM",None,None,None,due])
    ws.append(["PMAUTO-D","CONV-DONE","Completed","08",
               "Completed daily PM",None,None,None,due])
    ws.append(["NOT-A-PM","OTHER","To be Done","11",
               "Proactive repair",None,None,None,due])
    buf=BytesIO()
    wb.save(buf);wb.close()
    return buf.getvalue()


def test_wo_details_to_be_done_pm_auto_flags_on_real_pm_tab(live_server):
    base_url,_tmp=live_server
    with sync_playwright() as p:
        try:
            browser=p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        page=browser.new_page()
        errors=[]
        page.on("pageerror",lambda exc:errors.append(str(exc)))
        try:
            page.goto(base_url)
            signin=page.evaluate("""async () => {
                const response=await fetch('/api/login',{
                   method:'POST',credentials:'same-origin',
                   headers:{'Content-Type':'application/json'},
                   body:JSON.stringify({associate:'555099',intent:'direct'})});
                return response.status;
            }""")
            assert signin==200
            page.goto(base_url+"/shift-report.html")
            page.wait_for_function("!!window.V31Reporting && !!window.__v31ShiftContext",
                                   timeout=12000)
            dates=page.evaluate("""async () => (await (await fetch(
                '/api/pm-needing-completion',{credentials:'same-origin'})).json()).target_dates""")
            assert dates
            due=date.fromisoformat(dates[0]).strftime("%m/%d/%Y")
            with page.expect_response(
                lambda r:r.url.endswith('/api/reporting/stage') and
                r.request.method=='POST',timeout=12000
            ) as staged:
                page.locator("#cetaris-picker").set_input_files({
                    "name":"wo-details-due.xlsx",
                    "mimeType":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "buffer":_workbook(due),
                })
            assert staged.value.status==200,staged.value.text()
            page.locator('[data-tab="pm"]').click()
            page.get_by_text("PMAUTO-T").wait_for(timeout=12000)
            # WO Details alone can update the due-PM summary; the user
            # must not have to reload or upload a separate PM file.
            page.wait_for_function("""() => document.querySelector(
                '#pending-filename')?.textContent?.includes('2 PMs due')""",
                timeout=12000)
            panel=page.locator("#tab-content").inner_text()
            assert "PMAUTO-D" in panel
            assert "NOT-A-PM" not in panel
            assert "DUE TODAY — 11:59 PM" in panel
            assert "Completed" in panel
            assert "Daily PMs Due · 2" in panel
            assert "Still needing completion: 1" in panel
            state=page.evaluate("""async () => (await (await fetch(
                '/api/pm-needing-completion')).json())""")
            assert state["due_count"]==2
            assert state["count"]==1
            assert not state["present"] and state["auto_available"]
            assert errors==[],errors
        finally:
            browser.close()
