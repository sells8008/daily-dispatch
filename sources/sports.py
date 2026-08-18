"""Sports source — ESPN's unofficial hidden scoreboard JSON, no API key required.

This endpoint is undocumented and can change shape without notice, so every
call here is wrapped defensively: a broken/unexpected response degrades that
one team's line instead of killing the section.
"""

import datetime as dt
import json
import logging

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 fallback
    from backports.zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SITE_API = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_WEB = "https://www.espn.com"

# ESPN's college-lacrosse coverage of JU is inconsistent (per the build spec),
# so "today"/"next game" for this team falls back to JU's own athletics site,
# which embeds a clean schema.org SportsEvent JSON-LD block per game. The
# bare (year-less) schedule URL always resolves to the current season.
JU_LACROSSE_SCHEDULE_URL = "https://judolphins.com/sports/mens-lacrosse/schedule"

LEAGUE_PATHS = {
    "mlb": "baseball/mlb",
    "nfl": "football/nfl",
    "nhl": "hockey/nhl",
    "mens-college-lacrosse": "lacrosse/mens-college-lacrosse",
}

LEAGUE_LABELS = {
    "mlb": "MLB",
    "nfl": "NFL",
    "nhl": "NHL",
    "mens-college-lacrosse": "NCAA Lacrosse",
}

def _get(url, params=None, timeout=10):
    # ESPN's edge (Akamai) blocks browser-like User-Agents on this hidden
    # endpoint but allows the plain default `requests` UA — do not set one.
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception("sports fetch failed: %s", url)
        return None


def _scoreboard(league, date_str=None):
    path = LEAGUE_PATHS.get(league)
    if not path:
        return []
    data = _get(f"{SITE_API}/{path}/scoreboard", params={"dates": date_str} if date_str else None)
    if not data:
        return []
    return data.get("events", [])


def _find_team_competitor(event, team_name):
    """Return (this_team_competitor, opponent_competitor) or None."""
    team_name_lower = team_name.lower()
    for comp in event.get("competitions", []):
        competitors = comp.get("competitors", [])
        this_team = None
        opponent = None
        for c in competitors:
            display = c.get("team", {}).get("displayName", "")
            if display.lower() == team_name_lower or team_name_lower in display.lower():
                this_team = c
            else:
                opponent = c
        if this_team and opponent:
            return this_team, opponent, comp
    return None


def _game_url(league, event_id):
    path = LEAGUE_PATHS.get(league, "")
    slug = path.split("/")[-1] if path else ""
    return f"{ESPN_WEB}/{slug}/game/_/gameId/{event_id}"


def _to_et(iso_date_str, tz_name="America/New_York"):
    parsed = dateparser.parse(iso_date_str)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(ZoneInfo(tz_name))


def _team_short_name(team_dict, fallback):
    # The scoreboard endpoint uses "name"; the team-schedule endpoint uses
    # "nickname" for the same thing (e.g. "Eagles"). Try both.
    return team_dict.get("name") or team_dict.get("nickname") or team_dict.get("shortDisplayName") or fallback


def _broadcast_names(competition):
    names = []
    for b in competition.get("broadcasts", []):
        names.extend(b.get("names", []))
    return names


def _local_date_str(tz_name, offset_days=0):
    now = dt.datetime.now(ZoneInfo(tz_name)) + dt.timedelta(days=offset_days)
    return now.strftime("%Y%m%d")


def yesterday_results(teams, tz_name="America/New_York"):
    """Final scores + W/L for each configured team, for "yesterday" in the
    user's local timezone."""
    date_str = _local_date_str(tz_name, offset_days=-1)
    results = []
    for team in teams:
        league = team.get("league")
        name = team.get("name")
        if league not in LEAGUE_PATHS:
            continue
        try:
            events = _scoreboard(league, date_str)
            for event in events:
                match = _find_team_competitor(event, name)
                if not match:
                    continue
                this_team, opponent, comp = match
                status = comp.get("status", {}).get("type", {})
                if not status.get("completed"):
                    continue
                team_short = _team_short_name(this_team.get("team", {}), name)
                opp_short = _team_short_name(opponent.get("team", {}), "Opponent")
                results.append(
                    {
                        "league": f"{LEAGUE_LABELS.get(league, league.upper())} · {team_short}",
                        "match": f"{team_short} {this_team.get('score', '?')}, {opp_short} {opponent.get('score', '?')}",
                        "won": bool(this_team.get("winner")),
                        "url": _game_url(league, event.get("id")),
                    }
                )
                break
        except Exception:
            logger.exception("yesterday_results failed for %s", name)
    return results


