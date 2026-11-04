"""Choose an actually active IND3 shift for wall-clock Playwright tests.

GitHub Actions runs at any hour and UTC may differ from Indianapolis.
A hardcoded S1 login and naive datetime.now() can fail even when V3.1 is
correct. This helper requires a real shift window with time left to start.
"""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

TZ=ZoneInfo("America/Indiana/Indianapolis")
RULES={
 "S1":((5,30),(16,0),{0,1,2,3}),
 "S2":((12,0),(22,30),{0,1,2,3}),
 "S3":((20,30),(7,0),{0,1,2,3}),
 "S4":((5,30),(18,0),{4,5,6}),
 "S5":((17,30),(6,0),{4,5,6}),
}

def shift_for_browser(now=None):
    now=now or datetime.now(TZ)
    possible=[]
    for day in (now.date()-timedelta(days=1),now.date()):
        for code,(a,b,weekdays) in RULES.items():
            if day.weekday() not in weekdays:continue
            start=datetime.combine(day,time(*a),tzinfo=TZ)
            end=datetime.combine(day,time(*b),tzinfo=TZ)
            if end<=start:end+=timedelta(days=1)
            # For work-attribution browser tests require an actually begun
            # shift, not merely a prestart login grace. Start before end.
            if start<=now<end-timedelta(minutes=1):
                possible.append(((end-now).total_seconds(),code))
    if not possible:raise RuntimeError("No shift available for browser integration test")
    return max(possible)[1]

def local_work_time():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
