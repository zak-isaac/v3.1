# IND3 Shift Reporting V3.1 — Drax-backed Login and Shift Sessions

This is a **new, independent local development application**, separate from the live Workshop V3 and `zak-isaac/ind3-shift-passdown`. Its database, roster cache, and sessions are private to this project.

## Windows workstation pilot (localhost only)

V3.1 has **not** been installed or validated on a real Walmart workstation. Windows CI tests are an additional safeguard, not proof of approved operational deployment.

On a machine with Python 3.12, an authorized local checkout of V3.1, and the approved Drax workbook:

```powershell
cd C:\path\to\v3.1
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt pytest httpx
python -m pytest -q tests/
$env:IND3_V31_ROSTER_PATH = "C:\approved\Drax Associates.xlsx"
python -m uvicorn app:app --host 127.0.0.1 --port 8980
```

Open `http://127.0.0.1:8980/` on the **same host**. The default app rejects off-host browser connections, so **other MST laptops cannot use this deployment** as-is. Do not disable the restriction or expose it on the corporate network without employer IT approval and a reviewed authentication/access design. An associate number alone identifies a roster entry but does not prove who is typing it.

Validate a test S1–S5 login, valid/invalid ownership, dry-run Cetaris import, S3/S5 attribution, reimport, PMs Needing Completion, manual proactive repair, unresolved OOS and carry-over, saved EML byte equality, history, analytics, and End Shift with synthetic data first. Take a backup of the private SQLite database before any later upgrade or migration. Never overwrite the old V3 production database.

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

For authorized Drax-backed local logins, V3.1 needs an **approved copy or authorized synchronized path** to the Drax roster. It **does not connect to the production V3 database** and does not download the live Excel roster automatically.

The workbook must contain the existing Drax columns `Name`, `Assoc. #`, `Default ID`, `Shift`, and `Job Title`. The canonical filename is `Drax Associates.xlsx` (renamed 2026-11-09 from an old double-spaced name kept only for a now-retired V3 compatibility quirk) at its exact Windows OneDrive location.

**Original V3 Windows path — used automatically in V3.1 on the Workshop PC:**

```text
C:\Users\ins0002\OneDrive - Walmart Inc\Drax Associates.xlsx
```

V3.1 reads this synchronized file on every poll. For most roster reads this is read-only, but see the "V3.1 Administration -> Drax" and "Administration -> Delete" entries in the Drax synchronization section below: shift/MST/name edits and associate deletion from Administration *do* write back into this exact file when explicitly enabled, not just read it.

You can still use the original `ASSOCIATES_PATH` environment variable or override the source specifically for V3.1 with `IND3_V31_ROSTER_PATH` (higher priority). The canonical Windows path cannot work unchanged on macOS because Mac drive layouts differ.

**Option A (for local Mac development):** Point V3.1 to an authorized OneDrive-synced Mac copy:

```bash
export IND3_V31_ROSTER_PATH="/absolute/mac/path/to/Drax Associates.xlsx"
python -m uvicorn app:app --host 127.0.0.1 --port 8980
```

**Option B:** Place an approved copy in `.local-data/Drax  Associates.xlsx` (or `.local-data/Drax Associates.xlsx` or `.local-data/mst-roster.xlsx`). The local `.local-data/` directory is git-ignored. Only do this when permitted by your employer's data rules.

**Option C (development only):** `python setup_mst_roster.py` creates a minimal private CSV assignment file. This is not a replacement for the official Drax source.

Drax sync behavior: initial import at startup, background check every **5 minutes**, on-demand refresh as needed, plus **Reload Drax now** in management administration. The local SQLite cache stores source records, displays any source error, and keeps explicit management MST-status, shift, and display-name overrides. Records removed from a successfully reloaded Drax source are disabled; removed files do not silently erase the last cached snapshot.

The manager roster console is accessible both through **Manage Drax Roster** on the login shift-selector dialog and **Administration** in the report workspace. It supports reload, adding authorized local associates, enabling/disabling MST privileges, changing shift assignment, editing display names, resetting overrides, without modifying the original Drax source.

## Correct login flow

