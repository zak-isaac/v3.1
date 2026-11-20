"""Parse open/pending PM reports exported from Cetaris.

Expected columns (case-insensitive, partial match):
  WO Number | WO Date / Time | PM Due Date | Promise Date / Time | Asset #

Accepts .xlsx, .xls, .csv
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from v31_pm_due_scanner import due_date
from v31_cetaris_codes import normalize_primary_rfr
import io


@dataclass
class PendingPM:
    wo_number:    str
    wo_date:      str
    pm_due_date:  str
    promise_date: str
    asset_num:    str
    wo_status:   str = ""

    @property
    def due_today(self) -> bool:
        return due_date(self.pm_due_date) == date.today()


def _col(headers: list[str], *keywords: str) -> int:
    """Return index of first header that contains any keyword (case-insensitive)."""
    for i, h in enumerate(headers):
        hl = h.lower().strip()
        if any(k in hl for k in keywords):
            return i
    return -1


def _parse_rows(rows: list[list[str]]) -> list[PendingPM]:
    if not rows:
        return []
    headers = rows[0]
    wo_col   = _col(headers, "wo number", "wo #", "work order")
    date_col = _col(headers, "wo date", "date / time", "date/time")
    due_col  = _col(headers, "pm due", "due date")
    prom_col = _col(headers, "promise")
    asset_col= _col(headers, "asset")
    status_col= _col(headers, "wo status")
    rfr_col= _col(headers, "primary rfr")

    results: list[PendingPM] = []
    for row in rows[1:]:
        if not row or not any(c.strip() for c in row):
            continue
        def _get(idx: int) -> str:
            if idx < 0 or idx >= len(row):
                return ""
            return str(row[idx]).strip()
        wo = _get(wo_col)
        if not wo or wo.lower() in ("wo number", "nan", "none", ""):
            continue
        # A filtered Today's DAILY PMs export may omit Primary RFR,
        # but when provided don't misclassify other RFRs as PMs.
        if rfr_col >= 0:
            _rfr,_code=normalize_primary_rfr(_get(rfr_col))
            if _code != "08":
                continue
        results.append(PendingPM(
            wo_number   = wo,
            wo_date     = _get(date_col),
            pm_due_date = _get(due_col),
            promise_date= _get(prom_col),
            asset_num   = _get(asset_col),
            wo_status   = _get(status_col),
        ))
    return results


def parse_pm_pending(content: bytes, filename: str) -> list[PendingPM]:
    """Parse Excel or CSV PM pending report. Returns list of PendingPM rows."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in ("xlsx", "xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            ws = wb.active
            rows = [[str(cell.value or "") for cell in row] for row in ws.iter_rows()]
            wb.close()
            return _parse_rows(rows)
        except Exception:
            pass
        # fallback: try xlrd for .xls
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=content)
            ws = wb.sheet_by_index(0)
            rows = [[str(ws.cell_value(r, c)) for c in range(ws.ncols)] for r in range(ws.nrows)]
            return _parse_rows(rows)
        except Exception:
            return []

    # CSV fallback
    import csv
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader]
    return _parse_rows(rows)
