# The Morning Dispatch

A daily personal newspaper: fetches live weather, news, sports, events,
markets and a rotating recipe, renders them into `index.html`, emails it,
and publishes it to GitHub Pages.

- **Live page:** https://sells8008.github.io/daily-dispatch/
- **Everything tunable** (teams, topics, artists, venues, recipes, paywalled
  sources, freshness windows) lives in `config.yaml`. Edit, commit, push —
  the next run picks it up. No code changes needed.

## How it runs

`.github/workflows/daily.yml` builds the edition, emails it, and commits the
regenerated `index.html` back to the repo.

It is triggered three ways:

1. **An external scheduler** calling the GitHub API each morning — this is
   the primary trigger (see below).
2. **GitHub's own `schedule:` cron**, as a backup.
3. **Manually**, from the repo's Actions tab.

### Why an external scheduler?

GitHub's `schedule:` is explicitly best-effort. In practice it was both
badly delayed (every run between 2026-08-19 and 08-26 fired 76–112 minutes
late) and then stopped firing altogether from 2026-08-27 — no runs at all,
across three separate cron slots, with the workflow still `active`, valid
YAML, and no GitHub incident. The cron entries are kept as a safety net, but
they are not depended on.

### Duplicate protection

`.last-edition` holds the date of the last published edition. Every trigger
checks it first and no-ops if today's edition already went out, so the
external ping, the backup crons, and a manual click can all fire on the same
day without ever sending two emails.

To deliberately rebuild and resend, run it manually from the Actions tab
with **force** checked.

## Setting up the external trigger

Any scheduler that can make an HTTP POST works (cron-job.org, EasyCron,
Zapier, an always-on machine). Schedule it in **America/New_York** so it
tracks DST automatically.

**Request:**

```
POST https://api.github.com/repos/sells8008/daily-dispatch/actions/workflows/daily.yml/dispatches
```

**Headers:**

```
Accept: application/vnd.github+json
Authorization: Bearer YOUR_TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

**Body:**

```json
{"ref":"main"}
```

A successful trigger returns **204 No Content** (empty response). Sending no
`inputs` leaves `force` unset, so the duplicate guard stays active.

**Token:** a fine-grained personal access token
(github.com/settings/personal-access-tokens), scoped to *only* the
`daily-dispatch` repository, with **Actions: Read and write**. That is the
least privilege that can start a workflow. Set an expiry you're willing to
renew.

## Secrets

Set in the repo under Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `MAIL_FROM` | sending Gmail address |
| `MAIL_APP_PASSWORD` | Gmail app password (not the account password) |
| `MAIL_TO` | recipient(s), comma-separated |
| `TICKETMASTER_API_KEY` | Ticketmaster Discovery consumer key |

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
TZ=America/New_York .venv/bin/python build.py
```

Without the mail environment variables it builds `index.html` and skips the
email, which is the usual way to preview changes.

## Data sources

| Section | Source | Key |
|---|---|---|
| Weather / Forecast | Open-Meteo | no |
| News topics, Top Stories | Google News RSS | no |
| Scores, schedules, golf | ESPN site API (unofficial) | no |
| JU lacrosse fallback | judolphins.com schedule (JSON-LD) | no |
| Around Town events | Ticketmaster Discovery | yes |
| SeaWalk Pavilion events | City of Jacksonville Beach iCal | no |
| BTC | CoinGecko | no |
| S&P 500 | Yahoo Finance (unofficial) | no |
| Today's Cook | `recipe_rotation` in config | n/a |

Every source is fail-soft: if one breaks, that section degrades and the rest
of the edition still builds and sends.

### Known rough edges

- **S&P 500** frequently returns HTTP 429. Stooq (the originally specified
  source) is retired; Yahoo's endpoint rate-limits by IP. The row hides
  itself when the quote fails.
- **ESPN's API is undocumented** and can change shape without notice.
- **SeaWalk Pavilion** events come from the city's community calendar;
  promoter-run concerts at that venue may not appear there.
- **DST:** the backup crons are UTC and drift by an hour twice a year. The
  external trigger, scheduled in Eastern time, does not.
