"""Locks the V3.1 passdown EML to real parity with the live V3 workshop
app's format: X-Unsent (direct Outlook compose), the IND3 Next Gen
signature block (with embedded Spark CID image), and the bordered/zebra
PM table -- see v31_signature.py, v31_pm_table_style.py.
"""
import email
import email.policy

import pytest
from fastapi.testclient import TestClient
import app as m

from test_reporting import clock, enter, workbook, row, HEADERS


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PRIVATE", tmp_path)
    monkeypatch.setattr(m, "DB_FILE", tmp_path / "v31.sqlite3")
    monkeypatch.setattr(m, "SESSION_SECRET_FILE", tmp_path / "key")
    monkeypatch.delenv("IND3_V31_ROSTER_PATH", raising=False)
    with TestClient(m.app) as c:
        yield c


def _import_one_wo(client):
    data = workbook([row("5001")])
    client.post("/api/reporting/stage", files={"file": ("cetaris.xlsx", data,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    client.post("/api/reporting/import", json={"technicians": ["Synthetic A"]})


def _save_and_download(client):
    client.post("/api/reporting/draft", json={
        "vendors": [], "ro": [], "notes": "", "loto": True, "missingKeys": []})
    saved = client.post("/api/shifts/report", json={})
    assert saved.status_code == 200, saved.text
    download = client.get("/api/shifts/report/download")
    assert download.status_code == 200
    return download.content


def _html_part(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_content()
    raise AssertionError("No text/html part found in EML")


def test_eml_opens_outlook_directly_in_compose_mode(client, monkeypatch):
    clock(monkeypatch)
    enter(client, "S1")
    _import_one_wo(client)
    content = _save_and_download(client)
    msg = email.message_from_bytes(content, policy=email.policy.SMTP)
    assert msg["X-Unsent"] == "1", \
        "Missing X-Unsent:1 -- Outlook would open this as a read-only received message, not a draft"


def test_eml_includes_ind3_next_gen_signature_with_embedded_spark_image(client, monkeypatch):
    clock(monkeypatch)
    enter(client, "S1")
    _import_one_wo(client)
    content = _save_and_download(client)
    msg = email.message_from_bytes(content, policy=email.policy.SMTP)
    html = _html_part(msg)

    assert "eCommerce Fulfillment Center # 7552/8972" in html
    assert "5259 W. 500 North McCordsville, Indiana, 46055" in html
    assert "Save money." in html and "Live better" in html
    assert "cid:ind3-walmart-spark" in html
    # The yellow Walmart spark is small and in-line with the wordmark.
    assert 'Walmart&nbsp;<img' in html
    assert 'width="20" height="20"' in html
    assert "white-space:nowrap" in html

    inline_images = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert inline_images, "Spark PNG must be embedded as an inline related image"
    cid = inline_images[0]["Content-ID"]
    assert cid == "<ind3-walmart-spark>"
    assert inline_images[0].get_content_disposition() == "inline"


def test_eml_signature_uses_known_lead_name_and_default_id(client, monkeypatch):
    clock(monkeypatch)
    enter(client, "S1")  # signs in as management associate '8972' -> name "Management"
    _import_one_wo(client)
    content = _save_and_download(client)
    html = _html_part(email.message_from_bytes(content, policy=email.policy.SMTP))
    # "Management" is not in the KNOWN contact map -- must render generically,
    # never guess an NT ID for an unrecognized name.
    assert "Management" in html
    assert " - ins0002" not in html


def test_pm_needing_completion_renders_as_bordered_zebra_table_in_eml(client, monkeypatch):
    clock(monkeypatch)
    enter(client, "S1")
    _import_one_wo(client)
    pm_csv = b"WO Number,PM Due Date,Asset #\nPMPARITY1,11/05/2026,BALER-PARITY\n"
    uploaded = client.post("/api/pm-needing-completion/upload",
        files={"file": ("pm-needing.csv", pm_csv, "text/csv")})
    assert uploaded.status_code == 200, uploaded.text

    content = _save_and_download(client)
    html = _html_part(email.message_from_bytes(content, policy=email.policy.SMTP))

    assert "<table" in html
    assert "PM Due Date" in html and "Promise Date / Time" in html and "WO Date / Time" in html
    assert "PMPARITY1" in html
    assert "BALER-PARITY" in html
    assert "1a56db" in html  # V3's exact navy header color



def test_outlook_eml_uses_passdown_recipients_from_facility_settings(client, monkeypatch):
    """Unsent Outlook draft must prefill To using the saved admin settings."""
    clock(monkeypatch)
    enter(client, "S1")
    configured = client.post("/api/admin/settings", json={
        "distribution_center": "IND3",
        "recipients": "first@example.com; second@example.com, FIRST@example.com",
        "sender_name": "IND3 Shift Passdown",
    })
    assert configured.status_code == 200, configured.text
    assert configured.json()["recipients"] == "first@example.com,second@example.com"

    _import_one_wo(client)
    msg = email.message_from_bytes(_save_and_download(client), policy=email.policy.SMTP)
    assert msg["X-Unsent"] == "1"
    assert msg["To"] is not None
    assert [recipient.addr_spec for recipient in msg["To"].addresses] == [
        "first@example.com", "second@example.com"
    ]
    assert msg["Subject"] == "IND3 - S1 Shift Passdown - 2026-10-26"

    # Invalid addresses must fail when saved, not break the EML later.
    invalid = client.post("/api/admin/settings", json={
        "distribution_center": "IND3",
        "recipients": "valid@example.com, invalid@@example.com",
        "sender_name": "IND3 Shift Passdown",
    })
    assert invalid.status_code == 422
    assert client.get("/api/admin/settings").json()["recipients"] == (
        "first@example.com,second@example.com"
    )
