# IND3 Shift Reporting — V3.1

New, independent shift-reporting application for IND3 MST Reliability Operations. This is a **fresh project**, not a merge of the existing Workshop production code.

## Current milestone: frontend prototypes

| View | Open source |
| --- | --- |
| Associate Sign In, Active Shift Detected, New Shift Ready | [Login flow](prototypes/login-flow.html) |
| Shift Report workspace | [Shift reporting](prototypes/shift-report.html) |

These are self-contained, interactive HTML demos with embedded styles, JavaScript, and illustrative warehouse imagery. To preview locally, download the HTML and open it in a browser.

**These previews do not authenticate associates, create or close production shifts, upload real Cetaris files, modify rosters, or send emails.** Interactions are for frontend design review.

## Approved direction

A modern, clean enterprise interface. The main navigation is **Shift Report · Shift History · Analytics · Administration**.

The reporting workspace preserves the necessary information without overcrowding the screen:

- Cetaris import, Change Technicians, Replace File, and Imported WO Audit
- Shift Happenings, inline editing, exclude/restore, and completed PM count
- PMs Needing Completion as a separate file upload
- Breakdown, OOS/RTS tracking
- Vendors, RO, Shift Notes as text-entry fields
- LOTO verified toggle and a 20-key missing-key selector
- Outlook passdown preview/export, End Shift, Sign Out and footer

## Backend status

A **separate local-only** login proof of concept exists outside this GitHub repository, but is **not published here**. Earlier prototype access depended on entering associate-number identifiers; that is not sufficient to authenticate a person. Before a shared deployment, management privileges must be assigned **only after Walmart-approved identity verification**, not based on an entered number.

Never commit real associate numbers, rosters, employee records, production reports, private keys, authentication secrets or local databases to this public repository.

## Project boundaries

- New repository: `zak-isaac/v3.1`
- Existing live production repository: `zak-isaac/ind3-shift-passdown`
- No shared databases, branch merges, production deployments, or modifications to the live Workshop.
- Existing V3 code is a **reference for proven functionality**, not something we copy wholesale.

## Next build work

Structure frontend files into maintainable modules; connect a secure login mechanism and isolated database; test shift-specific reporting flows; then migrate only the proven components needed.
