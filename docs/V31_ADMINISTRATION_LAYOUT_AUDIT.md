# V3.1 Administration and Fixed Sidebar Audit

The redesigned workspace keeps the sidebar stationary (no sidebar scrolling at desktop sizes), moves MST identity directly above Sign Out, and places management-only shift switching in the top account menu. Manage Drax is removed from the sidebar and is accessible inside a full Administration page rather than a popup. The dashboard includes roster totals, Drax synchronization status, searchable associates, shift/MST permission controls, reset options, and local associate additions. Management can enter `/administration` before starting a shift; the server checks manager permissions and prevents ordinary MSTs from accessing roster administration. Production Workshop V3 is unchanged.

UI regression tests inspect the HTML, CSS, JavaScript wiring and backend authorization. Real-browser visual checks on Mac at 100% remain necessary.
