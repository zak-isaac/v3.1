# IND3 Shift Passdown — V2 / Current V3 / V3.1 End-to-End Parity Audit
**Date:** 2026-11-04 (Indianapolis)  
**Scope:** GitHub source review and GitHub Actions CI. No real Drax, Cetaris, production WorkShop database, or facility browser used.  
**Candidate branch:** feature/drax-shift-access-mst-takeover-20261104, draft PR #14. No merge/deployment.

## Version distinction (important)
- **Historical V2** lives in ind3-shift-passdown/main.py and its older /tech, /lead, /shift handlers. The current V3 production-style server registers v3_only.enable_v3_only(), which retires legacy operational V2 routes. Do **not** regard those V2 endpoints as currently callable.
- **Current V3:** ind3-shift-passdown/v3_routes.py, v3_identity.py and V2 report/DB functions retained internally through main.py and db.py.
- **V3.1:** separate localhost-only application, app.py + v31_roster.py + v31_reporting.py + browser assets. Its private shifts.sqlite3 is NOT the live V2/V3 DB. It can read a validated archival snapshot.
- Repository links: https://github.com/zak-isaac/ind3-shift-passdown and https://github.com/zak-isaac/v3.1

## User journey / backend map

| Step | V2 historical | Current V3 exposed workflow | V3.1 review branch | Parity |
|---|---|---|---|---|
| 1. Entry screen | Legacy home: start shift, tech/lead paths | GET /v3/login renders Jinja2 login; signed session in v3_identity.py | GET / injects auth-ui.js into login-flow.html; /api/entry advertises open/ready shifts | Different UI, same general intent |
| 2. Identity + roster | associates.py loads Drax; tech versus MST path | POST /v3/login: Drax-derived roster, MST-only operational login; protected management overrides | POST /api/login + v31_roster.profiles(): active Drax associates with S1–S5, irrespective of Job Title; MST role remains separate | Deliberate expanded access, SSO absent |
| 3. Schedule and shift start | Legacy /shift/start, tech/lead; early/late rules | Facility S1–S5 shifts, one-hour early/late windows, two-active-shift cap; start under own associate number | eligible_date(), shift_choices(), signed cookie and private shifts table; same schedule/cap; management picker; dated manager-approved MST OT on PR #14 | Broad parity; OT exception is new |
| 4. Ownership | Separate historical tech and lead routes | v3_identity.owns + secure(): active-shift opener or authorized manager | _report_access() and shift owner; draft PR #14 adds explicit same-home-shift MST relief of technician-started report with shift_owner_transfers audit; cross-shift takeover prohibited even with OT grant | New exception, must test onsite |
| 5. Cetaris upload | /tech/cmms-upload supports historical CMMS files | POST /v3/import validates workbook CONTENT, creates 24-hour staged token, does not immediately write WOs | POST /api/reporting/stage accepts XLSX/CSV, 12MB limit, persistent report_stage per shift, staged vs imported distinguished | Mostly ported; V3 filename leniency lost in browser |
| 6. Technician selection | Legacy work feed/individual records | Drax shift matching (not solely MST flag), auto-checkbox assigned names; manual additional selection | v31_reporting.technician_suggestions() currently only checks active Drax MST matches for auto-preselection; all others are unchecked even if assigned to the shift | **Regression** under new all-Drax login rule |
| 7. WO attribution | Earlier CMMS/entry workflow | v3_split.assign(): verified technician completion, roster continuity, shared shifts, S3/S5 overnight; admin close alone not WO ownership | v31_split.assign() retains core engine; adds 30-minute S3 postshift completion grace, v31_cetaris adds date format | Strong reuse plus deliberate improvement |
| 8. Import & re-import | Persistent entries, multiple tech records | V3 entries + v3_entry_meta/source_snapshot/audit; change technician selection hides/restores their records and PMs; changed incoming content raises review instead of silent overwrite | report_wo UNIQUE(shift_id,wo), report_pm/report_audit; preserves explicitly edited rows and older WOs, but does not reconcile deselection or flag altered incoming source as V3 did | **Partial/regression** |
| 9. Ignored/ambiguous WOs | Earlier manual review | V3 attribution evidence snapshot + v3_import_review include/dismiss actions, explicit per-WO review | report_audit pending/ignored/next-day JSON, S3/S5 evening-morning UI, completed/closure choice; no equivalent general include/dismiss endpoint or per-WO source-signature audit | **Partial/regression** |
| 10. Breakdown/OOS/RTS | Shared global OOS state and RTS log | V3 v3_breakdown.sync + hold/suppress/review/confirm, manual OOS/RTS, cross-shift status continuity | Cetaris OOS/RTS in report_wo plus separate global manual_oos table, allowing another shift owner to resolve; no equivalent unified v3_breakdown workflow | **Partial** |
| 11. Manual Happenings and carry-over | Tech manual entries, carry-over actions | /v3/manual; /v3/carry-over/{entry_id}/adopt and /dismiss | No manual WO/Happening creation endpoint; no comparable carry-over adoption/dismissal | **Missing** |
| 12. Pending PM file | V2 PM-file upload + parsing | /v3/pm-upload stores approved PM files, parses pending PM XLSX/CSV into pending state | Pending PM button only sets browser filename with 'preview only'; no persistent parsing/API; pending-PM report says unavailable | **Missing** |
| 13. Vendor, RO, LOTO, notes | Structured saved sections | /v3/vendor/add/delete; /v3/loto; /v3/notes; vendor name+notes and shift-scoped data | /api/reporting/draft saves vendor/RO strings, notes, 20 missing keys, LOTO. More lightweight, fewer structured details. UI prevents verified+missing combination but server does not validate it. | Partial |
| 14. AI and spelling | Earlier local Ollama rewrite engine | v3_ollama_background worker can queue/check/approve valid rewritten WO remarks; spelling_fix preserves source | spelling_fix and ai_remark_rewriter helper exist; v31_reporting request/accept/reject helpers exist but no exposed UI/API or running worker | **Spelling yes, AI not integrated** |
| 15. Preview & export | Report in DB, preview, EML/DOCX/PDF options | /v3/generate reuses server lead_generate to save report; /v3/report, EML, PDF/DOCX exports protected; report state is stored | Browser createReport()/buildEmailHtml() generates plain-text server-saved report and a client-side EML file. /api/history/shifts/{id}/eml rebuilds HTML from current persisted state | **Major structural difference / potential drift** |
| 16. End Shift | Legacy close report routes | /v3/end requires a saved generated report then closes only that shift | /api/shifts/end requires report_saved_at and owner/manager; PR #14 also invalidates saved passdown after manual OOS/RTS updates | Core parity, stronger manual OOS invalidation |
| 17. Post-shift history | Historical reports & analytics in shared DB | /v3/history, /v3/analytics, archive EML | /api/history/shifts + UI, /api/history/legacy API for copied V2/V3 archive; historical V2/V3 not visible in new UI; Analytics tab is placeholder | Partial/missing |

