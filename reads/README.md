# My Reads / Watch — runbook

Live: https://sells8008.github.io/daily-dispatch/reads/
Backup: OneDrive `ORGANIZED/000_AI_Projects_Skills/Reads-Watch/Hunter_Tracker_Reads-Watch.xlsx`

Item fields (reads/data.json, array):
`id` (e.g. rw-YYYYMMDD-NN, unique, never reused), `url`, `title`, `source` (site/publication/channel),
`type` (article|newsletter|video|podcast|other), `note` (one line), `added` (YYYY-MM-DD).

## Add an item (Hunter sends a link in chat)
1. Fetch the link (WebFetch / curl) → get page title and source (og:site_name, domain, or channel). Pick type.
2. Write a one-line note on why it's worth revisiting.
3. Append object to `/workspace/dd-pub/reads/data.json` (keep valid JSON; `python3 -m json.tool` to check).
4. `cd /workspace/dd-pub && git add reads/data.json && git commit -m "Reads: add <title>" && git push`
5. Verify: `curl -s https://sells8008.github.io/daily-dispatch/reads/data.json` shows the item (1–2 min).
6. Backup: download the xlsx (DownloadFile, user-Onedrive), append a row with the same columns
   (id, url, title, source, type, note, added) via openpyxl, upload back with overwrite of THAT file only.

Done/Favorites/view state is per-browser localStorage (rw-done-v1, rw-fav-v1, rw-view-v1); not in data.json.

Deleted state: localStorage rw-del-v1 (Deleted tab restores). Items are only hidden per-browser; never remove rows from data.json/xlsx for a delete. Keep ids stable.

## Cross-device sync (Done / Favorites / Deleted)
Marks sync across all devices via Cloudflare Worker `dd-sync` (https://dd-sync.sells8008.workers.dev, Hunter's free Cloudflare account, KV namespace DD_STATE). Shared client: `/sync.js` (loaded by reads/, x-bookmarks/, summaries/; page ids rw / xbm / cs). localStorage stays the working copy: clicks save locally, queue, and push; on open the page pulls the server copy. First load on each device merges its existing local marks in. Offline = local only, pushes when back online. Light shared key (X-Key) is in sync.js — it only gates mark flags, not content.
Worker source + wrangler.toml: /workspace/dd-sync (deploy: PATH=~/.local/node22/bin:~/.local/bin:$PATH; cd /workspace/dd-sync && wrangler deploy). E2E test: node e2e.js. Keep item ids stable; never change localStorage key names.
