"""News/topic-article source — Google News RSS search, no API key required."""

import datetime as dt
import logging
import random
import re
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
from dateutil import parser as dateparser

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

# Full replacement queries used in place of the bare topic name, to sharpen
# the Google News search. A topic's own `query:` config override (if present)
# always wins over this.
QUERY_HINTS = {
    "Technology": "technology industry news",
    "Artificial Intelligence": "artificial intelligence AI",
    "Cryptocurrency": "bitcoin crypto cryptocurrency market",
    "American Business": "US business economy",
    "PGA Tour": "PGA Tour golf",
    "Food & Cooking": "cooking recipe food",
    "Geopolitics / Current Events": "world news geopolitics",
    "Space": "space NASA astronomy",
    "Scientific Breakthroughs": "science discovery breakthrough",
    "Golf History & Courses": "golf course history",
    "Jacksonville & the Beaches": '(Jacksonville Florida OR "Neptune Beach" OR "Atlantic Beach" OR "Jacksonville Beach")',
    "Capital Markets & Lending (US)": "US credit markets lending rates",
    "US Residential Housing": "US housing market",
    "NHL": "NHL hockey",
}

# A few general long-read feeds for the Miscellaneous overflow section.
MISC_FEEDS = [
    ("Aeon", "https://aeon.co/feed.rss"),
    ("The Atlantic", "https://www.theatlantic.com/feed/channel/ideas/"),
    ("Smithsonian", "https://www.smithsonianmag.com/rss/latest_articles/"),
]

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DailyDispatch/1.0)"}

# Words too common to signal that two headlines are about the same story.
_STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "as", "at", "by", "with", "from", "its", "it's", "that", "this", "after",
    "before", "new", "says", "say", "report", "reports", "amid", "over",
    "into", "than", "how", "what", "why", "will", "has", "have", "had",
}


def _domain(url):
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except ValueError:
        return ""


def _is_paywalled(url, paywalled_sources):
    if not paywalled_sources:
        return False
    domain = _domain(url)
    return any(domain == d or domain.endswith("." + d) for d in paywalled_sources)


def _title_tokens(title):
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _is_near_duplicate(tokens, seen_token_sets, threshold=0.45):
    """Catch different outlets covering the same story with different
    wording (e.g. three separate "US debt hits $40T" headlines) — Jaccard
    overlap on meaningful words, not exact title matching."""
    if not tokens:
        return False
    for other in seen_token_sets:
        if not other:
            continue
        union = tokens | other
        if union and len(tokens & other) / len(union) >= threshold:
            return True
    return False


def _build_query(topic):
    if isinstance(topic, dict) and topic.get("query"):
        return topic["query"]

    name = topic["name"] if isinstance(topic, dict) else topic

    if name == "Music":
        # handled separately by _build_music_query
        return name

    return QUERY_HINTS.get(name, name)


def _build_music_query(artists):
    names = []
    for a in artists:
        if isinstance(a, dict):
            names.append(a["name"])
        else:
            names.append(a)
    quoted = " OR ".join(f'"{n}"' for n in names)
    return f"({quoted}) music"


def _entry_datetime(entry):
    if getattr(entry, "published_parsed", None):
        return dt.datetime(*entry.published_parsed[:6], tzinfo=dt.timezone.utc)
    if getattr(entry, "updated_parsed", None):
        return dt.datetime(*entry.updated_parsed[:6], tzinfo=dt.timezone.utc)
    for key in ("published", "updated"):
        val = getattr(entry, key, None)
        if val:
            try:
                parsed = dateparser.parse(val)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                return parsed
            except (ValueError, OverflowError):
                continue
    return None


