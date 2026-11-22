"""Pure parsers for what Strava's web pages return. No I/O, no Playwright: everything here is unit-tested.

Strava's web pages show numbers as display text in the logged-in account's units ("5.23 km", "3.1 mi",
"394 ft", "1h 5m"). These parsers accept metric and imperial units but only English labels and
English number formatting ("1,234.5"). A page in another language raises UnsupportedLocale instead of
returning wrong numbers.
"""
from __future__ import annotations

import html
import re
from collections.abc import Iterable

from .errors import MarkupChanged, UnsupportedLocale

# Metres per unit. Longer suffixes first so "mi" never matches as "m".
_LENGTH_UNITS = {"km": 1000.0, "mi": 1609.344, "ft": 0.3048, "yd": 0.9144, "m": 1.0}
_HMS_SECONDS = {"h": 3600, "m": 60, "s": 1}
_LENGTH_RE = re.compile(r"\s*(\d[\d,]*(?:\.\d+)?|\.\d+)\s*(km|mi|ft|yd|m)\s*", re.IGNORECASE)
_HMS_RE = re.compile(r"(\d+)\s*(h|m|s)(?![a-z])", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$")
_INT_RE = re.compile(r"\d[\d,]*")
_TAG_RE = re.compile(r"<[^>]+>")

# Stat labels Strava shows on activity cards in English. Used to tell an English page from a translated one.
ENGLISH_STAT_LABELS = frozenset({
    "Distance", "Time", "Elev Gain", "Pace", "Speed", "Steps", "Calories", "Avg HR", "Elevation",
    "Moving Time", "Elapsed Time", "Avg Speed", "Avg Pace", "Max Speed", "Energy Output", "Avg Power",
})

# "Activity" entries (camelCase) -> the snake_case keys "GroupActivity" activities already use.
_ACTIVITY_KEYS = {
    "activity_id": "id", "start_date": "startDate", "name": "activityName", "type": "type",
    "elapsed_time": "elapsedTime", "device_name": "deviceName", "workout_type": "workoutType",
    "is_virtual": "isVirtual", "is_commute": "isCommute", "visibility": "visibility",
    "description": "description", "stats": "stats",
}


def strip_markup(value) -> str:
    """Strip Strava's markup (<abbr> units, <p>/<br> in descriptions) and HTML entities.

    Args:
        value: Any value; None becomes ''.

    Returns:
        Plain, trimmed text.
    """
    return html.unescape(_TAG_RE.sub("", str(value if value is not None else ""))).strip()


def to_meters(text: str | None) -> float | None:
    """Parse a displayed length ('5.23 km', '3.1 mi', '394 ft', '1,234 m').

    The whole text must be one number and one unit, so '1h 5m' is not read as 5 metres.

    Args:
        text: Display text.

    Returns:
        Metres rounded to 0.1, or None if the text is not a length.
    """
    m = _LENGTH_RE.fullmatch(text or "")
    if not m:
        return None
    return round(float(m.group(1).replace(",", "")) * _LENGTH_UNITS[m.group(2).lower()], 1)


def to_seconds(text: str | None) -> int | None:
    """Parse a displayed duration: '1h 5m 3s', '45m 10s', '1h5m', '25:30' (mm:ss), '1:02:03' (h:mm:ss).

    Args:
        text: Display text.

    Returns:
        Whole seconds, or None if the text is not a duration.
    """
    s = text or ""
    clock = _CLOCK_RE.match(s)
    if clock:
        h, m, sec = clock.groups()
        return int(h or 0) * _HMS_SECONDS["h"] + int(m) * _HMS_SECONDS["m"] + int(sec)
    parts = _HMS_RE.findall(s)
    if not parts:
        return None
    return sum(int(n) * _HMS_SECONDS[u.lower()] for n, u in parts)


def to_int(text: str | None) -> int | None:
    """Parse the first integer in a string ('1,234 steps' -> 1234).

    Args:
        text: Display text.

    Returns:
        The integer, or None if there is none.
    """
    m = _INT_RE.search(text or "")
    return int(m.group(0).replace(",", "")) if m else None


def parse_stats(stats: Iterable[dict] | None) -> dict[str, str]:
    """Turn Strava's stat_one / stat_one_subtitle pairs into {label: value}.

    Labels vary by sport, so callers look up by label ('Distance', 'Pace'), never by position.

    Args:
        stats: Strava's [{"key": ..., "value": ...}] list.

    Returns:
        Plain-text values keyed by plain-text label.
    """
    raw = {s.get("key"): s.get("value") for s in (stats or [])}
    out = {}
    for key, value in raw.items():
        if key and not key.endswith("_subtitle"):
            label = strip_markup(raw.get(f"{key}_subtitle"))
            if label:
                out[label] = strip_markup(value)
    return out


def check_english(stat_dicts: Iterable[dict[str, str]]) -> None:
    """Check that activity stat labels are English.

    Args:
        stat_dicts: parse_stats() results.

    Raises:
        UnsupportedLocale: There are labels and none of them is an English label.
    """
    labels = {label for d in stat_dicts for label in d}
    if labels and not labels & ENGLISH_STAT_LABELS:
        raise UnsupportedLocale(
            f"Strava is showing stat labels {sorted(labels)[:5]}; set the account's language to English "
            "(strava.com/settings/display) so numbers can be parsed correctly.")


def _bool(value) -> bool | None:
    """Read a JSON or string boolean.

    Args:
        value: True/False, "true"/"false", None or "".

    Returns:
        The boolean, or None when absent.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _int(value) -> int | None:
    """Convert to int.

    Args:
        value: Anything.

    Returns:
        int(value), or None when it can't be converted.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _figures(stats: dict[str, str]) -> dict:
    """Read the numeric figures from a stats dict.

    Args:
        stats: parse_stats() result.

    Returns:
        distance, moving_time, total_elevation_gain, steps (numbers or None) and pace (text or None).
    """
    return {
        "distance": to_meters(stats.get("Distance")),
        "moving_time": to_seconds(stats.get("Time") or stats.get("Moving Time")),
        "total_elevation_gain": to_meters(stats.get("Elev Gain") or stats.get("Elevation")),
        "steps": to_int(stats.get("Steps")),
        "pace": stats.get("Pace") or None,
    }


def _activity_row(entity: str, a: dict) -> dict:
    """Build one flat activity dict from snake_case activity data (GroupActivity's shape).

    Args:
        entity: "Activity" or "GroupActivity".
        a: Activity data with GroupActivity's snake_case keys.

    Returns:
        The flat dict described in feed_entry_activities.
    """
    stats = parse_stats(a.get("stats"))
    return {
        "entity": entity, "_stats": stats, **_figures(stats),
        "id": _int(a.get("activity_id")), "athlete_id": _int(a.get("athlete_id")),
        "athlete_name": strip_markup(a.get("athlete_name")) or None,
        "athlete_firstname": strip_markup(a.get("athlete_firstname")) or None,
        "start_date": a.get("start_date") or None, "start_date_local": a.get("start_date_local") or None,
        "name": strip_markup(a.get("name")) or None, "sport_type": a.get("type") or None,
        "elapsed_time": _int(a.get("elapsed_time")), "device_name": a.get("device_name") or None,
        "workout_type": _int(a.get("workout_type")), "is_virtual": _bool(a.get("is_virtual")),
        "is_commute": _bool(a.get("is_commute")), "visibility": a.get("visibility") or None,
        "description": strip_markup(a.get("description")) or None, "location": a.get("location") or None,
        "kudos_count": _int(a.get("kudos_count")), "comment_count": _int(a.get("num_comments")),
    }


def feed_entry_activities(entry: dict) -> list[dict]:
    """Turn one club-feed / profile-week entry into zero or more flat activity dicts with numeric fields.

    "Activity" entries are camelCase at entry["activity"]; "GroupActivity" entries nest snake_case activities
    in entry["rowData"]["activities"] (one per athlete in the group). Other entities (posts, challenges) -> [].

    Args:
        entry: One feed entry.

    Returns:
        Dicts with keys id, athlete_id, athlete_name, athlete_firstname, start_date, start_date_local, name,
        sport_type, distance, moving_time, elapsed_time, total_elevation_gain, steps, pace, device_name,
        workout_type, is_virtual, is_commute, visibility, location, description, kudos_count, comment_count,
        entity, _stats.
    """
    entity = entry.get("entity")
    if entity == "Activity":
        a = entry.get("activity") or {}
        ath = a.get("athlete") or {}
        kc = a.get("kudosAndComments") or {}
        return [_activity_row(entity, {
            **{k: a.get(v) for k, v in _ACTIVITY_KEYS.items()},
            "athlete_id": ath.get("athleteId"), "athlete_name": ath.get("athleteName"),
            "athlete_firstname": ath.get("firstName"),
            "location": (a.get("timeAndLocation") or {}).get("location"),
            "kudos_count": kc.get("kudosCount"), "num_comments": len(kc.get("comments") or []),
        })]
    if entity == "GroupActivity":
        return [_activity_row(entity, a) for a in (entry.get("rowData") or {}).get("activities") or []]
    return []


def feed_page(entries: list[dict]) -> list[dict]:
    """Collect every activity in a page of feed entries, after checking the page is in English.

    Args:
        entries: Feed entries.

    Returns:
        Flat activity dicts that have both an id and an athlete_id.

    Raises:
        UnsupportedLocale: The page is not in English.
    """
    rows = [r for e in entries for r in feed_entry_activities(e)]
    check_english(r["_stats"] for r in rows)
    return [r for r in rows if r["id"] and r["athlete_id"]]


# --- members page -------------------------------------------------------------------------------------------

_COUNT_RE = re.compile(r"""class=['"]membership-count['"][^>]*>\s*([\d,]+)\s+members?\b""")
_LIST_RE = re.compile(r"""<ul[^>]*class=['"]list-athletes['"][^>]*>""")
_ROW_RE = re.compile(r"""class=['"]text-headline['"]>\s*<a href=['"]/athletes/(\d+)['"]>([^<]*)</a>""")
_HEADING_RE = re.compile(r"<h\d[^>]*>([^<]*)</h\d>", re.IGNORECASE)


def parse_member_count(page_html: str) -> int | None:
    """Read the headline from <span class='membership-count'>1,055 members</span>.

    Args:
        page_html: A members page.

    Returns:
        The member count, or None if the headline is missing.
    """
    m = _COUNT_RE.search(page_html or "")
    return to_int(m.group(1)) if m else None


def parse_roster(page_html: str) -> list[tuple[int, str, bool | None]]:
    """Read each row of a members page's ul.list-athletes lists.

    is_admin comes from the heading right before each list ('Admins' / 'Members'); None when there is no
    heading to tell. Rows outside those lists (nav, sidebar) are ignored.

    Args:
        page_html: A members page.

    Returns:
        (athlete_id, display name, is_admin) tuples in page order.
    """
    out: list[tuple[int, str, bool | None]] = []
    text = page_html or ""
    prev_end = 0
    for m in _LIST_RE.finditer(text):
        headings = _HEADING_RE.findall(text[prev_end:m.start()])
        heading = strip_markup(headings[-1]).lower() if headings else ""
        is_admin = True if "admin" in heading else False if "member" in heading else None
        block = text[m.end():].split("</ul>", 1)[0]
        for aid, name in _ROW_RE.findall(block):
            out.append((int(aid), html.unescape(name).strip(), is_admin))
        prev_end = m.end() + len(block)
    return out


def split_name(display_name: str) -> tuple[str | None, str | None]:
    """Split a display name at the first space: 'Ada Lovelace King' -> ('Ada', 'Lovelace King').

    Args:
        display_name: Full name as shown.

    Returns:
        (first, rest); rest is None for a single word, both None for an empty name.
    """
    parts = (display_name or "").split(None, 1)
    if not parts:
        return None, None
    return parts[0], (parts[1] if len(parts) > 1 else None)


# --- leaderboard --------------------------------------------------------------------------------------------

def leaderboard_rows(rows: list[dict]) -> list[dict]:
    """Turn rank-table cell texts (from the DOM) into numeric rows.

    Args:
        rows: {id, name, rank, dist, acts, elev, time} cell texts per row.

    Returns:
        Dicts with rank, athlete_id, athlete_name, distance, activities, total_elevation_gain, moving_time.

    Raises:
        MarkupChanged: A row can't be read.
    """
    out = []
    for r in rows:
        try:
            out.append({
                "rank": to_int(r.get("rank")),
                "athlete_id": int(r["id"]),
                "athlete_name": (r.get("name") or "").strip(),
                "distance": to_meters(r.get("dist")) or 0.0,
                "activities": to_int(r.get("acts")) or 0,
                "total_elevation_gain": to_meters(r.get("elev")) or 0.0,
                "moving_time": to_seconds(r.get("time")) or 0,
            })
        except (KeyError, TypeError, ValueError) as e:
            raise MarkupChanged(f"Unreadable leaderboard row {r!r}: {e}") from e
    return out
