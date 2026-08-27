#!/usr/bin/env python3
"""Orchestrator for The Morning Dispatch.

Fetch -> assemble -> render -> email -> write index.html.
Every fetch step is fail-soft: a broken source degrades its section instead
of killing the whole build. Only a true fatal error (config missing, template
missing, render failure) should exit non-zero.
"""

import datetime as dt
import logging
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from sources import events, markets, news, recipe, seawalk, sports, weather

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("build")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
TEMPLATE_NAME = "template.html.j2"
OUTPUT_PATH = os.path.join(BASE_DIR, "index.html")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def fetch_weather_section(config):
    return weather.fetch(config["location"])


def fetch_events_section(config):
    api_key = os.environ.get("TICKETMASTER_API_KEY")

    # SeaWalk Pavilion is city-run and isn't on Ticketmaster at all, so it
    # comes from the City of Jacksonville Beach calendar instead. Fetched
    # separately (and fail-soft on its own) so a problem with either source
    # can't take out the whole section.
    try:
        seawalk_events = seawalk.fetch(config)
    except Exception:
        logger.exception("SeaWalk events failed")
        seawalk_events = []

    try:
        return events.fetch(config, api_key, extra_events=seawalk_events)
    except Exception:
        logger.exception("events section failed")
        return seawalk_events


def fetch_sports_section(config):
    teams = config.get("teams", [])
    tz_name = config["location"].get("timezone", "America/New_York")

    yesterday_results = sports.yesterday_results(teams, tz_name)
    teams_today, teams_notes = sports.teams_today(teams, tz_name)

    golf_data = sports.golf(config.get("golf", {}).get("follow"))
    if golf_data:
        match_label = f"{golf_data['name']} — {golf_data['round_label']}"
        if golf_data["state"] == "post":
            yesterday_results.append(
                {
                    "league": "PGA Tour",
                    "match": match_label,
                    "kind": "leader",
                    "leader_name": golf_data["leader_name"],
                    "leader_score": golf_data["leader_score"],
                    "url": golf_data["url"],
                }
            )
        elif golf_data["state"] == "in":
            teams_today.append(
                {
                    "league": "PGA Tour",
                    "match": match_label,
                    "time": golf_data["round_label"],
                    "channel": golf_data["channel"],
                    "url": golf_data["url"],
                }
            )

    tonight_game = None
    if teams_today:
        g = teams_today[0]
        tonight_game = {"match": g["match"], "time": g["time"], "channel": g["channel"]}

    return {
        "yesterday_results": yesterday_results,
        "teams_today": teams_today,
        "teams_notes": teams_notes,
        "tonight_game": tonight_game,
    }


def fetch_news_sections(config):
    """Fetch all configured topics, then map them onto the template's
    named sections (some sections combine multiple topics)."""
    freshness_windows = config["freshness_windows"]
    paywalled = config.get("paywalled_sources", [])
    topics_by_name = {t["name"]: t for t in config["topics"]}

    # One shared seen_urls set across every fetch below, in claim order:
    # favorite-team news and top/music picks get first claim on a URL (so
    # they show starred/prominent) before the generic per-topic queries run
    # and would otherwise show the same story unstarred.
    seen_urls = set()

    team_news = news.fetch_team_news(
        config.get("teams", []),
        freshness_windows,
        count_per_team=config.get("team_news_per_team", 2),
        seen_urls=seen_urls,
        paywalled_sources=paywalled,
    )

    top_stories_articles = news.fetch_top_stories(
        freshness_windows,
        config.get("top_stories_count", 6),
        seen_urls=seen_urls,
        paywalled_sources=paywalled,
    )
    top_stories = {
        "lead": top_stories_articles[0] if top_stories_articles else None,
        "rest": top_stories_articles[1:] if top_stories_articles else [],
    }

    music_topic = topics_by_name.get("Music", {"priority": 3})
    music = news.fetch_music_topic(
        config.get("artists", []),
        freshness_windows,
        config["counts_by_priority"].get(music_topic.get("priority"), 3),
        seen_urls=seen_urls,
        paywalled_sources=paywalled,
    )

    # Generic per-topic queries — everything except Music (handled above via
    # the per-artist round-robin, not a single search query).
    generic_topics = [t for t in config["topics"] if t["name"] != "Music"]
    all_articles = news.fetch_all_topics(
        generic_topics,
        freshness_windows,
        config["counts_by_priority"],
        paywalled_sources=paywalled,
        seen_urls=seen_urls,
    )

    def get(name):
        return all_articles.get(name, [])

    business = get("American Business") + get("Capital Markets & Lending (US)") + get("US Residential Housing")
    science = get("Scientific Breakthroughs") + get("Space")
    golf = get("PGA Tour") + get("Golf History & Courses")
    travel = get("Travel & Destinations") + get("Travel Deals")
    sports_news = team_news + get("NFL") + get("MLB") + get("NHL") + get("NCAA Lacrosse")

    misc = news.fetch_misc(seen_urls, count=config.get("misc_count", 4), paywalled_sources=paywalled)

    return {
        "top_stories": top_stories,
        "technology": get("Technology"),
        "ai": get("Artificial Intelligence"),
        "business": business,
        "crypto": get("Cryptocurrency"),
        "science": science,
        "golf": golf,
        "music": music,
        "food": get("Food & Cooking"),
        "jacksonville": get("Jacksonville & the Beaches"),
        "travel": travel,
        "sports_news": sports_news,
        "misc": misc,
    }


