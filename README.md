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

### V3.1 Shift Report workspace (October 2026 refresh)

Cetaris import status, **Import Cetaris**, **Change Technicians**, and **Import Audit** are now grouped next to the Shift Report title. The Shift Progress bar remains visible and is **0% when no work has been staged**. It represents actual report-preparation milestones, not time elapsed: staged file (20%), imported with no qualifying WOs (30%), imported with qualifying WOs/PMs (75%), saved passdown (100%).

The red Cetaris button starts XLSX/CSV staging; **staging is not the same as importing**. A selection dialog opens immediately. It prechecks only exact technician-name matches to active Drax MSTs on the selected shift, then displays other-shift MSTs and unmatched Cetaris technicians as optional groups. Manually selecting an additional/overtime technician **does not change login permissions or automatically establish WO ownership**. Confirm **Import Selected Work Orders** to persist the work. Missing or ambiguous completion evidence stays visible in Import Audit. The app now distinguishes an unconfirmed staged replacement file from an imported file, preserving earlier saved WOs.

The sidebar now uses the genuine Walmart 2025 vector artwork (official shapes and colors), with IND3 and MST Reliability Operations separated into readable rows. This change does not alter V3, the historical archive, or Drax workbooks.

## Shift Report — Cetaris import (Phase 1)

The existing approved Shift Report UI is wired to the new, private V3.1 database.

1. Sign in as an authorized MST (Drax-assigned shift) or select a shift as management.
2. **Import Cetaris** accepts validated XLSX or CSV work-order exports, bounded to 12 MB / 30,000 labor rows. Staging the file **does not yet add work orders**.
3. Select the actual technicians present in the export. V3.1 imports only the selected technicians' rows and uses the reviewed V3 actual-work attribution engine. Drax shift assignments resolve overlapping work when technician identity matches, without assuming that selecting a technician proves ownership.
4. **Shift Happenings** groups selected labor by WO number, preserves individual remarks and combines multiple technicians, and uses the Cetaris Service/Repair Description as the displayed cause. Preventive-maintenance WOs are counted once and excluded from normal happenings.
5. Closed work orders are included; Completed-but-not-Closed non-PM WOs are excluded pending review. Use **Import Audit → Review completed work orders** to add a closure reminder, or leave it ignored. A reminder is labeled **PENDING**, never falsely RTS.
6. **Breakdown OOS** is tracked from a qualifying In Process breakdown. A later Closed WO may transition to RTS in another eligible shift without overwriting the original OOS record.
7. Edit, exclude, and restore are persisted. Reimporting a file merges WOs by number, preserves manual edits, and leaves previously imported WOs intact. Missing or ambiguous labor timestamps remain in the Import Audit rather than receiving guessed shift attribution.
8. Vendors, RO entries, notes and LOTO draft values autosave in the selected shift database. Creating the Outlook EML saves the current passdown to the selected shift; **End Shift requires a saved report**. Emails are not sent by the app.

**Not wired yet:** AI rewrite/proofreading preview, processing the separate pending-PM file, real Cetaris automation (imports require the user to supply the export), and production identity verification. Continue using the live Workshop production workflow until independently accepted on approved equipment.

## Preserve historical V2/V3 shift records

V2 and V3 used a shared per-facility SQLite database, with IND3 typically at \`dbs/ind3.db\`. V3.1 **does not convert, truncate, or overwrite the old database**. It stores a validated exact SQLite backup under \`.local-data/legacy-v2-v3-archive.sqlite3\` retaining every source table, row, original shift ID, entries, reports, timestamps, and ancillary fields. The V3.1 login and new shift database remains separate.

On a machine with approved access to the original repository and IND3 historical database:

\`\`\`powershell
# Run in the V3.1 project directory:
python scripts/import_legacy_history.py --source "C:\approved\old-v3\dbs\ind3.db"
# After verifying the table/shift/report counts:
python scripts/import_legacy_history.py --source "C:\approved\old-v3\dbs\ind3.db" --import
\`\`\`

The first command is a **dry run**. The second makes an independent snapshot using SQLite's online backup API. Neither command writes to the old V2/V3 database. If importing again later, the new snapshot replaces only the separate V3.1 history archive, not the V3.1 active-shift database. The Shift History screen reads this archive and logs **new V3.1 sign-ins** separately.

**Historical login limitation:** The original database records past shift openings (\`shifts.opened_by_assoc\`, where available) and their times. It does not necessarily record every historical sign-in, failed attempt, or sign-out. Missing login events cannot be reconstructed.

## Drax and original V3 synchronization

The approved Drax roster is the same OneDrive workbook as original V3:

\`\`\`text
C:\Users\ins0002\OneDrive - Walmart Inc\Drax  Associates.xlsx
\`\`\`

- **Drax -> V3.1:** Existing \`v31_roster.py\` refreshes the private roster cache from the authorized workbook about every 5 minutes. Changes made in Excel/OneDrive appear after the next successful read.
- **Drax -> old V3:** The old V3 program already watches the workbook. Its exact handling of administrative overrides and MST demotions depends on its legacy database rules; do not assume every changed cell overwrites prior manual V3 overrides.
- **Old V3 -> Drax:** New \`v31_drax_bridge.py\` compares the old V3 \`associates\` database (read-only), the workbook, and its last private baseline. Nonconflicting changes to \`Name\`, \`Shift\`, and MST flag can be written to the Excel file only with **explicit authorization and a write flag**. Workbook differences on first setup establish a baseline, and conflicting changes remain for human review. Deletions/new associates are not auto-mirrored.

Review only (does not edit Drax):

\`\`\`powershell
python scripts/sync_drax_from_v3.py --v3-db "C:\approved\old-v3\dbs\ind3.db"
\`\`\`

**Do not enable the following on a live company OneDrive workbook without approved IT authorization, an exclusive maintenance window, a verified backup and actual Windows/OneDrive acceptance testing.** If approved, the write setting is \`IND3_V31_DRAX_WRITE_ENABLED=1\`, and \`--apply\` enables the manual CLI write. The optional background bridge also needs \`IND3_V31_LEGACY_DB_PATH\` explicitly set to the old database file. The bridge never writes to the old V3 database. Each proposed workbook write creates a private before-image backup and checks for changes during preparation. OneDrive sync conflicts or Excel file locks still require manual recovery.

This is a **limited, guarded two-way synchronization bridge, not an unconditional real-time bidirectional merge**. It does not change original V3's code or guarantee that every administrative override in V3 is immediately reflected in Excel.


## Development boundaries and security

- **Associate number alone is not secure employee identity verification.** This prototype is **localhost only**; do not deploy on the Walmart facility network, open a tunnel, or enable public access. Enterprise SSO and proper authorization are required before real deployment.
- Any official roster or Cetaris data must be handled only through approved company systems and locations. Never commit such data or secrets to this public repository.
- The Shift Report starts with **zero sample work orders**, but real Cetaris XLSX/CSV parsing and shift-scoped work-order persistence are now connected. Generated local EML files are not sent; the report is not yet production-certified. Pending-PM export parsing and AI rewriting are separate upcoming stages.
- The original Workshop app, repository, and database remain untouched.

See [login and shift-lifecycle audit](docs/V31_LOGIN_AUDIT.md) for the comparison matrix and remaining environment-dependent verification.
