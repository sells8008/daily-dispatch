"""SeaWalk Pavilion events — City of Jacksonville Beach calendar. No API key.

SeaWalk Pavilion is a city-run venue: its events (festivals, markets, car
cruises, community concerts) are almost never ticketed through Ticketmaster,
so the Discovery API returns literally zero results for it no matter how the
venue is spelled. This reads the city's own public iCal calendar feed
instead and contributes those events to Around Town.

The feed carries no ticket links, which matches reality for these — the
listing is name, date, and location, linking to the city's event page.
"""

import datetime as dt
import html
import logging
import re

import requests
from dateutil import parser as dateparser

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 fallback
    from backports.zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# catID 23 is the city's Parks & Recreation calendar — swept the other
# category ids and it's the only one publishing events, SeaWalk included.
ICAL_URL = "https://www.jacksonvillebeach.org/common/modules/iCalendar/iCalendar.aspx?catID=23&feed=calendar"

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DailyDispatch/1.0)"}

# The venue is spelled several ways across listings ("Seawalk Pavilion &
# Latham Plaza", "Latham Plaza & Seawalk Pavilion"); its street address is
# the one stable identifier.
VENUE_MATCHES = ("seawalk", "11 1st street")

DISPLAY_VENUE = "SeaWalk Pavilion · Jacksonville Beach, FL"


def _unfold(raw):
    """iCal wraps long lines with a leading space/tab on continuations."""
    lines = []
    for line in raw.splitlines():
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _unescape(value):
    value = value.replace("\\n", " ").replace("\\N", " ")
    value = value.replace("\\;", ";").replace("\\,", ",").replace("\\\\", "\\")
    value = re.sub(r"<[^>]+>", " ", value)  # LOCATION carries HTML fragments
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_vevents(raw):
    events, current = [], None
    for line in _unfold(raw):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            name, _, value = line.partition(":")
            key = name.split(";")[0].upper()
            params = name.split(";")[1:]
            current[key] = value
            if key == "DTSTART":
                for p in params:
                    if p.upper().startswith("TZID="):
                        current["_TZID"] = p.split("=", 1)[1]
    return events


def _event_datetime(vevent):
    value = vevent.get("DTSTART")
    if not value:
        return None
    try:
        parsed = dateparser.parse(value)
    except (ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    # Feed times are wall-clock local to the venue; keep them naive so they
    # compare cleanly against a naive "now" the same way events.py does.
    return parsed.replace(tzinfo=None)


def _classify(name):
    lowered = name.lower()
    if "festival" in lowered or "fest" in lowered:
        return "Festival", ""
    if any(w in lowered for w in ("concert", "music", "band", "jam")):
        return "Concert", "concert"
    return "Local", ""


def fetch(config, timeout=15):
    """Upcoming SeaWalk Pavilion events. Returns a list shaped like the
    dicts sources/events.py produces, so the two merge directly."""
    events_cfg = config.get("events", {}) if config else {}
    starred_venues = [v.lower() for v in events_cfg.get("starred_venues", [])]
    star = any("seawalk" in v for v in starred_venues)

    try:
        resp = requests.get(ICAL_URL, headers=HTTP_HEADERS, timeout=timeout)
        resp.raise_for_status()
        vevents = _parse_vevents(resp.text)
    except Exception:
        logger.exception("SeaWalk calendar fetch failed")
        return []

    tz_name = (config or {}).get("location", {}).get("timezone", "America/New_York")
    try:
        now = dt.datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
    except Exception:
        now = dt.datetime.now()

    items = []
    seen = set()
    for vevent in vevents:
        location = _unescape(vevent.get("LOCATION", ""))
        if not any(m in location.lower() for m in VENUE_MATCHES):
            continue

        name = _unescape(vevent.get("SUMMARY", ""))
        if not name:
            continue

        start = _event_datetime(vevent)
        if start and start.date() < now.date():
            continue

        key = (name.lower(), start.date() if start else None)
        if key in seen:
            continue
        seen.add(key)

        # The city puts the event's own page in DESCRIPTION; the iCal URL
        # field just points back at the raw feed, which is useless here.
        description = _unescape(vevent.get("DESCRIPTION", ""))
        match = re.search(r"https?://\S+", description)
        url = match.group(0) if match else "https://www.jacksonvillebeach.org/Calendar.aspx"

        kind, kind_class = _classify(name)
        items.append(
            {
                "kind": kind,
                "kind_class": kind_class,
                "starred": star,
                "name": name,
                "venue": DISPLAY_VENUE,
                "when": start.strftime("%a %b %-d") if start else "Date TBA",
                "sort_key": start or dt.datetime.max,
                "url": url,
                "link_label": "Info",
            }
        )

    items.sort(key=lambda e: e["sort_key"])
    return items