def build_context(config):
    now_local = dt.datetime.now()
    location = config["location"]

    ctx = {
        "site_url": config.get("site_url"),
        "edition": {
            "date_str": now_local.strftime("%A, %B %-d, %Y"),
            "location": location["label"],
            "number": f"No. {now_local.timetuple().tm_yday:03d}",
        },
        "generated_time": now_local.strftime("%-I:%M %p"),
        "weather": None,
        "markets": None,
        "tonight_game": None,
        "cook": None,
        "events": [],
        "yesterday_results": [],
        "teams_today": [],
        "teams_notes": [],
        "top_stories": {"lead": None, "rest": []},
        "technology": [], "ai": [], "business": [], "crypto": [], "science": [],
        "golf": [], "music": [], "food": [], "jacksonville": [], "travel": [],
        "sports_news": [], "misc": [],
    }

    logger.info("fetching weather...")
    ctx["weather"] = fetch_weather_section(config)
    if ctx["weather"] is None:
        logger.warning("weather section unavailable")

    logger.info("fetching news topics...")
    try:
        ctx.update(fetch_news_sections(config))
    except Exception:
        logger.exception("news section failed")

    logger.info("fetching sports...")
    try:
        ctx.update(fetch_sports_section(config))
    except Exception:
        logger.exception("sports section failed")

    logger.info("fetching events...")
    ctx["events"] = fetch_events_section(config)

    logger.info("fetching markets...")
    try:
        ctx["markets"] = markets.fetch()
    except Exception:
        logger.exception("markets section failed")

    ctx["cook"] = recipe.fetch(config.get("recipe_rotation"))

    return ctx


def render(context):
    env = Environment(
        loader=FileSystemLoader(BASE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template(TEMPLATE_NAME)
    return template.render(**context)


def send_email(subject, html_body):
    mail_from = os.environ.get("MAIL_FROM")
    mail_password = os.environ.get("MAIL_APP_PASSWORD")
    mail_to_raw = os.environ.get("MAIL_TO")

    if not (mail_from and mail_password and mail_to_raw):
        logger.warning("mail credentials not set (MAIL_FROM/MAIL_APP_PASSWORD/MAIL_TO) — skipping email")
        return

    # MAIL_TO may be a single address or a comma-separated list.
    mail_to = [addr.strip() for addr in mail_to_raw.split(",") if addr.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(mail_to)
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(mail_from, mail_password)
            server.sendmail(mail_from, mail_to, msg.as_string())
        logger.info("email sent to %s", ", ".join(mail_to))
    except Exception:
        logger.exception("failed to send email")


def main():
    try:
        config = load_config()
    except Exception:
        logger.exception("fatal: could not load config.yaml")
        sys.exit(1)

    context = build_context(config)

    try:
        html = render(context)
    except Exception:
        logger.exception("fatal: template render failed")
        sys.exit(1)

    with open(OUTPUT_PATH, "w") as f:
        f.write(html)
    logger.info("wrote %s", OUTPUT_PATH)

    subject = f"{config['email']['subject_prefix']} — {dt.datetime.now().strftime('%a, %b %-d')}"
    send_email(subject, html)


if __name__ == "__main__":
    main()
