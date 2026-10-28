# IND3 Shift Reporting V3.1 — Drax-backed Login and Shift Sessions

This is a **new, independent local development application**, separate from the live Workshop V3 and `zak-isaac/ind3-shift-passdown`. Its database, roster cache, and sessions are private to this project.

## Launch on your Mac

```bash
cd ~/Projects/ind3-v3.1
git status --short
git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q tests/test_login.py tests/test_roster_lifecycle.py
python -m uvicorn app:app --host 127.0.0.1 --port 8980
```

Open <http://127.0.0.1:8980/> and use Command+Shift+R to refresh the UI after updating.

## Connect the approved Drax workbook

For real MST logins, V3.1 needs an **approved copy or authorized synchronized path** to the Drax roster. It **does not connect to the production V3 database** and does not download the live Excel roster automatically.

The workbook must contain the existing Drax columns `Name`, `Assoc. #`, `Default ID`, `Shift`, and `Job Title`. The original double-spaced filename `Drax  Associates.xlsx` and its exact Windows OneDrive location are recognized.

**Original V3 Windows path — used automatically in V3.1 on the Workshop PC:**

```text
C:\Users\ins0002\OneDrive - Walmart Inc\Drax  Associates.xlsx
```

The **two spaces** between `Drax` and `Associates.xlsx` are intentional. V3.1 reads this existing synchronized file **read-only**; it does not change the actual Drax workbook.

You can still use the original `ASSOCIATES_PATH` environment variable or override the source specifically for V3.1 with `IND3_V31_ROSTER_PATH` (higher priority). The canonical Windows path cannot work unchanged on macOS because Mac drive layouts differ.

**Option A (for local Mac development):** Point V3.1 to an authorized OneDrive-synced Mac copy:

```bash
export IND3_V31_ROSTER_PATH="/absolute/mac/path/to/Drax  Associates.xlsx"
python -m uvicorn app:app --host 127.0.0.1 --port 8980
```

**Option B:** Place an approved copy in `.local-data/Drax  Associates.xlsx` (or `.local-data/Drax Associates.xlsx` or `.local-data/mst-roster.xlsx`). The local `.local-data/` directory is git-ignored. Only do this when permitted by your employer's data rules.

**Option C (development only):** `python setup_mst_roster.py` creates a minimal private CSV assignment file. This is not a replacement for the official Drax source.

Drax sync behavior: initial import at startup, background check every **5 minutes**, on-demand refresh as needed, plus **Reload Drax now** in management administration. The local SQLite cache stores source records, displays any source error, and keeps explicit management MST-status, shift, and display-name overrides. Records removed from a successfully reloaded Drax source are disabled; removed files do not silently erase the last cached snapshot.

The manager roster console is accessible both through **Manage Drax Roster** on the login shift-selector dialog and **Administration** in the report workspace. It supports reload, adding authorized local associates, enabling/disabling MST privileges, changing shift assignment, editing display names, resetting overrides, without modifying the original Drax source.

## Correct login flow

- **MST:** Enter authorized associate number on the first login screen. V3.1 checks the Drax-backed role, date, ownership and assigned shift. It starts/resumes the MST's own shift and opens **Shift Report → Shift Happenings** directly.
- **Management preview identifiers:** `8972` and `2759` go to the **S1–S5 shift-selection dialog**. Managers may start an eligible shift or resume an open one.
- **Active Shift Detected / New Shift Ready:** Resume and Start ask for the associate number in a popup on the same screen. The selected shift ID is verified server-side, without bouncing back to the first login page.
- **Authorization:** A regular MST cannot resume or modify another associate's shift. Management can access eligible open sessions. **Overtime is disabled entirely in V3.1 for now.** MSTs are restricted to their Drax-assigned S1–S5 shift, and management can select from S1–S5.

### Reporting times (Indianapolis facility clock)

| Shift | Days shift starts | Scheduled | First start | Existing report until |
| --- | --- | --- | --- | --- |
| S1 | Mon–Thu | 5:30 AM–4 PM | 4:30 AM | 5 PM |
| S2 | Mon–Thu | Noon–10:30 PM | 11 AM | 11:30 PM |
| S3 | Mon–Thu | 8:30 PM–7 AM next day | 7:30 PM | 8 AM next day |
| S4 | Fri–Sun | 5:30 AM–6 PM | 4:30 AM | 7 PM |
| S5 | Fri–Sun | 5:30 PM–6 AM next day | 4:30 PM | 7 AM next day |

**One-hour opening/closing rule:** Starting a brand-new report is permitted from one hour before scheduled start until scheduled end. The one-hour period after scheduled end is for **resuming or finishing an existing report only**.

At most **two shift sessions** may be active. Overnight work remains assigned to the date its shift started. Automatic closure after the end + one-hour grace is checked at startup, on activity, and approximately every 60 seconds. Expired records are closed but not erased.

**End Shift** requires a saved passdown. Use **Generate Passdown (.eml)** first; this stores a copy in the selected V3.1 session, then downloads a local Outlook draft. End Shift only closes the selected session. Sign Out only ends the browser login session.

## Development boundaries and security

- **Associate number alone is not secure employee identity verification.** This prototype is **localhost only**; do not deploy on the Walmart facility network, open a tunnel, or enable public access. Enterprise SSO and proper authorization are required before real deployment.
- Any official roster or Cetaris data must be handled only through approved company systems and locations. Never commit such data or secrets to this public repository.
- The Shift Report interface starts with **zero sample work orders**. Cetaris parsing and verified work-order reporting are **not yet implemented**; file selection is currently a preview. Generating a local EML saves a draft in the isolated V3.1 database, not a verified production report.
- The original Workshop app, repository, and database remain untouched.

See [login and shift-lifecycle audit](docs/V31_LOGIN_AUDIT.md) for the comparison matrix and remaining environment-dependent verification.
