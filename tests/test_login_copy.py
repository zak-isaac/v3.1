"""Public-facing V3.1 entry copy should stay concise without touching login logic."""
from pathlib import Path

PAGE=Path(__file__).resolve().parents[1]/"prototypes"/"login-flow.html"
JS=Path(__file__).resolve().parents[1]/"web"/"auth-ui.js"

def page_section(markup, start, end):
    return markup.split(f'id="screen-{start}"',1)[1].split(f'id="screen-{end}"',1)[0]

def test_first_login_uses_one_facility_name_and_one_purpose_line():
    html=PAGE.read_text(encoding="utf-8")
    screen=page_section(html,"login","active")
    assert screen.count("IND3")==1
    assert "MST Reliability<br>Operations" in screen
    assert "Shift Passdown V3.1" in screen
    assert "Welcome back" in screen
    assert 'id="login-title"' in screen
    assert 'id="login-form"' in screen
    assert 'id="associate-input"' in screen
    assert "Your assigned shift loads automatically." in screen
    assert "Sign in to IND3" not in screen
    assert "v1.0.0" not in screen
    assert "System Online" not in screen
    assert "Shift Reporting &amp; Passdown" not in screen
    assert "McCordsville, Indiana" in screen
    assert "Need access?" in screen
    assert "Sign In" in screen

def test_active_and_handoff_keep_their_real_navigation_targets():
    html=PAGE.read_text(encoding="utf-8")
    active=page_section(html,"active","handoff")
    handoff=html.split('id="screen-handoff"',1)[1].split('id="preview-dock"',1)[0]
    assert "IND3 – MST Reliability Operations" not in active
    assert "V3.1 · Local Test" in active
    assert "id=\"resume-active\"" in active
    assert "id=\"different-associate\"" in active
    assert "id=\"resume-previous\"" in handoff
    assert "id=\"start-new\"" in handoff
    assert "id=\"handoff-different\"" in handoff
    assert "Frontend design preview" not in handoff
    assert handoff.count("IND3")==1

def test_dynamic_login_javascript_stays_wired():
    js=JS.read_text(encoding="utf-8")
    assert "document.querySelector" in js
    assert "login-form" in js
    assert "resume-active" in js
    assert "handoff-title" in js
    assert "v31-manager-shift-picker" in js
    assert "Shift report session" not in js
