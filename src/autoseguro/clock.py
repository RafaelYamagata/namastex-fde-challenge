"""The business date: the agent works on Brazil's calendar, wherever the process runs.

A server or container on UTC is already "tomorrow" from 21:00 in Brasília. Without this,
"pode começar hoje" at night would become tomorrow, and a lead's real "today" would be
rejected as a start date in the past.
"""

from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo(os.environ.get("AGENT_TZ", "America/Sao_Paulo"))


def today() -> dt.date:
    return dt.datetime.now(BUSINESS_TZ).date()
