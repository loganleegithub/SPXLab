"""Frozen Cboe 2026 SPXW PM session calendar; unknown years fail closed.

Source checked 2026-09-16: https://www.cboe.com/about/hours/us-options/
Expiration cutoff: https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications
This is the expiring SPXW session, not the general 16:15 options session.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .contracts import require, stamp

VERSION = 'CBOE_SPXW_PM_2026_20260916'
HOLIDAYS = frozenset(('2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03',
                     '2026-05-25', '2026-06-19', '2026-07-03', '2026-09-07',
                     '2026-11-26', '2026-12-25'))
HALF_DAYS = frozenset(('2026-11-27', '2026-12-24'))
NY = ZoneInfo('America/New_York')


def session_schedule(session, version=VERSION):
    require(version == VERSION, 'Unknown calendar version')
    day = date.fromisoformat(session)
    require(day.year == 2026, 'Calendar does not cover this year')
    require(day.weekday() < 5 and session not in HOLIDAYS, 'No regular SPXW session')
    def local(clock):
        return datetime.fromisoformat(session + 'T' + clock).replace(tzinfo=NY)
    close = local('13:00:00' if session in HALF_DAYS else '16:00:00')
    utc = lambda x: x.astimezone(timezone.utc).isoformat()
    return {'session': session, 'series': 'SPXW_PM', 'calendar_version': VERSION,
            'timezone': 'America/New_York', 'half_day': session in HALF_DAYS,
            'open_utc': utc(local('09:30:00')), 'fixed_utc': utc(local('10:05:00')),
            'entry_end_utc': utc(close-timedelta(minutes=30)), 'close_utc': utc(close),
            'capture_end_utc': utc(close+timedelta(minutes=10))}


def decision_times(schedule, step_seconds=30):
    require(step_seconds > 0, 'Positive grid required')
    t, end = stamp(schedule['fixed_utc']), stamp(schedule['entry_end_utc'])
    result = []
    while t < end:
        result.append(t.isoformat())
        t += timedelta(seconds=step_seconds)
    return result


def contract_session_matches(contract, schedule):
    """Compare actual IB liquid-hours segment after its own time-zone conversion."""
    try:
        tz = ZoneInfo(contract['time_zone'])
        expiry = schedule['session'].replace('-', '')
        for segment in contract['liquid_hours'].split(';'):
            if segment.startswith(expiry + ':') and 'CLOSED' not in segment:
                start, end = segment.split('-')
                if ':' not in end:
                    end = expiry + ':' + end
                a = datetime.strptime(start, '%Y%m%d:%H%M').replace(tzinfo=tz)
                b = datetime.strptime(end, '%Y%m%d:%H%M').replace(tzinfo=tz)
                return a == stamp(schedule['open_utc']) and b == stamp(schedule['close_utc'])
    except (KeyError, ValueError):
        return False
    return False
