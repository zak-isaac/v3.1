# IND3 Shift Reporting V3.1 — Login Connected

This is a **new, isolated project**, not the live Workshop V3 application.

The approved login and Shift Report frontend designs are preserved. Signing in with an authorized associate number and a **private access passphrase** opens the Shift Report landing page directly. A shift picker inside the workspace lets management open any eligible S1–S5 shift without closing overlapping reports.

## First-time setup

```bash
pip install -r requirements.txt
python setup_access.py
python -m uvicorn app:app --host 127.0.0.1 --port 8980
```

Open <http://127.0.0.1:8980/>. Provision your two authorized management identifiers during `setup_access.py`, marking both **Full S1–S5 access**. They are **not** embedded in the public repository and no mock accounts are provisioned by default.

The login screen first requests associate number, then asks for the locally provisioned passphrase in a matching modal. Successful login goes **straight to the Shift Report landing page**. On the landing page, **Choose Shift** selects an active/eligible reporting session. Sign Out does not close a shift.

## Important limits

- Loopback-only developer service. Do not bind to `0.0.0.0` or expose through a public tunnel.
- Local passphrase verification is a staging mechanism, **not** approved enterprise SSO. A facility-network deployment requires IT-approved identity authentication, HTTPS, access logs, and operational security review.
- `.local-data/` stores only **this project's** password hashes, session signing key, and isolated shift-session database. It is gitignored.
- Work orders and reporting controls on the frontend are **sample design interactions**, not live Cetaris processing or production reporting.
- The live IND3 Workshop application, roster, database, and GitHub repository are untouched.