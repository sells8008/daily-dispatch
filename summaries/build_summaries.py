#!/usr/bin/env python3
"""Rebuild summaries/data.json from /workspace/creator-monitoring/posts/*.md.
Usage: python3 summaries/build_summaries.py  (from /workspace/dd-pub), then git add/commit/push."""
import glob, json, os, re, sys
sys.path.insert(0, os.path.expanduser('~/.local/lib/python3/site-packages'))
import markdown
POSTS = '/workspace/creator-monitoring/posts'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')
DISC = 'Educational analysis, not professional financial advice.'
DROP = re.compile(r'^\s*[-*]?\s*\**(status|ops|filed|state)\b', re.I)
items = []
for f in sorted(glob.glob(POSTS + '/*.md')):
    name = os.path.basename(f)[:-3]
    m = re.match(r'(\d{4}-\d{2}-\d{2})-(ai-investor|sightbringer)-(.*)', name)
    if not m: continue
    date, cr, slug = m.groups()
    creator = 'AI Investor' if cr == 'ai-investor' else 'SightBringer'
    if slug.startswith('x-'): typ = 'X posts'
    elif slug.startswith('quip'): typ = 'Quip'
    else: typ = 'Full post'
    text = open(f, encoding='utf-8').read()
    lines = text.splitlines()
    title = name
    if lines and lines[0].startswith('#'):
        title = lines[0].lstrip('# ').strip(); lines = lines[1:]
    lines = [l for l in lines if not DROP.match(l)]
    url = ''
    um = re.search(r'https?://[^\s)>\]]+', text)
    if um: url = um.group(0)
    body = '\n'.join(lines).strip()
    if DISC.lower() not in body.lower():
        body += '\n\n*' + DISC + '*'
    html = markdown.markdown(body, extensions=['extra', 'sane_lists'])
    html = re.sub(r'<a href=', '<a target="_blank" rel="noopener" href=', html)
    plain = re.sub(r'<[^>]+>', ' ', html)
    items.append(dict(id=name, date=date, creator=creator, type=typ, title=title,
                      url=url, html=html, text=re.sub(r'\s+', ' ', plain)[:4000]))
items.sort(key=lambda x: (x['date'], x['type'] == 'Full post'), reverse=True)
json.dump(items, open(OUT, 'w'), ensure_ascii=False, indent=1)
print(len(items), 'summaries ->', OUT)
