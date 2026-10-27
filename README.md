# IND3 Shift Reporting V3.1 — Associate-number login (local preview)

A **new, independent** frontend and backend, separate from live IND3 Workshop V3.

Enter an authorized associate number on the approved sign-in screen. With one eligible shift, it opens **Shift Report** automatically. When multiple reports overlap, select the one you want first. Both local preview identities have access to the S1–S5 reporting shifts within their time windows. You can start eligible shifts and resume open ones from **Choose Shift** in the workspace.

## Start on your Mac

```bash
cd ~/Projects/ind3-v3.1
git pull --ff-only
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8980
```

Open <http://127.0.0.1:8980/>. **No passphrase, setup wizard, passkey, or fictional accounts.** The old `setup_access.py` was deleted.

### Remove old passphrase setup from this Mac

After stopping the server (Control+C), you may remove the now-unused local hash/roster file with:

```bash
cd ~/Projects/ind3-v3.1
rm -f -- .local-data/users.json
```

This does not delete the isolated shift-session database. If you want to sign out an existing browser session, click **Sign Out** in the app.

## Current frontend behavior

Clicking **Resume Active Shift**, **Resume Existing Shift**, or **Start New Shift** on the second and third approved login screens opens a centered **associate-number popup on that same screen**. Successful confirmation selects the requested shift and opens Shift Report. A failed entry stays in the popup with an error. The first sign-in screen is not used as an intermediate step.

The Shift Report now starts with **zero work orders, PM counts, breakdowns, vendor entries, RO entries and technician selections**. LOTO starts unverified. No sample work orders, invented technician names or imported Cetaris filenames appear by default. The main canvas and KPI cards use true white, and the previous blue-tinted warehouse-photo backgrounds have been removed from the dashboard (the approved login photography remains).

Cetaris and PM uploads are still UI-only file selection until the new V3.1 parsing backend is implemented. A selected filename does **not** imply imported work orders. Report preview/EML is still local demo output, not a production passdown.

## Security and scope

- **Associate number alone is not secure authentication.** This deliberately simplified login is **only for local frontend testing** on a computer you control. Anyone with access to the localhost app can use those numbers. Do **not** deploy to the Walmart network or Internet. Before team rollout, integrate approved identity verification and proper authorization.
- Keep the server bound to `127.0.0.1`, **never** `0.0.0.0`, and do not use public tunnels or reverse proxies. A loopback request restriction is also enforced server-side.
- New V3.1 has its own local shift database. Existing production GitHub project, Workshop application, and database remain untouched.
- Shift Report content and controls are still a **sample frontend**, not connected to live Cetaris, reporting, or actual work orders.
- `.local-data/` contains local session signing key, and isolated shift records; it is ignored by Git.
