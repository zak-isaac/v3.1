"""Regression tests for the server-side closing-tag injection fix.

A served prototype page can legitimately contain the literal text of a
closing tag (e.g. '</body>') inside an inline script's own JS string -- the
client-composed HTML email template does exactly that. A naive
html.replace('</body>', ...) corrupted that string in place, breaking the
entire inline script silently (found 2026-10-29 while building the EML
template). _inject_before_last() targets only the LAST occurrence, which
is always the real structural tag in a well-formed document.
"""
import app as m
from test_reporting import clock, client, enter


def test_inject_before_last_targets_only_the_final_occurrence():
    html = "<p>contains a fake </body> inside some text</p>real content</body>"
    out = m._inject_before_last(html, "</body>", "<INSERTED>")
    # The earlier, spurious occurrence must be left completely untouched.
    assert "<p>contains a fake </body> inside some text</p>" in out
    # Only the real, final tag gets the insertion immediately before it.
    assert out.endswith("<INSERTED></body>")
    assert out.count("<INSERTED>") == 1


def test_inject_before_last_raises_if_tag_missing():
    try:
        m._inject_before_last("<p>no closing tag here</p>", "</body>", "<X>")
        assert False, "Expected RuntimeError"
    except RuntimeError:
        pass


def test_served_shift_report_page_is_not_corrupted_by_its_own_eml_template(client, monkeypatch):
    """shift-report.html's own inline script legitimately contains the text
    '</body>' (building the EML HTML in JS) -- prove the served page still
    injects the real deferred <script> tags in the right place and the
    inline script is not truncated/corrupted.
    """
    clock(monkeypatch)
    enter(client)
    page = client.get("/shift-report.html").text

    # The adversarial condition this bug needs actually exists in the file:
    # more than one literal "</body>" substring.
    assert page.count("</body>") >= 2, \
        "Test assumption stale: shift-report.html no longer contains a " \
        "literal '</body>' inside its own JS -- safe either way, but this " \
        "test should be revisited."

    # The real structural close must be the LAST one, with the deferred
    # controller scripts injected immediately before it -- not before an
    # earlier, spurious occurrence inside the inline script's JS string.
    assert page.rstrip().endswith("</html>")
    last_body = page.rfind("</body>")
    before_last_body = page[:last_body]
    assert before_last_body.endswith('<script defer src="/web/shift-data.js"></script>')

    # The inline script must still be syntactically whole -- its own
    # function definitions must all still be present and not truncated.
    assert "function buildEmailHtml(){" in page
    assert "function confirmEnd(){" in page
    assert "function createReport(){" in page
