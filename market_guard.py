# Gate for refresh_market.bat: exit 0 only when it is just after the US market
# open (09:30-10:10 ET) or close (16:00-16:40 ET) on a NY weekday; exit 1 otherwise.
# The scheduled task fires at 21:40, 22:40, 04:15 and 05:15 SGT so that exactly one
# open trigger and one close trigger land inside the window whichever side of US
# daylight saving we are on; this guard silently skips the other two.
#
# Test:  python market_guard.py --at "2026-08-03T09:45"   (interpreted as NY time)

import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

if len(sys.argv) == 3 and sys.argv[1] == "--at":
    ny = datetime.fromisoformat(sys.argv[2]).replace(tzinfo=NY)
else:
    ny = datetime.now(NY)

after_open = time(9, 30) <= ny.time() < time(10, 10)
after_close = time(16, 0) <= ny.time() < time(16, 40)
ok = ny.weekday() < 5 and (after_open or after_close)
label = "open" if after_open else "close" if after_close else "off-hours"
print(f"NY time {ny:%Y-%m-%d %H:%M} ({label}) -> {'refresh' if ok else 'skip'}")
sys.exit(0 if ok else 1)
