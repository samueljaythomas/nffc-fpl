#!/usr/bin/env python3
"""
Builds index.html - a standalone scoreboard page for a Draft FPL league.
Run by GitHub Actions on a schedule; the output is served by GitHub Pages.
"""

import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

LEAGUE_ID = int(os.environ.get("LEAGUE_ID", "6206"))
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Chicago"))

SEGMENTS = [
    ("Segment 1", 1, 10),
    ("Segment 2", 11, 20),
    ("Segment 3", 21, 30),
    ("Segment 4", 31, 38),
]

BASE = "https://draft.premierleague.com/api"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; fpl-scoreboard/1.0)",
    "Accept": "application/json",
})


def get(path):
    r = session.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


details = get(f"/league/{LEAGUE_ID}/details")
league_name = details["league"]["name"]
names = {e["id"]: e["entry_name"] for e in details["league_entries"]}
managers = {e["id"]: f"{e['player_first_name']} {e['player_last_name']}"
            for e in details["league_entries"]}

weekly = defaultdict(dict)
finished = set()
for m in details["matches"]:
    if not m.get("started"):
        continue
    gw = m["event"]
    weekly[gw][m["league_entry_1"]] = m["league_entry_1_points"]
    weekly[gw][m["league_entry_2"]] = m["league_entry_2_points"]
    if m.get("finished"):
        finished.add(gw)

played = sorted(weekly)
latest = max(played) if played else 0


def totals_for(start, end):
    gws = [gw for gw in played if start <= gw <= end]
    totals = defaultdict(int)
    for gw in gws:
        for entry, pts in weekly[gw].items():
            totals[entry] += pts
    return totals, gws


def ranked(totals):
    ordered = sorted(totals.items(), key=lambda kv: -kv[1])
    out, last_pts, last_rank = [], None, 0
    for i, (entry, pts) in enumerate(ordered, start=1):
        rank = last_rank if pts == last_pts else i
        out.append((rank, entry, pts))
        last_pts, last_rank = pts, rank
    return out


def rows_html(table, gw_count, value_sub=None):
    out = []
    for rank, entry, pts in table:
        lead = " lead" if rank == 1 else ""
        sub = value_sub or (f"{pts / gw_count:.1f}/gw" if gw_count else "")
        out.append(
            f'<div class="row{lead}">'
            f'<div class="rank">{rank}</div>'
            f'<div class="who"><div class="team">{names[entry]}</div>'
            f'<div class="mgr">{managers[entry]}</div></div>'
            f'<div class="val"><div class="pts">{pts}</div>'
            f'<div class="sub">{sub}</div></div>'
            f'</div>'
        )
    return "".join(out)


def segment_block(label, start, end, is_current):
    totals, gws = totals_for(start, end)
    span = end - start + 1

    if not gws:
        return (f'<section class="card">'
                f'<div class="head"><span class="badge muted">UPCOMING</span>'
                f'<h2>{label}</h2><p>GW{start}&ndash;{end}</p></div></section>')

    complete = len(gws) == span
    badge = "FINAL" if complete else f"{len(gws)}/{span}"
    badge_cls = "done" if complete else "live"
    table = ranked(totals)

    note = ""
    pending = [g for g in gws if g not in finished]
    if pending:
        note = ('<div class="note warn">GW'
                + ", GW".join(str(g) for g in pending)
                + " not finalised &mdash; scores may still change</div>")
    elif complete:
        winners = " &amp; ".join(names[e] for r, e, p in table if r == 1)
        note = f'<div class="note">&#127942; Award: <b>{winners}</b></div>'

    cur = " current" if is_current else ""
    return (f'<section class="card{cur}">'
            f'<div class="head"><span class="badge {badge_cls}">{badge}</span>'
            f'<h2>{label}</h2><p>GW{start}&ndash;{end}</p></div>'
            f'{rows_html(table, len(gws))}{note}</section>')