- **Active Drax associate, any Job Title:** Enter the authorized associate number. V3.1 checks the active Drax record, assigned S1–S5 shift, scheduled reporting hours, and exclusive report ownership. The original owner may resume; unrelated associates on the same shift cannot join. A non-MST report owner can use the normal Shift Report workspace.
- **MST relief:** An MST assigned to that shift can explicitly take over a report originally started by a non-MST technician. V3.1 asks for confirmation and records previous owner, new owner and timestamp. It is not an automatic join; the former owner loses edit access. MST-to-MST takeover is blocked.
- **MST overtime:** Only an active MST with a management-approved grant for one specific shift code and date may start/resume an off-shift report. Regular non-MST users cannot receive cross-shift grants. Management can approve and revoke a dated MST overtime grant in **Administration → MST Overtime Approval**. The protected `GET/POST /api/admin/mst-overtime` API also exposes the grant records. No overtime permission is inferred from a different-shift Cetaris technician selection.
- **Management preview identifiers:** `8972` and `2759` go to the **S1–S5 shift-selection dialog**. Managers may start an eligible shift or resume an open one.
- **Active Shift Detected / New Shift Ready:** Resume and Start ask for the associate number in a popup on the same screen. The selected shift ID is verified server-side, without bouncing back to the first login page.
- **Authorization:** Only the current owner or management can work in a report, except the explicit audited MST relief of a technician-owned report. All non-management users are confined to their assigned shift unless a dated MST overtime grant exists. Management can select eligible S1–S5 shifts. Regular associate numbers are **not secure identity verification**; this remains localhost-only.



### Staffing availability prompt (design pending)

A possible start-shift prompt is **"Is an MST on site today?"**. This is **not**
enforced yet: a yes/no answer from the person attempting login is not a
verified roster attendance or security check. Decide how staffing will be
confirmed, who may attest, and whether it blocks opening or only warns before
adding a hard access gate. Current code enforces Drax assignment, shift hours,
exclusive ownership, and explicit MST relief.

### Reporting times (Indianapolis facility clock)

| Shift | Days shift starts | Scheduled | First start | Existing report until |
| --- | --- | --- | --- | --- |
| S1 | Mon–Thu | 5:30 AM–4 PM | 4:30 AM | 5 PM |
| S2 | Mon–Thu | Noon–10:30 PM | 11 AM | 11:30 PM |
| S3 | Mon–Thu | 8:30 PM–7 AM next day | 7:30 PM | 8 AM next day |
| S4 | Fri–Sun | 5:30 AM–6 PM | 4:30 AM | 7 PM |
| S5 | Fri–Sun | 5:30 PM–6 AM next day | 4:30 PM | 7 AM next day |

**One-hour opening/closing rule:** Starting a brand-new report is permitted from one hour before scheduled start until scheduled end. The one-hour period after scheduled end is for **resuming or finishing an existing report only**.

At most **two overlapping shift sessions** may be active at the current facility-clock time. Other dates' open rows do not consume capacity when a local developer moves the workstation clock backwards to test earlier shifts. Overnight work remains assigned to its start date. Automatic closure after the end + one-hour grace is checked at startup, on activity, and approximately every 60 seconds. Expired records are closed but not erased.

**Historical date testing (existing local workflow, no test mode):** Keep using the workstation's time and date as before. With the **2759** management profile in the local-only app, eligible shifts show **Start**, **Resume**, or **Reopen** when a prior report for that date was already closed. Reopen requires explicit confirmation, preserves the previous WOs and saved report, and logs the reopening action. It does not silently reset a completed shift or bypass a genuine time window. Other users cannot reopen closed shifts. The chooser now explains why a shift is unavailable (outside reporting window, closed, already owned, or concurrent capacity). Git over SSH avoids HTTPS GitHub certificate-date errors; the app never disables SSL verification. To protect other apps and certificates, restore the correct workstation clock when finished testing.

**End Shift** requires a saved passdown. Use **Generate Passdown (.eml)** first; this stores a copy in the selected V3.1 session, then downloads a local Outlook draft. End Shift only closes the selected session. Sign Out only ends the browser login session.

**Opening the downloaded .eml faster (2026-11-09):** V3.1 runs as a shared, multi-workstation service -- different technicians open it in a browser on their own PCs, not on the server machine -- so the backend cannot force-launch Outlook directly on a user's PC; no website can force-open a native desktop app without the browser's own permission, by design, on any browser. What *does* work, and now works more reliably since the download was switched from a forced blob save to a normal server link: in the browser's download tray, after the first passdown download, choose **"Always open files of this type"** (Chrome/Edge) once per workstation. After that one-time step, every future **Generate Passdown** click opens directly in Outlook with no extra click. A first-time on-screen tip now reminds the user of this the first time they generate a passdown on a given browser.

**"Always open" does nothing at all (2026-11-09 finding):** on a freshly-checked workstation, Windows had *no* default app set for `.eml` at all (`HKCU...\FileExts\.eml\UserChoice` was empty), even though Outlook was installed and registers a valid handler (`Outlook.File.eml.15`). Without a Windows-level default, the browser's "Always open" setting has nothing to hand the file to, so it silently keeps downloading instead. Fix once per workstation, from File Explorer (Windows blocks scripts/registry edits from silently setting this, by design, so it has to go through the real UI): right-click any downloaded `.eml` → **Open with** → **Outlook** → check **"Always use this app to open .eml files"** → OK. Only after that will the browser's "Always open" setting actually work.

