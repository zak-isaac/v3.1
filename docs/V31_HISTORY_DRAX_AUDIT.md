# V3.1 V2/V3 History and Drax Bridge Audit

This audit verifies the implementation that preserves previous V2/V3 shift records and handles Drax synchronization.

## Invariants

- Source `dbs/ind3.db` is read-only, never edited or renamed.
- A copy of the entire V2/V3 SQLite schema is stored privately in `.local-data/legacy-v2-v3-archive.sqlite3`.
- Historical shift IDs, report IDs, maintenance records and ancillary tables are preserved.
- New V3.1 login events are stored separately from historical shift-start events.
- The shared Drax workbook is read-only by default, including the bridge's first baseline.
- The V3-to-Drax write path needs explicit operator approval/opt-in and creates a private workbook backup.
- Drax conflicts or absent associates are held for review rather than overwritten.
- A default `ASSOCIATES_PATH` / canonical OneDrive workbook source continues to work.

Tests use synthetic data only. Do not upload real employee rosters or production databases to GitHub. A successful test does not constitute authorization to write to a production OneDrive workbook.
