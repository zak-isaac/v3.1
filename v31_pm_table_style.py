"""Shared styling for the "PM's Needing to be Completed" table, ported
verbatim from the live V3 workshop app (pm_table_style.py).

No due-date alarm color anywhere, ever -- these are routine, daily-recurring
maintenance items, "due today" is the normal state for nearly every row,
not an exception worth an alarm color. See the V3 original for the full
rationale (alert fatigue + Outlook dark-mode color remapping).
"""

PM_HEADER_HEX = "1a56db"    # navy -- no leading '#' (docx-shading friendly)
PM_ROW_EVEN_HEX = "f9fafb"  # pale gray
PM_ROW_ODD_HEX = "ffffff"   # white


def pm_zebra_hex(index: int) -> str:
    """Plain alternating row color for the PM table, no leading '#'."""
    return PM_ROW_EVEN_HEX if index % 2 == 0 else PM_ROW_ODD_HEX