blocks = []
current = next((s for s in SEGMENTS if s[1] <= latest <= s[2]), None)
for seg in SEGMENTS:
    blocks.append(segment_block(seg[0], seg[1], seg[2], seg is current))

# segment titles
wins = defaultdict(int)
for label, start, end in SEGMENTS:
    totals, gws = totals_for(start, end)
    if gws and len(gws) == (end - start + 1):
        for r, e, p in ranked(totals):
            if r == 1:
                wins[e] += 1
if wins:
    table = [(i, e, n) for i, (e, n) in
             enumerate(sorted(wins.items(), key=lambda kv: -kv[1]), start=1)]
    blocks.append(f'<section class="card"><div class="head">'
                  f'<h2>Segment titles</h2><p>Completed blocks only</p></div>'
                  f'{rows_html(table, 0, value_sub="titles")}</section>')

# season total
if played:
    season, gws = totals_for(1, 38)
    blocks.append(f'<section class="card"><div class="head">'
                  f'<span class="badge muted">ALL</span>'
                  f'<h2>Season total</h2><p>Through GW{latest}</p></div>'
                  f'{rows_html(ranked(season), len(gws))}</section>')

updated = datetime.now(TZ).strftime("%a %d %b, %I:%M %p").replace(" 0", " ")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{league_name} &middot; Scoreboard</title>
<meta name="theme-color" content="#12161f">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 20px 14px 44px;
    background: #0c0f16; color: #f2f5fa;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 460px; margin: 0 auto; }}
  .title {{ font-size: 25px; font-weight: 800; letter-spacing: -.02em; }}
  .stamp {{ font-size: 12px; color: #8c97ad; margin: 6px 0 22px; }}
  .card {{
    background: #12161f; border: 1px solid #2b3446;
    border-radius: 15px; overflow: hidden; margin-bottom: 15px;
  }}
  .card.current {{ border-color: #37d67a; }}
  .head {{ padding: 15px 17px 12px; border-bottom: 1px solid #2b3446; }}
  .head h2 {{ margin: 0; font-size: 18px; font-weight: 700; }}
  .head p {{ margin: 3px 0 0; font-size: 12px; color: #8c97ad; }}
  .badge {{
    float: right; font-size: 10px; font-weight: 700; letter-spacing: .06em;
    padding: 4px 9px; border-radius: 20px; color: #12161f;
  }}
  .badge.done {{ background: #37d67a; }}
  .badge.live {{ background: #f5c451; }}
  .badge.muted {{ background: #3d4759; color: #c9d2e2; }}
  .row {{
    display: flex; align-items: center; gap: 12px;
    padding: 12px 16px 12px 13px; border-bottom: 1px solid #212939;
    border-left: 3px solid transparent;
  }}
  .row:last-of-type {{ border-bottom: none; }}
  .row.lead {{ border-left-color: #f5c451; background: #151c28; }}
  .rank {{ width: 20px; font-size: 15px; font-weight: 700; color: #8c97ad; }}
  .row.lead .rank {{ color: #f5c451; }}
  .who {{ flex: 1; min-width: 0; }}
  .team {{
    font-size: 14.5px; font-weight: 600;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .mgr {{ font-size: 11px; color: #8c97ad; margin-top: 1px; }}
  .val {{ text-align: right; }}
  .pts {{ font-size: 19px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .sub {{ font-size: 10px; color: #8c97ad; }}
  .note {{ padding: 11px 17px; font-size: 12px; color: #8c97ad; }}
  .note b {{ color: #f2f5fa; }}
  .note.warn {{ color: #f5c451; }}
  footer {{ text-align: center; font-size: 11px; color: #55607a; margin-top: 26px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="title">{league_name}</div>
  <div class="stamp">Consistency tracker &middot; updated {updated}</div>
  {''.join(blocks)}
  <footer>Auto-updates daily &middot; data from the official FPL Draft API</footer>
</div>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Built index.html for {league_name} through GW{latest}")
