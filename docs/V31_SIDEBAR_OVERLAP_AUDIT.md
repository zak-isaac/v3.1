# V3.1 Sidebar and Shift Overlap UI Audit

- Repositions Walmart IND3 branding into the left sidebar, with a compact MST Reliability Operations top header.
- Removes Support from visible navigation and the unused view mapping.
- Relocates selected shift date, scheduled hours, reporting window, current time, Manage Drax Roster (management only), Choose Shift, and Sign Out to the left sidebar.
- Keeps the signed-out screen capable of showing two eligible reports even when **both are already open**. Supports S1+S2 and S4+S5 active overlaps, and S5 (previous day) + ready S4 on the next morning.
- Sign Out ends only the session cookie and leaves all shift reports open; the login screen then refreshes all active and ready shifts.
- Selecting a different shift within the report refreshes its work-order data rather than showing the previous shift's information.

All regressions use private synthetic records; production V3 and Drax are unchanged. Browser layout remains to be verified on Mac at 100% zoom.