**Why a generated draft can sit in Outlook's Outbox:** once a `.eml` is opened and Send is clicked inside Outlook, whether it actually leaves the Outbox right away depends on that workstation's own Outlook configuration (Send/Receive settings, Work Offline state, and which application Windows has associated with `.eml` files) -- none of which V3.1's backend can see or control once the file has left the browser. Confirm each workstation's `.eml` file association points at the full Outlook application (not a lightweight viewer) and that Outlook's "Send immediately when connected" setting is on. A true zero-dependency send (no Outlook involved at all) would require a server-side Microsoft Graph or SMTP relay integration; that was evaluated and intentionally not pursued (see `v31_facility.py`) and remains out of scope here by explicit choice, not oversight.

### V3.1 Shift Report workspace (October 2026 refresh)

Cetaris import status, **Import Cetaris**, **Change Technicians**, and **Import Audit** are now grouped next to the Shift Report title. The Shift Progress bar is a live, equally-weighted checklist recomputed on every render, not a one-time flag or elapsed time: (1) Cetaris import confirmed, only required if a file was actually chosen -- a fully-manual shift with no Cetaris file at all is legitimate and is never blocked by this item; (2) work orders or PMs recorded for the shift; (3) LOTO verification completed (marking any key missing also counts, since documenting a missing key is itself a completed inspection). Reaching 100% is required before **Generate Passdown** will proceed -- it is a gate, never a side effect of generating the report itself.

The red Cetaris button starts XLSX/CSV staging; **staging is not the same as importing**. A selection dialog opens immediately. It prechecks uniquely matched active Drax associates assigned to the selected shift, irrespective of their Job Title or who opened the report; it displays other-shift and unmatched/ambiguous technicians for explicit review. Reimports preserve deliberately deselected technicians while automatically checking newly encountered assigned technicians. Manually selecting an additional/overtime technician **does not change login permissions or automatically establish WO ownership**. Confirm **Import Selected Work Orders** to persist the work. Missing or ambiguous completion evidence stays visible in Import Audit. The app now distinguishes an unconfirmed staged replacement file from an imported file, preserving earlier saved WOs.

The sidebar now uses the genuine Walmart 2025 vector artwork (official shapes and colors), with IND3 and MST Reliability Operations separated into readable rows. This change does not alter V3, the historical archive, or Drax workbooks.

## Shift Report — Cetaris import (Phase 1)

The existing approved Shift Report UI is wired to the new, private V3.1 database.

1. Sign in as an authorized active Drax associate for your assigned shift, or select an eligible shift as management.
2. **Import Cetaris** accepts validated XLSX or CSV work-order exports, bounded to 12 MB / 30,000 labor rows. Staging the file **does not yet add work orders**.
3. Select the actual technicians present in the export. V3.1 imports only the selected technicians' rows and uses the reviewed V3 actual-work attribution engine. Drax shift assignments resolve overlapping work when technician identity matches, without assuming that selecting a technician proves ownership.
4. **Shift Happenings** groups selected labor by WO number, preserves individual remarks and combines multiple technicians, and uses the Cetaris Service/Repair Description as the displayed cause. Preventive-maintenance WOs are counted once and excluded from normal happenings.
5. Closed work orders are included; Completed-but-not-Closed non-PM WOs are excluded pending review. Use **Import Audit → Review completed work orders** to add a closure reminder, or leave it ignored. A reminder is labeled **PENDING**, never falsely RTS.
6. **Breakdown OOS** is tracked from a qualifying In Process breakdown. A later Closed WO may transition to RTS in another eligible shift without overwriting the original OOS record.
7. Edit, exclude, and restore are persisted. Reimporting a file merges WOs by number, preserves manual edits, and leaves previously imported WOs intact. Missing or ambiguous labor timestamps remain in the Import Audit rather than receiving guessed shift attribution.
8. Vendors, RO entries, notes and LOTO draft values autosave in the selected shift database. Creating the Outlook EML saves the current passdown to the selected shift; **End Shift requires a saved report**. Emails are not sent by the app.