def _next_scheduled_game(league, team_name, tz_name):
    """Best-effort lookahead to the next upcoming game beyond today, via the
    ESPN team schedule endpoint. Returns a note dict or None."""
    path = LEAGUE_PATHS.get(league)
    if not path:
        return None
    try:
        teams_data = _get(f"{SITE_API}/{path}/teams")
        if not teams_data:
            return None
        team_id = None
        for t in teams_data["sports"][0]["leagues"][0]["teams"]:
            if t["team"]["displayName"].lower() == team_name.lower():
                team_id = t["team"]["id"]
                break
        if not team_id:
            return None

        schedule = _get(f"{SITE_API}/{path}/teams/{team_id}/schedule")
        if not schedule:
            return None

        now = dt.datetime.now(dt.timezone.utc)
        upcoming = []
        for event in schedule.get("events", []):
            event_dt = dateparser.parse(event["date"])
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=dt.timezone.utc)
            if event_dt > now:
                upcoming.append((event_dt, event))
        if not upcoming:
            return None
        upcoming.sort(key=lambda pair: pair[0])
        event_dt, event = upcoming[0]

        match = _find_team_competitor(event, team_name)
        if not match:
            return None
        this_team, opponent, comp = match
        opp_short = _team_short_name(opponent.get("team", {}), "Opponent")
        is_home = this_team.get("homeAway") == "home"
        verb = "vs." if is_home else "@"
        et = event_dt.astimezone(ZoneInfo(tz_name))
        names = _broadcast_names(comp)
        channel = f" ({names[0]})" if names else ""
        return {
            "team": _team_short_name(this_team.get("team", {}), team_name),
            "text": f"next: {et.strftime('%a %b %-d')}, {et.strftime('%-I:%M %p')} ET {verb} {opp_short}{channel}",
        }
    except Exception:
        logger.exception("next_scheduled_game failed for %s", team_name)
        return None


def _ju_lacrosse_schedule():
    """Parse the schema.org SportsEvent JSON-LD block JU's athletics site
    embeds on its schedule page. Returns a list of event dicts, or []."""
    try:
        resp = requests.get(
            JU_LACROSSE_SCHEDULE_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; DailyDispatch/1.0)"},
            timeout=10,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", type="application/ld+json")
        if not script or not script.string:
            return []
        data = json.loads(script.string)
        return data if isinstance(data, list) else [data]
    except Exception:
        logger.exception("JU lacrosse schedule fetch failed")
        return []


def _ju_lacrosse_next_game(tz_name):
    """Next upcoming JU men's lacrosse game from judolphins.com. The site's
    JSON-LD dates have no timezone — they're the school's own local (ET)
    listing times. Returns a note dict, or None if nothing upcoming (i.e.
    genuinely off-season, or next season not posted yet)."""
    events = _ju_lacrosse_schedule()
    if not events:
        return None

    tz = ZoneInfo(tz_name)
    now_local = dt.datetime.now(tz)
    upcoming = []
    for ev in events:
        start_str = ev.get("startDate")
        if not start_str:
            continue
        try:
            start_local = dateparser.parse(start_str).replace(tzinfo=tz)
        except (ValueError, OverflowError):
            continue
        if start_local > now_local:
            upcoming.append((start_local, ev))
    if not upcoming:
        return None

    upcoming.sort(key=lambda pair: pair[0])
    start_local, ev = upcoming[0]
    name = (ev.get("name") or "").strip()
    is_home = name.lower().startswith("vs")
    opponent = ev.get("awayTeam", {}).get("name") if is_home else ev.get("homeTeam", {}).get("name")
    opponent = opponent or name[2:].strip() or "TBA"
    verb = "vs." if is_home else "@"
    return {
        "team": "Dolphins",
        "text": f"next: {start_local.strftime('%a %b %-d')}, {start_local.strftime('%-I:%M %p')} ET {verb} {opponent}",
    }


