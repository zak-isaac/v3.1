"""Cetaris WO Status and Primary RFR vocabulary from the IND3 UI.

The WO status filter shows C/D/I/W/O/P (then T/U/V below P).  Primary
RFR shows ten categories, with numeric codes.  Code is authoritative;
do not infer asset RTS from either dropdown or from WO Line Status.
"""
from __future__ import annotations
import re

WO_STATUSES = {
    "C": "Closed",
    "D": "Completed",
    "I": "In Process",
    "W": "Manager Review Required",
    "O": "Outside Service",
    "P": "Parts Pending",
}
# The screenshot truncates the full W and O descriptions, but shows their
# codes.  The prefixes and aliases below support exports using either form.
STATUS_ALIASES = {
    "closed": "C", "completed": "D",
    "in process": "I", "in progress": "I",
    "manager review required": "W", "manager review": "W",
    "manager": "W", "outside service": "O",
    "outside services": "O", "outside s": "O",
    "parts pending": "P", "parts pend": "P",
}
RFR_CATEGORIES = {
    "01": "Breakdown",
    "44": "Campaign/Recall",
    "12": "Found During PM",
    "24": "Manager Task",
    "08": "Preventive Maintenance",
    "11": "Proactive Repair",
    "04": "Process Interruption",
    "42": "Project",
    "38": "Warranty",
    "22": "Work Request",
}
RFR_ALIASES = {
    "breakdown": "01",
    "campaign/recall": "44", "campaign recall": "44",
    "found during pm": "12",
    "manager task": "24",
    "preventive maintenance": "08", "preventative maintenance": "08",
    "proactive repair": "11",
    "process interruption": "04", "process interrupt": "04",
    "project": "42", "warranty": "38",
    "work request": "22",
}
ACTIVE_WO_STATUSES = frozenset(("in process", "manager review required",
                                "outside service", "parts pending"))
COMPLETED_WO_STATUSES = frozenset(("closed", "completed"))


def _normalize(raw, mapping, aliases, code_pattern):
    value = str(raw or "").strip()
    if not value:
        return "", ""
    low = re.sub(r"\s+", " ", value).strip().casefold()
    code = aliases.get(low)
    if not code:
        # Match codes as standalone tokens at the start or end:
        # "P", "Parts Pending; P", "01 - Breakdown", "Breakdown (01)".
        for candidate in re.findall(code_pattern, value, re.I):
            candidate = candidate.upper().zfill(2) if code_pattern == r"(?<!\d)\d{1,2}(?!\d)" else candidate.upper()
            if candidate in mapping:
                code = candidate
                break
    if not code:
        # Real exports sometimes truncate long category descriptions.
        if mapping is WO_STATUSES:
            for prefix, candidate in (("manager", "W"), ("outside s", "O"), ("parts pen", "P")):
                if low.startswith(prefix):
                    code = candidate
                    break
        else:
            for prefix, candidate in (("preventive main", "08"), ("process inter", "04")):
                if low.startswith(prefix):
                    code = candidate
                    break
    return (mapping[code], code) if code else (value, "")


def normalize_wo_status(raw):
    return _normalize(raw, WO_STATUSES, STATUS_ALIASES, r"(?<![A-Za-z])(?:[CDIWOP])(?![A-Za-z])")


def normalize_primary_rfr(raw):
    return _normalize(raw, RFR_CATEGORIES, RFR_ALIASES, r"(?<!\d)\d{1,2}(?!\d)")
