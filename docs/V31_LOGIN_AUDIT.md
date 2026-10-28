# IND3 V3.1 — Login, Drax and shift-lifecycle audit

Audit focus: new `zak-isaac/v3.1` only. The original Workshop V3 remains unchanged.

## Verified implementation points

| Area | Implementation | Key regression |
| --- | --- | --- |
| Drax reading | Authorized .xlsx or .csv, original header mapping | `test_workbook_excel_source_format` |
| Independent roster cache | Private SQLite, normal refresh every 5 minutes and manual reload | `test_drax_reload_inserts_updates_and_disables_removed_rows` |
| Manager admin overrides | MST flag, shift, display name, manual add, protected numbers | `test_manager_edit_survives_drax_reload` |
| First-screen MST | Validate Drax assignment and bind own shift | `test_mst_goes_directly_to_roster_assigned_shift` |
| First-screen management | Select eligible S1–S5 after association | `test_manager_first_signin_always_requires_shift_popup` |
| Active and handoff screens | Popup identity check, chosen shift ID | `test_login_popup_regex_and_manager_selector` |
| One-hour boundaries | Facility clock, S3/S5 next-day handling | `test_time_window_one_hour_before_after_and_overnight` |
| Two active reports maximum | Reject third start, even if three windows become eligible | `test_two_active_shift_limit_even_if_three_windows_overlap` |
| OT | Management-granted date/shift, recorded `is_ot`, revoke | `test_overtime_grant_and_revocation` |
| End Shift | Require server-saved report; close selected report only | `test_report_required_before_ending_and_saved_in_correct_shift` |
| Auto close | Startup, request-time and every minute after end + 1 hour | `test_late_closure_auto_expires_and_preserves_unsubmitted_history` |
| No fabricated work orders | Empty Shift Happenings on first load | `test_zero_sample_maintenance_data_in_report` |

## Remaining environment-dependent verification

1. An approved Drax workbook must be connected to V3.1 on the actual workstation (environment path or private local sync copy). No real employee roster belongs in this public repository.
2. Workstation-browser layout at 100% zoom and the complete graphical flow must be confirmed on the Mac, since HTML/JS regression checks do not test browser rendering.
3. Associate-number-only access is a **local-only development convenience**, not verified enterprise authentication. Do not expose the backend to the facility network until approved SSO/authorization is installed.
4. Generated reports currently use the local preview frontend, not real Cetaris ingestion. End Shift can require saving a report, but that does not mean the reporting engine is production-ready.

CI runs `pytest` for both login and lifecycle test modules and Node syntax checks for all three active JavaScript files.
