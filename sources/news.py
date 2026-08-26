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

# Google's own front-page feed — genuinely "whatever is the top news of the
# day," any topic, not scoped to a single search query.
TOP_STORIES_RSS = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"

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
    "Space": "space NASA astronomy",
    "Scientific Breakthroughs": "science discovery breakthrough",
    "Golf History & Courses": "golf course history",
    "Jacksonville & the Beaches": '(Jacksonville Florida OR "Neptune Beach" OR "Atlantic Beach" OR "Jacksonville Beach")',
    "Capital Markets & Lending (US)": "US credit markets lending rates",
    "US Residential Housing": "US housing market",
    "NHL": "NHL hockey",
    "NFL": "NFL football",
    "MLB": "MLB baseball",
    "NCAA Lacrosse": "NCAA college lacrosse",
    "Travel & Destinations": "travel destination beautiful scenic culture",
    "Travel Deals": "travel deal flight hotel discount",
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
    return QUERY_HINTS.get(name, name)


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


def _extract_from_entries(entries, freshness, freshness_windows, seen_urls, paywalled_sources, now, require_fresh, evergreen):
    """Shared filter/dedupe pass: paywall domain, exact-URL dedup, near-
    duplicate headline suppression, and (optionally) freshness — used by
    every fetch function below so they all get the same content-quality
    rules. Sorted newest first."""
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
        # entry.link is often a news.google.com redirect wrapper, not the
        # real publisher URL — entry.source.href has the actual domain, so
        # check that first for paywall matching.
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
                "starred": False,
            }
        )
    found.sort(key=lambda a: a["published"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return found


def fetch_topic(topic, freshness_windows, count=None, seen_urls=None, paywalled_sources=None):
    """Fetch, filter, dedupe, and sort articles for a single topic.

    `topic` is a dict like {name, priority, freshness, never_skip, query?}.
    Returns a list of article dicts: {title, url, source, published, date_label, evergreen, starred}.
    """
    freshness = topic.get("freshness", "standard")
    seen_urls = seen_urls if seen_urls is not None else set()
    now = dt.datetime.now(dt.timezone.utc)
    evergreen = freshness == "evergreen"

    entries = _parse_entries(GOOGLE_NEWS_RSS.format(query=quote_plus(_build_query(topic))))

    articles = _extract_from_entries(entries, freshness, freshness_windows, seen_urls, paywalled_sources, now, True, evergreen)

    # never_skip topics still show *something* even if nothing clears the
    # freshness bar (thin coverage beats a missing section for these).
    used_fallback = False
    if not articles and topic.get("never_skip"):
        articles = _extract_from_entries(entries, freshness, freshness_windows, seen_urls, paywalled_sources, now, False, evergreen)
        used_fallback = True

    # Evergreen topics have no natural turnover (nothing ages out). Topics
    # that fell back to require_fresh=False are in the same boat. Either
    # way, the same top-ranked result would otherwise show up every single
    # day — rotate through the available pool with a day-seeded shuffle
    # instead: deterministic (same pick all day), but different tomorrow.
    if (evergreen or used_fallback) and count and len(articles) > count:
        rng = random.Random(dt.date.today().toordinal())
        rng.shuffle(articles)

    for a in articles:
        seen_urls.add(a["url"].split("?")[0])

    return articles[:count] if count else articles


def fetch_all_topics(topics, freshness_windows, counts_by_priority, paywalled_sources=None, seen_urls=None):
    """Fetch every configured topic. Returns {topic_name: [articles]}."""
    seen_urls = seen_urls if seen_urls is not None else set()
    results = {}
    for topic in topics:
        count = counts_by_priority.get(topic.get("priority"), 3)
        try:
            results[topic["name"]] = fetch_topic(
                topic,
                freshness_windows,
                count=count,
                seen_urls=seen_urls,
                paywalled_sources=paywalled_sources,
            )
        except Exception:
            logger.exception("topic fetch failed: %s", topic.get("name"))
            results[topic["name"]] = []
    return results


def fetch_top_stories(freshness_windows, count, seen_urls=None, paywalled_sources=None):
    """Genuine top-of-the-day headlines from Google's own front-page feed —
    any topic (world events, politics, disasters, culture...), not scoped
    to a single search query the way the other topic sections are."""
    seen_urls = seen_urls if seen_urls is not None else set()
    now = dt.datetime.now(dt.timezone.utc)
    entries = _parse_entries(TOP_STORIES_RSS)
    articles = _extract_from_entries(entries, "fresh", freshness_windows, seen_urls, paywalled_sources, now, True, False)
    for a in articles:
        seen_urls.add(a["url"].split("?")[0])
    return articles[:count] if count else articles


def fetch_music_topic(artists, freshness_windows, count, seen_urls=None, paywalled_sources=None):
    """Equal-weighted music news. A single combined "artist1 OR artist2 OR
    ..." query lets whichever artist has the most press dominate every
    slot (Billy Strings' famously prolific coverage was crowding out
    everyone else) — querying each artist separately and round-robining
    across them keeps representation fair. A day-seeded shuffle of both
    the artist order and each artist's own candidate pool means a
    different subset of artists (and articles) surfaces each day."""
    seen_urls = seen_urls if seen_urls is not None else set()
    now = dt.datetime.now(dt.timezone.utc)
    names = [a["name"] if isinstance(a, dict) else a for a in (artists or [])]
    if not names or not count:
        return []

    today_seed = dt.date.today().toordinal()

    per_artist = {}
    for name in names:
        entries = _parse_entries(GOOGLE_NEWS_RSS.format(query=quote_plus(f'"{name}" music')))
        pool = _extract_from_entries(entries, "standard", freshness_windows, seen_urls, paywalled_sources, now, True, False)
        if not pool:
            # no genuinely recent coverage for this artist — fall back to
            # whatever exists so they aren't just permanently absent
            pool = _extract_from_entries(entries, "standard", freshness_windows, seen_urls, paywalled_sources, now, False, False)
        random.Random(f"{today_seed}:{name}").shuffle(pool)
        per_artist[name] = pool

    order = names[:]
    random.Random(today_seed).shuffle(order)

    selected = []
    cursor = {name: 0 for name in names}
    made_progress = True
    while len(selected) < count and made_progress:
        made_progress = False
        for name in order:
            if len(selected) >= count:
                break
            pool = per_artist[name]
            idx = cursor[name]
            if idx >= len(pool):
                continue
            cursor[name] += 1
            article = pool[idx]
            key = article["url"].split("?")[0]
            if key in seen_urls:
                continue
            seen_urls.add(key)
            selected.append(article)
            made_progress = True

    selected.sort(key=lambda a: a["published"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return selected


# Disambiguating word appended to a team's own name in its news query — a
# college mascot like "Jacksonville Dolphins" covers every sport the school
# plays, not just the one we're actually tracking, without this.
TEAM_LEAGUE_HINTS = {
    "mlb": "MLB baseball",
    "nfl": "NFL football",
    "nhl": "NHL hockey",
    "mens-college-lacrosse": "lacrosse",
}


def fetch_team_news(teams, freshness_windows, count_per_team=2, seen_urls=None, paywalled_sources=None):
    """Favorite-team-specific news (not scores/schedules — those come from
    ESPN via sports.py). Marked `starred` so they stand out from the
    general NFL/MLB/NHL/NCAA-Lacrosse coverage in the combined Sports News
    section."""
    seen_urls = seen_urls if seen_urls is not None else set()
    now = dt.datetime.now(dt.timezone.utc)
    results = []
    for team in teams or []:
        name = team.get("name") if isinstance(team, dict) else team
        if not name:
            continue
        league = team.get("league") if isinstance(team, dict) else None
        hint = TEAM_LEAGUE_HINTS.get(league, "")
        query = f'"{name}" {hint}'.strip()
        entries = _parse_entries(GOOGLE_NEWS_RSS.format(query=quote_plus(query)))
        articles = _extract_from_entries(entries, "fresh", freshness_windows, seen_urls, paywalled_sources, now, True, False)
        for a in articles[:count_per_team]:
            a["starred"] = True
            seen_urls.add(a["url"].split("?")[0])
            results.append(a)
    results.sort(key=lambda a: a["published"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
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
                    "starred": False,
                }
            )
            break  # one per feed keeps it varied; loop again below if we need more
    items.sort(key=lambda a: a["published"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return items[:count]
