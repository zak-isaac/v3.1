# IND3 V3.1 — Independent Shift Reporting (Local Preview)

This project is entirely separate from the live Workshop V3 application and `zak-isaac/ind3-shift-passdown`. It does not connect to that system's database or modify production.

## Login behavior — restored from the original V3 policy

**First Sign In screen**

- **MST associates:** Their associate number is read from this project's private, authorized Drax-style roster, using `Assoc. #`, `Job Title` and assigned `Shift`. Only rows with `Job Title = MST` and shift `S1`–`S5` can log in. Within the shift's reporting window, signing in starts or resumes that associate's assigned shift **and opens Shift Report with Shift Happenings selected**. No second shift selection screen.
- **Management identifiers 8972 and 2759:** Signing in always opens a centered **Choose Your Shift** dialog, listing S1–S5 with Start, Resume, or Unavailable status. Management selects a shift before entering its workspace.
- The original V3 **starter ownership rule** is retained for MSTs: only the associate who opened a shift or management can resume/end it. Another MST cannot silently claim someone else's report.

**Second / Third screens:** Active Shift Detected and New Shift Ready still show the approved photos and designs. Clicking **Resume** or **Start** opens an associate-number dialog **on the same screen**. The numeric validation expression has been fixed, and the backend checks the clicked shift and permission before navigation. It never creates a different shift just because someone is signing in from a popup.

**100% viewport:** The three screens now share `web/login-layout.css`; widescreen desktop is fitted to the viewport, while shorter and mobile screens can scroll vertically without clipped cards. We preserve the original first-screen style.

## Private roster setup (MST only)

**No real MST associate numbers or employee rosters are stored in GitHub.** Manager preview access works without a roster; MST login needs an authorized local roster.

If permitted under your employer's data-handling rules, choose either:

1. Place an **approved** Drax-format workbook at `.local-data/Drax Associates.xlsx` or `.local-data/mst-roster.xlsx`. Columns: `Name`, `Assoc. #`, `Shift`, `Job Title`. The app reads only valid MST rows, and it never edits the workbook.
2. Configure a minimal local roster by running `python setup_mst_roster.py`, then entering only authorized real MST associate numbers and their assigned S1–S5 shift. It saves `.local-data/mst-roster.csv`. No dummy accounts are seeded.

An optional `IND3_V31_ROSTER_PATH` environment variable can point to an approved local `.csv` or `.xlsx` instead.

If an MST is missing from the local roster, their login will be rejected until the authorized roster is configured. The two management override identifiers work independently of that roster.

## Run on your Mac

```bash
cd ~/Projects/ind3-v3.1
git status --short
git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
python setup_mst_roster.py
python -m uvicorn app:app --host 127.0.0.1 --port 8980
```

Open <http://127.0.0.1:8980/>. Stop the server with Control+C before updating.

## Reporting windows

All timestamps are Indianapolis local time. A report may start up to **one hour before** the scheduled start. An existing report may be resumed up to **one hour after** scheduled end (but a brand-new report cannot be started in that closing grace hour).

| Shift | Shift start days | Scheduled period |
| --- | --- | --- |
| S1 | Mon–Thu | 5:30 AM–4 PM |
| S2 | Mon–Thu | Noon–10:30 PM |
| S3 | Mon–Thu | 8:30 PM–7 AM next day |
| S4 | Fri–Sun | 5:30 AM–6 PM |
| S5 | Fri–Sun | 5:30 PM–6 AM next day |

Overnight reports remain associated with their **starting date**. End Shift closes only the selected report. Sign Out does not close it. Overlapping reports are independent.

## Current limits and security

- Associate-number-only login is **not verified identity**. Restrict to localhost for private development, **never** publish on the company network, Internet, public tunnels or reverse proxies. Before team rollout, implement approved enterprise authentication and authorization.
- `.local-data/` and all private roster files are ignored by Git. Never commit actual associate data, Cetaris work orders, or confidential reports.
- The new Shift Report is an **empty frontend** until Cetaris backend ingestion is built: no sample work orders, PM counts, technician names, vendors or RO entries. File-selection UI doesn't parse data yet. The canvas is white; dashboard blue overlays are removed.
- Tests cover timing, overlapping sessions, manager and MST routing, ownership checks, empty work-order state, numeric popup validation, and presence of viewport rules. Browser layout must still be verified on your Mac.
