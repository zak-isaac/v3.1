"""Regression coverage for large, previously accepted Cetaris exports.

The tests use synthetic data only. No workplace spreadsheet is committed.
"""
from datetime import datetime
from io import BytesIO
import json
import sqlite3

from openpyxl import Workbook

import v31_cetaris
import v31_reporting


def example_monthly_export(rows=1050):
    book=Workbook()
    sheet=book.active
    sheet.append([
        "WO Number","Asset #","WO Status","Primary RFR",
        "Service/Repair Description","Technician","Technician Remarks",
        "Start Date","Update Date / Time","WO Date / Time",
        "WO Last Complete Date / Time","WO Closed Date/Time",
        "In Service Date / Time"
    ])
    for index in range(rows):
        sheet.append([
            f"MONTH-{index:05d}","CONV-01","Closed","Proactive Repair",
            "Adjustment","Sample Tech A" if index%2==0 else "Sample Tech B",
            "Verified work",
            "11/03/2026 08:30 AM","11/03/2026 11:15 AM",
            "2026-11-03T07:00:00","2026-11-03T11:00:00",
            "11/03/2026 11:20 AM","11/03/2026 10:50 AM"
        ])
    buffer=BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def test_monthly_excel_dates_cached_without_changing_parsed_values():
    data=example_monthly_export(1050)
    v31_cetaris._parse_date_text.cache_clear()
    stats={}
    parsed=v31_cetaris.parse_excel(data,stats)
    assert len(parsed)==1050
    assert stats["source_rows"]==1050
    assert stats["parsed_rows"]==1050
    assert parsed[0]["start_at"]=="2026-11-03T08:30:00"
    assert parsed[0]["updated_at"]=="2026-11-03T11:15:00"
    assert parsed[0]["opened_at"]=="2026-11-03T07:00:00"
    assert parsed[0]["last_complete_at"]=="2026-11-03T11:00:00"
    assert parsed[0]["closed_at"]=="2026-11-03T11:20:00"
    assert parsed[0]["in_service_at"]=="2026-11-03T10:50:00"
    assert parsed[-1]["wo_number"]=="MONTH-01049"
    assert v31_cetaris._parse_date_text.cache_info().hits>5000
    assert v31_cetaris._parse_dt(datetime(2026,11,3,12,5)).isoformat()=="2026-11-03T12:05:00"


def test_monthly_stage_refresh_uses_cached_technicians_not_full_json():
    conn=sqlite3.connect(":memory:")
    conn.row_factory=sqlite3.Row
    try:
        data=example_monthly_export(500)
        original=v31_reporting.stage(conn,1,data,"month.xlsx","SYNTHETIC")
        assert {r["name"] for r in original["technicians"]}=={
            "Sample Tech A","Sample Tech B"}
        trace=[]
        conn.set_trace_callback(trace.append)
        details=v31_reporting.stage_details(conn,1)
        conn.set_trace_callback(None)
        assert details["stats"]["parsed_rows"]==500
        assert details["technicians"]==original["technicians"]
        assert not any("SELECT raw_json FROM report_stage" in sql for sql in trace)
        assert not any("SELECT * FROM report_stage" in sql for sql in trace)

        # Old V3.1 files already on users' machines still load without
        # modifying or discarding the existing stage.
        old_stats=dict(original["stats"])
        old_stats.pop("technicians")
        conn.execute("UPDATE report_stage SET stats_json=? WHERE shift_id=1",
                     (json.dumps(old_stats),))
        fallback=v31_reporting.stage_details(conn,1)
        assert fallback["technicians"]==original["technicians"]
    finally:
        conn.close()


def test_restage_retains_prior_technicians_when_cached():
    conn=sqlite3.connect(":memory:")
    conn.row_factory=sqlite3.Row
    try:
        data=example_monthly_export(30)
        first=v31_reporting.stage(conn,1,data,"month.xlsx","SYNTHETIC")
        conn.execute("UPDATE report_stage SET applied_at='2026-11-03T12:00:00' WHERE shift_id=1")
        second=v31_reporting.stage(conn,1,data,"month.xlsx","SYNTHETIC")
        details=v31_reporting.stage_details(conn,1)
        assert second["technicians"]==first["technicians"]
        assert set(details["prior_stage_technicians"])=={
            "Sample Tech A","Sample Tech B"}
        assert details["imported"] is False
    finally:
        conn.close()