def teams_today(teams, tz_name="America/New_York"):
    """Returns (games, notes) for the "Your Teams — Today" section.

    games: list of dicts for teams with a game today (in progress or upcoming).
    notes: one-line notes for teams with no game today (next game, or a
    quiet off-season line for JU lacrosse).
    """
    date_str = _local_date_str(tz_name)
    games = []
    notes = []

    for team in teams:
        league = team.get("league")
        name = team.get("name")
        if league not in LEAGUE_PATHS:
            continue
        try:
            events = _scoreboard(league, date_str)
            found = None
            for event in events:
                match = _find_team_competitor(event, name)
                if not match:
                    continue
                this_team, opponent, comp = match
                status = comp.get("status", {}).get("type", {})
                if status.get("completed"):
                    continue
                found = (this_team, opponent, comp, event)
                break

            if found:
                this_team, opponent, comp, event = found
                team_short = _team_short_name(this_team.get("team", {}), name)
                opp_short = _team_short_name(opponent.get("team", {}), "Opponent")
                is_home = this_team.get("homeAway") == "home"
                verb = "vs." if is_home else "@"
                et = _to_et(comp.get("date", event.get("date")), tz_name)
                names = _broadcast_names(comp)
                channel = " · ".join(names) if names else "Check local listings"
                games.append(
                    {
                        "league": f"{LEAGUE_LABELS.get(league, league.upper())} · {team_short}",
                        "match": f"{team_short} {verb} {opp_short}",
                        "time": f"{et.strftime('%-I:%M %p')} ET",
                        "channel": channel,
                        "url": _game_url(league, event.get("id")),
                    }
                )
                continue

            # No game today for this team.
            if league == "mens-college-lacrosse":
                # ESPN's college-lacrosse coverage of JU is inconsistent —
                # try it first, then fall back to JU's own athletics site,
                # then a quiet "nothing posted" line rather than an error.
                note = _next_scheduled_game(league, name, tz_name) or _ju_lacrosse_next_game(tz_name)
                notes.append(note or {"team": name.split()[-1], "text": "off-season — no upcoming games posted yet"})
                continue

            note = _next_scheduled_game(league, name, tz_name)
            if note:
                notes.append(note)
        except Exception:
            logger.exception("teams_today failed for %s", name)

    return games, notes


def golf(follow=None):
    """Tournament name, round, and leader for the currently tracked PGA event.

    ESPN's scoreboard gives current tournament state, not a per-day history,
    so this classifies the *whole event* as belonging to "yesterday" (state
    post/completed) or "today" (state in-progress), and is silent in off
    weeks (state pre / no event).
    """
    try:
        data = _get(f"{SITE_API}/golf/pga/scoreboard")
        if not data:
            return None
        events = data.get("events", [])
        if not events:
            return None
        event = events[0]
        comp = event["competitions"][0]
        status = comp.get("status", {}).get("type", {})
        state = status.get("state")
        period = comp.get("status", {}).get("period")
        competitors = sorted(comp.get("competitors", []), key=lambda c: c.get("order", 999))
        leader = competitors[0] if competitors else None
        leader_name = leader.get("athlete", {}).get("displayName") if leader else None
        leader_score = leader.get("score") if leader else None
        names = _broadcast_names(comp)
        return {
            "name": event.get("name"),
            "state": state,
            "round_label": "Final" if state == "post" else (f"Rd {period}" if period else "In progress"),
            "leader_name": leader_name,
            "leader_score": leader_score,
            "channel": " · ".join(names) if names else "Check listings",
            "url": f"{ESPN_WEB}/golf/leaderboard",
        }
    except Exception:
        logger.exception("golf fetch failed")
        return None