**Current review implementation:** The separate **PMs Needing Completion** CSV/Excel/PDF/image upload is stored privately and appears in the report; spreadsheet rows are parsed, while PDF/image files remain clearly labeled reference attachments. Manual proactive/PM discoveries, carry-over adoption, breakdown confirmation/holds, historical EML snapshots, and read-only analytics are connected. Optional AI suggestions require an approved local model already present and never download one automatically. Cetaris imports still require a user-supplied export, and production identity verification and company-network rollout are not implemented. Continue using the existing live Workshop V3 workflow until the Windows pilot and operational acceptance checklist are approved.

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
C:\Users\ins0002\OneDrive - Walmart Inc\Drax Associates.xlsx
\`\`\`

- **Drax -> V3.1:** Existing \`v31_roster.py\` refreshes the private roster cache from the authorized workbook about every 5 minutes. Changes made in Excel/OneDrive appear after the next successful read.
- **Drax -> old V3:** The old V3 program already watches the workbook. Its exact handling of administrative overrides and MST demotions depends on its legacy database rules; do not assume every changed cell overwrites prior manual V3 overrides.
- **Old V3 -> Drax:** New \`v31_drax_bridge.py\` compares the old V3 \`associates\` database (read-only), the workbook, and its last private baseline. Nonconflicting changes to \`Name\`, \`Shift\`, and MST flag can be written to the Excel file only with **explicit authorization and a write flag**. Workbook differences on first setup establish a baseline, and conflicting changes remain for human review. Deletions/new associates are not auto-mirrored.
- **V3.1 Administration -> Drax (approved 2026-11-09 for this facility's deployment):** Shift reassignment, MST toggle, and name edits made in Administration now also write immediately into the same live Drax workbook, in addition to saving locally -- see \`v31_drax_bridge.write_admin_override()\`. It reuses the exact same guarded single-cell primitive as the bridge above (before-image backup, only the changed cell, aborts cleanly instead of overwriting if the workbook changed on disk mid-write). A manually-added local-only associate has no real Drax row and is never written back. This write path shares the same \`IND3_V31_DRAX_WRITE_ENABLED=1\` gate as the CLI bridge below -- disable that flag to fall back to local-only overrides again.
- **Administration -> Delete (approved 2026-11-09):** Administration now has a Delete action per associate, permission-gated the same as every other roster edit (management session required). For a manually-added local-only associate it's a plain, permanent local delete (blocked if they're referenced by real shift history). For a real Drax-sourced associate it actually removes their row from the live Drax workbook itself -- see \`v31_drax_bridge.delete_admin_row()\` -- since a local-only delete would just have them resync right back within 5 minutes. Unlike the softer field edits above, a failed Drax delete is reported as a hard error, not a silent local-only fallback: there is no safe partial state for "deleted here but not there." Row deletion shifts every subsequent row up by one, a materially bigger change than a single-cell edit, so it still shares the same before-image backup and concurrent-change abort, but is inherently riskier -- treat it with the same care as editing the workbook by hand.

Review only (does not edit Drax):

\`\`\`powershell
python scripts/sync_drax_from_v3.py --v3-db "C:\approved\old-v3\dbs\ind3.db"
\`\`\`

**Do not enable the following on a live company OneDrive workbook without approved IT authorization, an exclusive maintenance window, a verified backup and actual Windows/OneDrive acceptance testing.** If approved, the write setting is \`IND3_V31_DRAX_WRITE_ENABLED=1\`, and \`--apply\` enables the manual CLI write (this same flag also gates the live Administration-page write-back described above). The optional background bridge also needs \`IND3_V31_LEGACY_DB_PATH\` explicitly set to the old database file. The bridge never writes to the old V3 database. Each proposed workbook write creates a private before-image backup and checks for changes during preparation. OneDrive sync conflicts or Excel file locks still require manual recovery.

This is a **limited, guarded two-way synchronization bridge, not an unconditional real-time bidirectional merge**. It does not change original V3's code or guarantee that every administrative override in V3 is immediately reflected in Excel.


## Development boundaries and security

- **Associate number alone is not secure employee identity verification.** This prototype is **localhost only**; do not deploy on the Walmart facility network, open a tunnel, or enable public access. Enterprise SSO and proper authorization are required before real deployment.
- Any official roster or Cetaris data must be handled only through approved company systems and locations. Never commit such data or secrets to this public repository.
- The Shift Report starts with **zero sample work orders**, but real Cetaris XLSX/CSV parsing and shift-scoped work-order persistence are now connected. Generated local EML files are not sent; the report is not yet production-certified. PMs Needing Completion spreadsheet parsing is connected; optional local AI requires an approved model and may be unavailable on restricted devices.
- The original Workshop app, repository, and database remain untouched.

See [login and shift-lifecycle audit](docs/V31_LOGIN_AUDIT.md) for the comparison matrix and remaining environment-dependent verification.
