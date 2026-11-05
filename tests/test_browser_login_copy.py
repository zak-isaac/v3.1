"""Real Chromium check of the uncluttered first V3.1 login screen."""
import json
import urllib.request

import pytest
pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright
from test_browser_end_shift import live_server

@pytest.mark.parametrize("viewport",[
    {"width":1440,"height":900},
    {"width":390,"height":844},
])
def test_login_copy_is_clear_without_repeat_facility_labels(live_server, viewport):
    base_url,_=live_server
    try:
        with sync_playwright() as engine:
            engine.chromium.launch().close()
    except Exception as exc:
        pytest.skip(f"Chromium unavailable: {exc}")

    # The app legitimately shows 'handoff' instead of 'login' first whenever
    # the real wall clock falls in a shift-overlap window (e.g. S1 closing
    # grace overlapping S2's opening grace) -- this is correct, documented
    # behavior, not a bug. This test is specifically about login-screen
    # copy, so skip rather than false-fail when login isn't the first
    # screen reachable right now. Same real-clock-aware philosophy as
    # browser_shift_support.py's shift_for_browser().
    entry = json.loads(urllib.request.urlopen(base_url + "/api/entry").read())
    if entry["screen"] != "login":
        pytest.skip(f"Real wall clock is currently in a shift-overlap window "
                    f"(screen={entry['screen']!r}) -- login isn't the first screen "
                    f"shown right now; rerun when only one shift is in its window.")

    with sync_playwright() as engine:
        browser=engine.chromium.launch()
        page=browser.new_page(viewport=viewport)
        page.goto(base_url,wait_until="domcontentloaded")
        page.locator("#screen-login").wait_for(state="visible",timeout=12000)
        screen=page.locator("#screen-login")
        assert screen.get_by_role("heading",name="Welcome back").is_visible()
        assert screen.get_by_role("textbox",name="Associate Number").is_visible()
        assert screen.get_by_role("button",name="Sign In").is_visible()
        assert screen.locator(".intro-kicker").inner_text().strip()=="IND3"
        assert screen.get_by_text("MST Reliability",exact=False).count()>=1
        assert screen.locator(".page-footer").get_by_text("McCordsville, Indiana").is_visible()
        assert screen.get_by_text("Sign in to IND3").count()==0
        assert page.locator("#screen-active").is_hidden()
        assert page.locator("#screen-handoff").is_hidden()
        browser.close()