## High-priority gaps before V3.1 can replace V3

### P0 — security, ownership and report integrity
1. **Identity verification**: both the V3/V3.1 associate-number flow and manager preview identifiers are not enterprise authentication. V3.1 deliberately runs loopback-only. Do not expose to facility network without approved SSO/TLS and security review. Under Option A, ALL active assigned Drax associates get access; confirm this is an approved facility policy.
2. **Saved report ≠ archived EML**: V3.1 stores the plain-text report and generates HTML EML in browser. Historical EML rebuild is a second Python implementation and may differ from the original, especially global/manual OOS, later data changes, and format drift. Persist the exact generated report/EML snapshot with version and hash; assert preview/export/history equivalence.
3. **Draft-saving failure can be swallowed**: web/shift-data.js saveDraft() catches request errors and flushDraft() awaits that function. A failing draft save need not prevent frontend EML from being saved/downloaded, and archived HTML can differ. Make draft flush fail-closed for final report and cover in browser tests.
4. **Browser tests not verified by CI**: three browser modules skip in Actions without Playwright. Add a separate Playwright job with browser installation for login->Cetaris->EML->End Shift and ownership race tests.
5. **PR #14 fix already applied**: manual OOS create/list/RTS now use _report_access() and cannot bypass shift ownership or clock; manual OOS/RTS changes invalidate a previously saved passdown. Synthetic regression tests cover both.

