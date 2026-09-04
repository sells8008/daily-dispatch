"""Your Reads — followed writers and newsletters. No API key required.

Unlike the topic sections, this one does not pool, rank, or budget anything:
every configured source is surfaced so the reader can browse. A source whose
feed can't be resolved still renders as a link to its page — losing the
latest-post line is acceptable, silently dropping the source is not.

Paywalled publications (Stratechery, Doomberg, Chamath's deep dives) expose
only their free items in a public feed. That's expected, not a failure.
"""

import datetime as dt
import logging
import re
from urllib.parse import urljoin

import feedparser
import requests

from .news import _entry_datetime

logger = logging.getLogger(__name__)

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DailyDispatch/1.0)"}

# Tried in order when the configured feed fails and the page declares no
# feed of its own.
FALLBACK_PATHS = ("/feed", "/feed/", "/rss", "/rss/")

NEW_WITHIN = dt.timedelta(hours=24)

_FEED_LINK_RE = re.compile(
    r"""<link[^>]+type=["']application/(?:rss|atom)\+xml["'][^>]*>""",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)


def _fetch_entries(url, timeout=15):
    """Return parsed entries for a feed URL, or None if it isn't usable."""
    if not url:
        return None
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        return parsed.entries or None
    except Exception:
        logger.info("feed not usable: %s", url)
        return None


def _autodiscover(page_url, timeout=15):
    """Read <link rel="alternate" type="application/rss+xml"> off the page.
    hrefs are often relative (Joe Posnanski publishes href="/feed")."""
    if not page_url:
        return None
    try:
        resp = requests.get(page_url, headers=HTTP_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        logger.info("could not fetch page for autodiscovery: %s", page_url)
        return None

    for tag in _FEED_LINK_RE.findall(resp.text):
        href = _HREF_RE.search(tag)
        if href:
            return urljoin(page_url, href.group(1))
    return None


def _resolve_entries(source):
    """Configured feed -> page autodiscovery -> conventional fallbacks."""
    entries = _fetch_entries(source.get("feed"))
    if entries:
        return entries

    page = source.get("page")
    discovered = _autodiscover(page)
    if discovered:
        entries = _fetch_entries(discovered)
        if entries:
            logger.info("autodiscovered feed for %s: %s", source.get("name"), discovered)
            return entries

    if page:
        for path in FALLBACK_PATHS:
            entries = _fetch_entries(urljoin(page, path))
            if entries:
                logger.info("fallback feed for %s: %s", source.get("name"), path)
                return entries

    logger.warning("no feed resolved for %s — rendering page link only", source.get("name"))
    return None


def _latest_post(entries, now):
    for entry in entries:
        title = (getattr(entry, "title", "") or "").strip()
        url = getattr(entry, "link", None)
        if not title or not url:
            continue
        published = _entry_datetime(entry)
        return {
            "title": title,
            "url": url,
            "date_label": published.strftime("%b %-d") if published else "",
            "is_new": bool(published and (now - published) <= NEW_WITHIN),
        }
    return None


def fetch(config):
    """Returns one entry per configured source, in configured order:
    {name, page, post: {title, url, date_label, is_new} | None}."""
    sources = (config or {}).get("reads", [])
    now = dt.datetime.now(dt.timezone.utc)

    results = []
    for source in sources:
        name = source.get("name")
        page = source.get("page")
        if not name:
            continue

        post = None
        try:
            entries = _resolve_entries(source)
            if entries:
                post = _latest_post(entries, now)
        except Exception:
            logger.exception("reads source failed: %s", name)

        results.append({"name": name, "page": page, "post": post})

    return results
