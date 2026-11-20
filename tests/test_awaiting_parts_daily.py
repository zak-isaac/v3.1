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
    # A new daily export belongs to a new shift session, not yesterday's
    # expired S1 report. The facility-wide parts backlog stays available.
    enter(client, "S1")
    assert upload(client, [details("P-OTHER", "RC0011", "P")]).status_code == 200
    listed = client.get("/api/assets/status").json()["awaiting_parts"]
    assert {x["wo_number"] for x in listed} == {"P-OLD", "P-OTHER"}
    # Still In Process is not Closed/Completed. The outstanding record
    # must remain in the tracker and retain its exact WO identity.
    assert upload(client, [details("P-OLD", "CONV0012", "In Process")]).status_code == 200
    listed = client.get("/api/assets/status").json()["awaiting_parts"]
    assert {x["wo_number"] for x in listed} == {"P-OLD", "P-OTHER"}
    # Explicit Completed/Closed from Cetaris clears only that specific WO.
    assert upload(client, [details("P-OLD", "CONV0012", "Completed")]).status_code == 200
    listed = client.get("/api/assets/status").json()["awaiting_parts"]
    assert [x["wo_number"] for x in listed] == ["P-OTHER"]
    html = client.get("/api/shifts/report/preview").json()["html"]
    assert "P-OLD" not in html.split(">Awaiting Parts</strong>", 1)[1].split(
        ">Returned to Service</strong>", 1)[0]


def upload_with_asset_alias(client, *, items):
    """Synthetic daily Cetaris sheet with BOTH Asset # and Asset ID."""
    from io import BytesIO
    from openpyxl import Workbook
    wb = Workbook()
    sheet = wb.active
    columns = ["WO Number", "Asset #", "Asset ID", "WO Status",
               "Primary RFR", "Service/Repair Description", "Technician",
               "Technician Remarks", "Start Date", "PM Due Date",
               "Update Date / Time"]
    sheet.append(columns)
    for item in items:
        sheet.append([item.get(column) for column in columns])
    buf = BytesIO()
    wb.save(buf)
    wb.close()
    return client.post("/api/reporting/stage", files={"file":(
        "todays-wo-details.xlsx", buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_asset_number_wins_over_conflicting_asset_id_for_parts_pm_and_work(
    client, monkeypatch
):
    clock(monkeypatch, day=26, hour=10)
    enter(client, "S1")
    items=[
        {"WO Number":"28055231","Asset #":"SCRUB0011",
         "Asset ID":"SCRUB0002","WO Status":"Parts Pending",
         "Primary RFR":"Breakdown","Service/Repair Description":"Repair",
         "Technician":"Synthetic A","Technician Remarks":"Parts ordered",
         "Start Date":"2026-10-26 08:00",
         "Update Date / Time":"10/26/2026 09:00 AM"},
        {"WO Number":"PM-ALIAS","Asset #":"TRKGATE0002",
         "Asset ID":"TRKGATE002","WO Status":"To be Done",
         "Primary RFR":"Preventive Maintenance",
         "PM Due Date":"10/26/2026",
         "Service/Repair Description":"Quarterly inspection",
         "Technician":None,"Technician Remarks":None},
    ]
    response=upload_with_asset_alias(client,items=items)
    assert response.status_code==200,response.text
    board=client.get("/api/assets/status").json()
    assert {"asset":"SCRUB0011","wo_number":"28055231"} in board["awaiting_parts"]
    due=client.get("/api/pm-needing-completion").json()
    assert any(r["asset_num"]=="TRKGATE0002" and r["wo_number"]=="PM-ALIAS"
               for r in due["rows"])
    from test_reporting import import_techs
    assert import_techs(client,["Synthetic A"]).status_code==200
    work=client.get("/api/reporting/state").json()["rows"]
    assert any(r["wo_number"]=="28055231" and r["asset"]=="SCRUB0011"
               for r in work)
    assert any(r["wo_number"]=="28055231" and r["asset"]=="SCRUB0011"
               for r in board["active_breakdowns"])


def test_stale_cetaris_export_cannot_restore_completed_parts_pending_wo(
    client, monkeypatch
):
    clock(monkeypatch,day=26,hour=10)
    enter(client,"S1")
    initial=details("P-HISTORY","SCRUB0011","Parts Pending")
    initial["Update Date / Time"]="10/24/2026 08:00 AM"
    # Alias columns exist in this test's generated one-file export.
    assert upload_with_asset_alias(client,items=[initial]).status_code==200
    finished=dict(initial, **{"WO Status":"Closed",
                               "Update Date / Time":"10/26/2026 09:00 AM"})
    assert upload_with_asset_alias(client,items=[finished]).status_code==200
    assert client.get("/api/assets/status").json()["awaiting_parts"]==[]
    # Accidentally uploading an older file again must not resurrect the WO.
    assert upload_with_asset_alias(client,items=[initial]).status_code==200
    assert client.get("/api/assets/status").json()["awaiting_parts"]==[]