### P1 — V3 functionality regressed or not yet recovered
6. **Drax technician preselection**: broaden to all active uniquely matched, assigned Drax associates, not just MST, while preserving duplicate/ambiguous safeguards and user-selected exceptions.
7. **Deselect / re-import reconciliation**: V3 actively hides/restores WOs/PMs when a technician is removed and reviews changed incoming data; V3.1 retains existing WOs, including WOs from technicians no longer selected. Define reimport expected behavior and add same-file/new-file/deselection/PM tests.
8. **Import audit actionability**: restore appropriate V3 per-WO source evidence, non-destructive changed-source review, and reviewed include/dismiss workflows for recoverable ambiguous WOs.
9. **Pending PM workflow**: actually parse and save the separate pending-PM report with provenance, not just local filename state. Include pending PMs in the passdown.
10. **Carry-over and manual WO entries**: restore /v3/manual equivalent and adopt/dismiss prior-shift carry-over, with double-credit safeguards.
11. **Breakdown reconciliation**: unify Cetaris-imported and manual global OOS states, manual holds, confirmed RTS, and cross-shift progression; prevent duplicate concurrent manual OOS records for the same asset unless intentionally distinct incidents.
12. **Historical reports**: surface legacy V2/V3 archive in new Shift History with read-only boundaries; preserve current shift owner changes and original opener in visible audit.
13. **Structured vendor data and LOTO server validation**: don't lose V3 vendor notes/provenance; enforce 'verified' incompatible with missing keys on backend.

### P2 — elevate beyond V3 after parity
14. Wire and validate the existing AI rewrite helper using approved local Ollama, factual checks, and source fallback; don't label this connected until a real end-to-end test passes.
15. Add analytics and relevant PDF/DOCX exports if operationally needed; do not expose deprecated V2 write routes.
16. Improve staging provenance, UI error messages, and explicit one-hour grace/cross-midnight tests using synthetic records and a same-input V3/V3.1 parity harness.

### Additional frontend integration findings
- **End Shift has two UI handlers:** web/workspace-auth.js installs a capture-phase listener with stopImmediatePropagation() and a native confirm dialog, so the styled confirmEnd() modal in prototypes/shift-report.html generally does not run. This is explicitly documented by tests/test_browser_end_shift.py. Consolidate to one approved confirmation flow and execute it in real-browser CI.
- **Overtime screen behavior needs browser acceptance:** management grant is verified by the API and backed by the dated grant table, but direct first-screen login can automatically enter an eligible overtime shift. Confirm users can deliberately select/decline an approved off-shift report when home and overtime windows overlap; do not silently make a cross-shift report the default.

## Special decisions still pending
- **MST on-site question**: User proposed asking whether an MST is onsite before a non-MST starts a report. Not implemented: a self-reported Yes/No does not establish real staffing or authorization. Decide who verifies staffing, when to ask, what happens if staffing changes midshift, and whether it is informational or blocking.
- **MST approved overtime vs strict home shift**: PR #14 allows different-shift access ONLY with a date/shift-specific management grant and ONLY inside that reporting window. It never permits takeover of an already-owned off-shift report. All routine access remains home-shift-only; review operational approval policy before merging.
- **Ownership transfer**: Same-home-shift MST can explicitly take over from non-MST, with owner and audit updates. Other associates stay locked out and the previous owner loses edit rights. Onsite confirmation/audit-view design is pending.

