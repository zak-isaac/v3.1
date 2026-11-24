import sqlite3
c = sqlite3.connect('.local-data/shifts.sqlite3')
c.row_factory = sqlite3.Row
print("=== roster_associates: name, job_title, is_mst ===")
for r in c.execute("""SELECT full_name, job_title, is_mst, shift FROM roster_associates
                      WHERE lower(full_name) IN (
                        'isaac ssaku','stanley augustin','alexander carr','len ward','kevin smith'
                      ) ORDER BY full_name""").fetchall():
    print(dict(r))
print()
print("=== distinct job_title values across whole roster ===")
for r in c.execute("SELECT DISTINCT job_title, is_mst, COUNT(*) as n FROM roster_associates GROUP BY job_title, is_mst").fetchall():
    print(dict(r))
