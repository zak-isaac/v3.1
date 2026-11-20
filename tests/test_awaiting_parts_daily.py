"""Awaiting Parts uses daily WO Details, never a second or monthly upload."""
from test_reporting import client, clock, enter, upload


def details(wo, asset, status, rfr="11", technician=None):
    return {
        "WO Number": wo,
        "Asset #": asset,
        "WO Status": status,
        "Primary RFR": rfr,
        "Service/Repair Description": "Repair awaiting parts",
        "Technician": technician,
        "Technician Remarks": "",
        "Start Date": None,
    }


def test_daily_wo_details_parts_pending_across_all_rfr_and_no_tech(client, monkeypatch):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    response = upload(client, [
        details("P-BD", "VOIDFILL1201", "P", "01"),
        details("P-PRO", "BAGGER0003", "Parts Pending", "11"),
        details("P-FOUND", "RC0011", "P", "12"),
        details("P-PM", "PACK004", "P", "08"),
        details("P-DONE", "PACK005", "Completed", "12"),
        details("P-NOT-P", "PACK006", "To Be Done", "08"),
        details("P-PKM", "PKM01234", "P", "01"),
    ])
    assert response.status_code == 200, response.text
    board = client.get("/api/assets/status").json()
    pending = board["awaiting_parts"]
    assert pending == [
        {"asset": "BAGGER0003", "wo_number": "P-PRO"},
        {"asset": "PACK004", "wo_number": "P-PM"},
        {"asset": "RC0011", "wo_number": "P-FOUND"},
        {"asset": "VOIDFILL1201", "wo_number": "P-BD"},
    ]
    html = client.get("/api/shifts/report/preview").json()["html"]
    section = html.split(">Awaiting Parts</strong>", 1)[1].split(
        ">Returned to Service</strong>", 1)[0]
    for asset, wo in [("VOIDFILL1201", "P-BD"), ("BAGGER0003", "P-PRO"),
                      ("RC0011", "P-FOUND"), ("PACK004", "P-PM")]:
        assert asset in section and f"(WO# {wo})" in section
    assert "P-PKM" not in section
    assert "P-DONE" not in section


def test_daily_parts_status_survives_omission_but_clears_on_new_cetaris_status(
    client, monkeypatch
):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    assert upload(client, [details("P-OLD", "CONV0012", "P")]).status_code == 200
    assert client.get("/api/assets/status").json()["awaiting_parts"] == [
        {"asset": "CONV0012", "wo_number": "P-OLD"}
    ]
    # The next day's WO Details may omit a parts-pending repair.
    # This is independent of the no-persistence PM-urgency rule.
    clock(monkeypatch, day=27, hour=10)
    assert upload(client, [details("P-OTHER", "RC0011", "P")]).status_code == 200
    listed = client.get("/api/assets/status").json()["awaiting_parts"]
    assert {x["wo_number"] for x in listed} == {"P-OLD", "P-OTHER"}
    # Explicit newer Cetaris status removes only the resolved WO.
    assert upload(client, [details("P-OLD", "CONV0012", "In Process")]).status_code == 200
    listed = client.get("/api/assets/status").json()["awaiting_parts"]
    assert [x["wo_number"] for x in listed] == ["P-OTHER"]
    html = client.get("/api/shifts/report/preview").json()["html"]
    assert "P-OLD" not in html.split(">Awaiting Parts</strong>", 1)[1].split(
        ">Returned to Service</strong>", 1)[0]