## Source trail
- V2/V3: [main.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/main.py), [v3_only.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_only.py), [v3_routes.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_routes.py), [v3_split.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_split.py), [v3_ollama_background.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_ollama_background.py), [v3_breakdown.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_breakdown.py).
- V3.1: [app.py](https://github.com/zak-isaac/v3.1/blob/main/app.py), [v31_roster.py](https://github.com/zak-isaac/v3.1/blob/main/v31_roster.py), [v31_reporting.py](https://github.com/zak-isaac/v3.1/blob/main/v31_reporting.py), [v31_split.py](https://github.com/zak-isaac/v3.1/blob/main/v31_split.py), [web/shift-data.js](https://github.com/zak-isaac/v3.1/blob/main/web/shift-data.js), [prototypes/shift-report.html](https://github.com/zak-isaac/v3.1/blob/main/prototypes/shift-report.html), [v31_oos.py](https://github.com/zak-isaac/v3.1/blob/main/v31_oos.py).
- Draft change: https://github.com/zak-isaac/v3.1/pull/14
- No real employee names, roster content, equipment inventory, credentials, or work-order exports are included in this public review document.


## Additional confirmed audit findings (source-level, not facility-tested)

### P0 — PENDING work-order status can become RTS unintentionally
- In `prototypes/shift-report.html`, `editRow()` always sends a `status` from a dropdown with only **RTS** and **OOS** options. The import engine legitimately stores **PENDING** for a non-PM `Completed` WO awaiting Cetaris closure. When the form opens for a PENDING record, neither option is selected; the browser defaults to the first option (**RTS**). Saving an unrelated remark or asset edit can therefore mark the WO RTS without a positive completion-status decision.
- Fix: represent PENDING explicitly and preserve the original value on unrelated edits; require a distinct documented status action to move from PENDING/OOS to RTS; add real-browser regression tests and independent backend transition guards.
- Source: `prototypes/shift-report.html` `editRow()`; `v31_reporting.py` `import_selection()` and `update_wo()`.

### P0 — Final report can be saved without server-verified report content
- `POST /api/shifts/report` accepts caller-supplied text if it includes `IND3` and `SHIFT HAPPENINGS`, regardless of whether the saved text faithfully includes the qualifying WOs, pending closure, OOS/RTS, LOTO, or PMs currently in the database. The backend then marks `report_saved_at`, enabling End Shift. This does **not** establish that the report is accurate, and the synthetic E2E test explicitly saves an arbitrary short placeholder.
- Fix: server renders and persists a canonical, immutable passdown snapshot from the shift's persisted data, validates required workflow checks, and ties preview/EML/History/End Shift to that snapshot version. Preserve original content, creation actor, and a digest for audit.
- Source: `app.py` `save_shift_report()`, `end_shift()`; `tests/test_e2e_shift_workflow.py`.

### P1 — Work timestamp is stored but not displayed in the current WO detail/report
- `v31_reporting.py` stores `work_at` for an imported WO, yet `prototypes/shift-report.html` `applySnapshot()` drops it and the detail/preview/EML do not show its evidence timestamp. V3 has an explicit WO work-time label/source in its model.
- Fix: display a clearly labeled actual work-evidence time and source, keeping it distinct from Cetaris administrative WO closure time, without inventing timestamps.

### P1 — Shift may expire without a generated passdown
- `expire_shifts()` automatically closes a shift one hour after its scheduled end even if `report_saved_at` is empty; unlike a successful user-triggered End Shift, that closure leaves an incomplete/unreviewed passdown. This may be intentionally safer than an always-open session, but operations need an explicit exception queue, owner/manager alert and recoverable close-out process.
- Do not automatically invent or send an incomplete passdown.

### P1 — Test assurance gap
- The CI job now runs all Python tests but the Playwright modules remain skipped unless installed/configured. Some login-to-end unit tests contain permissive assertions (e.g., `or True`) that fail to enforce no-reopen guarantees. Add real browser CI and assertions that can actually fail.
- A passing CI suite alone is **not** an onsite user-acceptance test.

### Audit policy
- These are *identified issues*; no production change or merge is authorized by this report. Source-based risks should be reproduced with synthetic test fixtures before changing business rules, then validated on an approved staging environment.
