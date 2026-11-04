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

## Special decisions still pending
- **MST on-site question**: User proposed asking whether an MST is onsite before a non-MST starts a report. Not implemented: a self-reported Yes/No does not establish real staffing or authorization. Decide who verifies staffing, when to ask, what happens if staffing changes midshift, and whether it is informational or blocking.
- **MST approved overtime vs strict home shift**: PR #14 allows different-shift access ONLY with a date/shift-specific management grant and ONLY inside that reporting window. It never permits takeover of an already-owned off-shift report. All routine access remains home-shift-only; review operational approval policy before merging.
- **Ownership transfer**: Same-home-shift MST can explicitly take over from non-MST, with owner and audit updates. Other associates stay locked out and the previous owner loses edit rights. Onsite confirmation/audit-view design is pending.

## Source trail
- V2/V3: [main.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/main.py), [v3_only.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_only.py), [v3_routes.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_routes.py), [v3_split.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_split.py), [v3_ollama_background.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_ollama_background.py), [v3_breakdown.py](https://github.com/zak-isaac/ind3-shift-passdown/blob/main/v3_breakdown.py).
- V3.1: [app.py](https://github.com/zak-isaac/v3.1/blob/main/app.py), [v31_roster.py](https://github.com/zak-isaac/v3.1/blob/main/v31_roster.py), [v31_reporting.py](https://github.com/zak-isaac/v3.1/blob/main/v31_reporting.py), [v31_split.py](https://github.com/zak-isaac/v3.1/blob/main/v31_split.py), [web/shift-data.js](https://github.com/zak-isaac/v3.1/blob/main/web/shift-data.js), [prototypes/shift-report.html](https://github.com/zak-isaac/v3.1/blob/main/prototypes/shift-report.html), [v31_oos.py](https://github.com/zak-isaac/v3.1/blob/main/v31_oos.py).
- Draft change: https://github.com/zak-isaac/v3.1/pull/14
- No real employee names, roster content, equipment inventory, credentials, or work-order exports are included in this public review document.
