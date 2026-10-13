# My Reads / Watch — runbook

Live: https://sells8008.github.io/daily-dispatch/reads/
Backup: OneDrive `ORGANIZED/06_Pursuits/(AI) Projects/Reads-Watch/Hunter_Tracker_Reads-Watch.xlsx`

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