def _source_name(entry, fallback="Google News"):
    if getattr(entry, "source", None) and getattr(entry.source, "title", None):
        return entry.source.title
    title = getattr(entry, "title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1]
    return fallback


def _clean_title(entry):
    title = getattr(entry, "title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[0]
    return title


def _within_freshness(published, freshness, windows, now):
    if freshness == "evergreen":
        return True
    if published is None:
        return False
    age = now - published
    if freshness == "fresh":
        return age <= dt.timedelta(hours=windows.get("fresh_hours", 72))
    if freshness == "standard":
        return age <= dt.timedelta(days=windows.get("standard_days", 14))
    return True


def _date_label(published, evergreen):
    if published is None:
        return "Evergreen" if evergreen else "Undated"
    return published.strftime("%b %-d, %Y")


def _parse_entries(feed_url, timeout=10):
    try:
        resp = requests.get(feed_url, headers=HTTP_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return feedparser.parse(resp.content).entries
    except Exception:
        logger.exception("failed to fetch/parse feed: %s", feed_url)
        return []


def fetch_topic(topic, freshness_windows, artists=None, count=None, seen_urls=None, paywalled_sources=None):
    """Fetch, filter, dedupe, and sort articles for a single topic.

    `topic` is a dict like {name, priority, freshness, never_skip, query?}.
    Returns a list of article dicts: {title, url, source, published, date_label, evergreen}.
    """
    name = topic["name"]
    freshness = topic.get("freshness", "standard")
    seen_urls = seen_urls if seen_urls is not None else set()

    query = _build_music_query(artists or []) if name == "Music" else _build_query(topic)
    feed_url = GOOGLE_NEWS_RSS.format(query=quote_plus(query))
    entries = _parse_entries(feed_url)

    now = dt.datetime.now(dt.timezone.utc)
    evergreen = freshness == "evergreen"

    def _extract(require_fresh):
        found = []
        local_seen = set()
        title_token_sets = []
        for entry in entries:
            url = getattr(entry, "link", None)
            title = _clean_title(entry)
            if not url or not title:
                continue
            dedupe_key = url.split("?")[0]
            if dedupe_key in seen_urls or dedupe_key in local_seen:
                continue
            # entry.link is often a news.google.com redirect wrapper, not
            # the real publisher URL — entry.source.href has the actual
            # domain, so check that first for paywall matching.
            source_href = getattr(entry, "source", None)
            source_href = source_href.get("href") if source_href else None
            if _is_paywalled(source_href or url, paywalled_sources):
                continue

            tokens = _title_tokens(title)
            if _is_near_duplicate(tokens, title_token_sets):
                continue

            published = _entry_datetime(entry)
            if require_fresh and not _within_freshness(published, freshness, freshness_windows, now):
                continue

            local_seen.add(dedupe_key)
            title_token_sets.append(tokens)
            found.append(
                {
                    "title": title,
                    "url": url,
                    "source": _source_name(entry),
                    "published": published,
                    "date_label": _date_label(published, evergreen),
                    "evergreen": evergreen,
                }
            )
        found.sort(key=lambda a: a["published"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        return found

    articles = _extract(require_fresh=True)

    # never_skip topics still show *something* even if nothing clears the
    # freshness bar (thin coverage beats a missing section for these).
    used_fallback = False
    if not articles and topic.get("never_skip"):
        articles = _extract(require_fresh=False)
        used_fallback = True

    # Evergreen topics have no natural turnover (nothing ages out). Topics
    # that fell back to require_fresh=False are in the same boat — the
    # freshness filter that would normally rotate content as items age out
    # isn't doing anything for them either (e.g. Music, when the artist
    # watchlist has no genuinely recent hits, was pulling the same
    # date-sorted top N going back months). Either way, the same top-ranked
    # result would otherwise show up every single day — rotate through the
    # available pool with a day-seeded shuffle instead: deterministic (same
    # pick all day), but different tomorrow.
    if (evergreen or used_fallback) and count and len(articles) > count:
        rng = random.Random(dt.date.today().toordinal())
        rng.shuffle(articles)

    for a in articles:
        seen_urls.add(a["url"].split("?")[0])

    return articles[:count] if count else articles


def fetch_all_topics(topics, freshness_windows, counts_by_priority, artists=None, paywalled_sources=None):
    """Fetch every configured topic. Returns {topic_name: [articles]}."""
    seen_urls = set()
    results = {}
    for topic in topics:
        count = counts_by_priority.get(topic.get("priority"), 3)
        try:
            results[topic["name"]] = fetch_topic(
                topic,
                freshness_windows,
                artists=artists,
                count=count,
                seen_urls=seen_urls,
                paywalled_sources=paywalled_sources,
            )
        except Exception:
            logger.exception("topic fetch failed: %s", topic.get("name"))
            results[topic["name"]] = []
    return results


def fetch_misc(seen_urls, count=4, paywalled_sources=None):
    """Curated general-interest feeds for the Miscellaneous overflow section."""
    items = []
    for source_name, feed_url in MISC_FEEDS:
        entries = _parse_entries(feed_url)
        for entry in entries[:5]:
            url = getattr(entry, "link", None)
            title = getattr(entry, "title", None)
            if not url or not title:
                continue
            dedupe_key = url.split("?")[0]
            if dedupe_key in seen_urls:
                continue
            if _is_paywalled(url, paywalled_sources):
                continue
            seen_urls.add(dedupe_key)
            published = _entry_datetime(entry)
            items.append(
                {
                    "title": title,
                    "url": url,
                    "source": source_name,
                    "published": published,
                    "date_label": _date_label(published, evergreen=True),
                    "evergreen": True,
                }
            )
            break  # one per feed keeps it varied; loop again below if we need more
    items.sort(key=lambda a: a["published"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return items[:count]
