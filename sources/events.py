"""Events source — Ticketmaster Discovery API. Requires TICKETMASTER_API_KEY.

Food/restaurant-opening items ("Food"/"Opening" tags in the template) are
sourced from local news, not Ticketmaster — per the build spec that lane is
expected to be thin or empty and isn't handled here.
"""

import datetime as dt
import logging
import time

import requests
from dateutil import parser as dateparser

logger = logging.getLogger(__name__)

DISCOVERY_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

# The radius search alone returns everything Ticketmaster has nearby —
# minor-league baseball every night, film screenings, etc. Around Town is
# meant to be a curated concerts/festivals/marquee list, not that firehose,
# so anything that doesn't land in one of these buckets is dropped.
KEPT_KIND_CLASSES = {"concert", "marquee"}
KEPT_KINDS = {"Festival"}

MAX_ITEMS = 40


def _get(params, api_key, timeout=10):
    # The free tier throttles around 5 req/sec; this fetch fires a couple
    # dozen calls (radius + one per artist + one per marquee venue), so
    # space them out a bit to avoid 429s.
    time.sleep(0.25)
    try:
        params = dict(params, apikey=api_key)
        resp = requests.get(DISCOVERY_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("_embedded", {}).get("events", [])
    except Exception:
        logger.exception("Ticketmaster fetch failed: %s", {k: v for k, v in params.items() if k != "apikey"})
        return []


def _classify(event, always_watch_venues):
    name_lower = event.get("name", "").lower()
    classifications = event.get("classifications", [])
    segment = classifications[0].get("segment", {}).get("name", "") if classifications else ""
    venue_name = _venue_name(event) or ""

    if "festival" in name_lower:
        return "Festival", ""
    if segment == "Music":
        return "Concert", "concert"
    if any(v.lower() in venue_name.lower() for v in always_watch_venues):
        return "Marquee", "marquee"
    return segment or "Event", ""


def _venue_name(event):
    venues = event.get("_embedded", {}).get("venues", [])
    return venues[0].get("name") if venues else None


def _venue_city(event):
    venues = event.get("_embedded", {}).get("venues", [])
    if not venues:
        return None
    v = venues[0]
    city = v.get("city", {}).get("name")
    state = v.get("state", {}).get("stateCode")
    return ", ".join(p for p in (city, state) if p)


def _event_date(event):
    start = event.get("dates", {}).get("start", {})
    date_str = start.get("localDate")
    time_str = start.get("localTime")
    if not date_str:
        return None
    try:
        if time_str:
            return dateparser.parse(f"{date_str}T{time_str}")
        return dateparser.parse(date_str)
    except (ValueError, OverflowError):
        return None


def _when_label(event_dt):
    return event_dt.strftime("%a %b %-d")


def _link_label(event):
    status = event.get("dates", {}).get("status", {}).get("code")
    if status == "onsale":
        return "Tickets"
    return "Info"


def fetch(config, api_key):
    if not api_key:
        logger.warning("TICKETMASTER_API_KEY not set — events section unavailable")
        return []

    events_cfg = config.get("events", {})
    lat = events_cfg.get("center_lat", config["location"]["lat"])
    lon = events_cfg.get("center_lon", config["location"]["lon"])
    radius = events_cfg.get("radius_miles", 75)
    always_watch_venues = events_cfg.get("always_watch_venues", [])
    artist_search_cities = events_cfg.get("artist_search_cities", [])
    # Which of the always_watch_venues also get the "starred" highlight, on
    # top of their Marquee tag (e.g. SeaWalk Pavilion, but not every marquee
    # venue needs to shout as loud as a watchlist-artist show).
    starred_venues = events_cfg.get("starred_venues", [])

    artists = []
    for a in config.get("artists", []):
        if isinstance(a, dict):
            if not a.get("news_only"):
                artists.append(a["name"])
        else:
            artists.append(a)

    # raw_events[id] = {"event": <ticketmaster event>, "starred": bool}. An
    # event can turn up via more than one search (e.g. the base radius query
    # and an artist-city query); if *any* of those hits is star-worthy, the
    # event stays starred even if a later, unstarred hit re-finds it.
    raw_events = {}

    def _merge(event, starred=False):
        entry = raw_events.get(event["id"])
        if entry:
            entry["starred"] = entry["starred"] or starred
        else:
            raw_events[event["id"]] = {"event": event, "starred": starred}

    for event in _get(
        {"latlong": f"{lat},{lon}", "radius": radius, "unit": "miles", "sort": "date,asc", "size": 50},
        api_key,
    ):
        _merge(event)

    # Artist searches are scoped to specific cities (Jacksonville + the
    # radius, plus Ocala/Orlando/Savannah from config) rather than the
    # whole country — but within those cities, no date cutoff, so a show
    # announced a year out still shows up as soon as it's on sale. These are
    # always starred: a watchlist artist playing nearby should stand out.
    for artist in artists:
        for city in artist_search_cities:
            for event in _get({"keyword": artist, "city": city, "sort": "date,asc", "size": 10}, api_key):
                _merge(event, starred=True)

    # Marquee venues deliberately ignore radius — they're checked by name so
    # out-of-radius venues (WEC Ocala, TPC Sawgrass) still surface.
    for venue in always_watch_venues:
        venue_starred = any(v.lower() == venue.lower() for v in starred_venues)
        for event in _get({"keyword": venue, "sort": "date,asc", "size": 10}, api_key):
            _merge(event, starred=venue_starred)

    now = dt.datetime.now()
    items = []
    for entry in raw_events.values():
        event = entry["event"]
        event_dt = _event_date(event)
        if event_dt and event_dt < now:
            continue
        kind, kind_class = _classify(event, always_watch_venues)
        if kind_class not in KEPT_KIND_CLASSES and kind not in KEPT_KINDS:
            continue
        venue_name = _venue_name(event) or "Venue TBA"
        city = _venue_city(event)
        items.append(
            {
                "kind": kind,
                "kind_class": kind_class,
                "starred": entry["starred"],
                "name": event.get("name"),
                "venue": f"{venue_name} · {city}" if city else venue_name,
                "when": _when_label(event_dt) if event_dt else "Date TBA",
                "sort_key": event_dt or dt.datetime.max,
                "url": event.get("url"),
                "link_label": _link_label(event),
            }
        )

    items.sort(key=lambda e: e["sort_key"])
    items = items[:MAX_ITEMS]
    for item in items:
        del item["sort_key"]
    return items
